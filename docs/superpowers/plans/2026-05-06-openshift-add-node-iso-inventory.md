# OpenShift add-node ISO inventory wiring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `igou-inventory` so `playbooks/openshift/add_node_iso.yml` can run against the `ocp` cluster with `hpg5.igou.systems` as the first add-node worker.

**Architecture:** All changes land in the `igou-inventory` repo (separately versioned, symlinked into `igou-ansible/`). Two files change: `inventory.yaml` gains a new `openshift_workers_ocp` group with `hpg5.igou.systems` and its boot MAC inline; `host_vars/ocp.yml` gains the two add-node cluster vars (`openshift_add_node_arch`, `openshift_add_node_boot_artifacts_base_url`). DHCP-only — no nmstate `networkConfig` — so the `--tags monitor` step is unavailable for this run.

**Tech Stack:** Ansible inventory YAML, `ansible-inventory` CLI, yamllint.

**Reference docs:**
- Spec: `igou-ansible/docs/superpowers/specs/2026-05-06-openshift-add-node-iso-netboot-design.md`
- Playbook plan: `igou-ansible/docs/superpowers/plans/2026-05-06-openshift-add-node-iso-netboot.md`
- Playbook (already on main): `igou-ansible/playbooks/openshift/add_node_iso.yml`

**Important caveat — physical host conflict:**
`hpg5.igou.systems` is currently the running k3s control-plane node (`inventory.yaml` line 23, `kubernetes_role: control_plane`). PXE-booting it for OpenShift wipes the disk. Coordinate the k3s teardown / workload migration **before** booting this worker. The inventory changes themselves are safe — they only declare group membership and add-node metadata; nothing destructive happens until the operator PXE-boots in Task 4.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `igou-inventory/inventory.yaml` | Modify | Add `openshift_workers_ocp` group containing `hpg5.igou.systems` with `openshift_add_node_mac` inline |
| `igou-inventory/host_vars/ocp.yml` | Modify | Add `openshift_add_node_arch` and `openshift_add_node_boot_artifacts_base_url` |

No new `host_vars/hpg5.igou.systems.yml` — DHCP-only worker config is a single key (the MAC), and the existing repo pattern puts small per-host vars inline in `inventory.yaml` (see the `orange_pi_5_pro` block, lines 85–104).

---

## Task 1: Add openshift_workers_ocp group to inventory.yaml

**Files:**
- Modify: `igou-inventory/inventory.yaml`

The `openshift_workers_ocp` group is a flat group-of-hosts. `hpg5.igou.systems` is already declared under `k8s_internal_nodes` (line 23) — referencing the same host name in another group adds it to that group too; group memberships union across declarations. The `openshift_add_node_mac` var is declared inline on this group entry; it becomes host-scoped via group membership.

- [ ] **Step 1: Insert the new group after `openshift_clusters`**

Open `igou-inventory/inventory.yaml`. Locate the `openshift_clusters` block (lines 29–31):

```yaml
    openshift_clusters:
      hosts:
        ocp:
```

Immediately after that block (so before the `aap:` block on line 32), insert:

```yaml
    openshift_workers_ocp:
      hosts:
        hpg5.igou.systems:
          openshift_add_node_mac: "F8:B4:6A:AB:55:C7"
```

The host `hpg5.igou.systems` already exists in `k8s_internal_nodes`; this entry only adds it to `openshift_workers_ocp` and attaches the MAC. No other vars are needed because the worker is DHCP-only.

- [ ] **Step 2: Verify inventory parses**

```bash
cd igou-inventory
ansible-inventory -i inventory.yaml --list > /tmp/inv.json
```

Expected: clean exit, no errors. `/tmp/inv.json` contains the parsed inventory.

- [ ] **Step 3: Verify hpg5 lands in the new group with the MAC**

```bash
ansible-inventory -i inventory.yaml --host hpg5.igou.systems \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("openshift_add_node_mac") == "F8:B4:6A:AB:55:C7", d; print("OK")'
ansible-inventory -i inventory.yaml --graph openshift_workers_ocp
```

Expected: first command prints `OK`. Second prints a graph showing `hpg5.igou.systems` under `openshift_workers_ocp`.

- [ ] **Step 4: Verify hpg5 still belongs to k8s_internal_nodes**

```bash
ansible-inventory -i inventory.yaml --graph k8s_internal_nodes
```

Expected: the graph still lists `hpg5.igou.systems` under `k8s_internal_nodes` with `kubernetes_role: control_plane` intact. Group membership is additive, not replaced.

- [ ] **Step 5: yamllint**

```bash
yamllint inventory.yaml
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add inventory.yaml
git commit -m "Add openshift_workers_ocp group with hpg5.igou.systems"
```

---

## Task 2: Add add-node cluster vars to host_vars/ocp.yml

**Files:**
- Modify: `igou-inventory/host_vars/ocp.yml`

The playbook reads two add-node cluster vars from the cluster host (`ocp`):
- `openshift_add_node_arch` — defaults to `x86_64` inside the playbook, but pinning it in inventory documents the cluster's actual arch and protects against future default changes.
- `openshift_add_node_boot_artifacts_base_url` — required; no default. Must point at the netbootxyz HTTP path the playbook populates.

The existing `openshift_agent_install_agent_config.bootArtifactsBaseURL` (`host_vars/ocp.yml` line 45) is `http://10.10.45.242/ocp/`. The add-node URL parallels that under the `<cluster>-add-node` subdir: `http://10.10.45.242/ocp-add-node/`.

- [ ] **Step 1: Append the two vars near the top of host_vars/ocp.yml**

Open `igou-inventory/host_vars/ocp.yml`. Replace the first three lines:

```yaml
---
cluster_name: ocp
ansible_connection: local
```

with:

```yaml
---
cluster_name: ocp
ansible_connection: local

# Vars for playbooks/openshift/add_node_iso.yml. The boot artifacts base URL
# must match the per-cluster subdir the playbook populates on netbootxyz.
openshift_add_node_arch: x86_64
openshift_add_node_boot_artifacts_base_url: http://10.10.45.242/ocp-add-node/
```

Keep the rest of the file unchanged.

- [ ] **Step 2: Verify both vars resolve on the ocp host**

```bash
ansible-inventory -i inventory.yaml --host ocp \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["openshift_add_node_arch"] == "x86_64", d; assert d["openshift_add_node_boot_artifacts_base_url"] == "http://10.10.45.242/ocp-add-node/", d; print("OK")'
```

Expected: prints `OK`.

- [ ] **Step 3: yamllint**

```bash
yamllint host_vars/ocp.yml
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add host_vars/ocp.yml
git commit -m "Add OpenShift add-node cluster vars for ocp"
```

---

## Task 3: Render-check the playbook against the wired inventory

**Files:** none modified. Validates the inventory wiring against the live playbook in `igou-ansible/`.

`ansible-playbook --check --diff` exercises the preflight assertions and the `nodes-config.yaml` template render without making real cluster API calls. The playbook asserts the `KUBECONFIG` env var is set; satisfy it with a sentinel path so the assertion passes for dry-run.

- [ ] **Step 1: From igou-ansible, run a check-mode render**

```bash
cd igou-ansible
KUBECONFIG=/dev/null ansible-playbook \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  --skip-tags monitor \
  playbooks/openshift/add_node_iso.yml \
  --check --diff
```

Expected:
- Preflight assertions pass: `KUBECONFIG` present; group `openshift_workers_ocp` non-empty; MAC `F8:B4:6A:AB:55:C7` matches the regex.
- `--diff` shows the rendered `nodes-config.yaml`. It must contain:
  - `hosts:` with one entry, `hostname: hpg5.igou.systems`
  - `interfaces:` with `name: eth0` and `macAddress: "F8:B4:6A:AB:55:C7"`
  - no `rootDeviceHints` and no `networkConfig`
  - `bootArtifactsBaseURL: http://10.10.45.242/ocp-add-node/`
- The `Wipe stale work dir`, pull-secret fetch, and `oc adm node-image create --pxe` tasks may be skipped or fail under `--check` since they touch the filesystem and run external commands; that's expected. The goal of this task is preflight + template render only.

- [ ] **Step 2: If the render is wrong, fix the inventory and re-run**

Common things to check:
- MAC missing → ensure `openshift_add_node_mac` is on the host inside `openshift_workers_ocp`, not buried under another group.
- `bootArtifactsBaseURL` wrong or missing → confirm `host_vars/ocp.yml` was saved with the new var.
- Hostname not `hpg5.igou.systems` → the template falls back to `inventory_hostname` when `openshift_add_node_hostname` is unset; the host key in `inventory.yaml` must be the FQDN (it already is).

No commit for this task — it's read-only validation.

---

## Task 4: Manual end-to-end checklist (no automation)

A checklist, not a code change. Mirrors Task 9 of the playbook plan, narrowed to this specific worker.

**Pre-conditions:**
- Tasks 1–3 complete and committed to `igou-inventory`.
- `hpg5.igou.systems` has been drained and removed from the running k3s cluster (or the operator has accepted losing the k3s control plane). PXE-booting it wipes the disk.
- TrueNAS netbootxyz is reachable at `http://10.10.45.242/` and has a `/config/menus/local/` dir (per `playbooks/openshift/agent-install/deploy_pxe_assets.yml` precedent).
- A `KUBECONFIG` with `cluster-admin` (or at minimum `get` on `secrets` in `openshift-config`) exists for the `ocp` cluster.

- [ ] **Step 1: Drain and remove hpg5 from k3s** (operational step; not in scope of this plan)

- [ ] **Step 2: Real run from igou-ansible**

```bash
cd igou-ansible
export KUBECONFIG=<path-to-ocp-kubeconfig>
ansible-navigator run playbooks/openshift/add_node_iso.yml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp
```

Expected:
- Play 1 generates PXE assets in `~/openshift-add-node/ocp/`.
- Play 2 syncs them to `/mnt/ssd/containers/netbootxyz/assets/ocp-add-node/` on TrueNAS and writes `f8b46aab55c7-add-node-ocp.ipxe` (MAC lowercased, colons stripped) into both `/config/menus/` and `/config/menus/local/`.

- [ ] **Step 3: Verify PXE asset filenames match assumption**

The playbook task `Show generated PXE asset filenames` prints the actual files. Compare against the assumed names: `node.x86_64.ipxe`, `node.x86_64-vmlinuz`, `node.x86_64-initrd.img`, `node.x86_64-rootfs.img`. If they differ, follow the playbook plan Task 9 Step 4 for fix-up instructions (update the stat loop in the playbook + the iPXE source path in Play 2).

- [ ] **Step 4: PXE-boot hpg5**

Power-cycle hpg5 with PXE first in boot order (or trigger via BMC if available). It should chainload `f8b46aab55c7-add-node-ocp.ipxe` and start the add-node flow.

- [ ] **Step 5: Approve CSRs as hpg5 reports in**

```bash
oc get csr
oc adm certificate approve <name>
```

Typically two rounds: kubelet client cert, then kubelet serving cert.

- [ ] **Step 6: Confirm node joined**

```bash
oc get nodes -o wide
```

Expected: `hpg5.igou.systems` (or whatever name the assisted installer assigns) appears as `Ready` with role `worker`.

- [ ] **Step 7 (optional follow-up): Remove hpg5 from k8s_internal_nodes**

If hpg5 should no longer be considered a k3s control plane in inventory, make a follow-up commit in `igou-inventory` removing it from the `k8s_internal_nodes` block in `inventory.yaml`. Out of scope for the initial inventory wiring — left here so it isn't forgotten.

---

## Self-Review Notes

Spec coverage:
- `openshift_workers_<cluster>` group → Task 1 ✓
- Per-worker `openshift_add_node_mac` → Task 1 ✓
- Cluster `openshift_add_node_arch` → Task 2 ✓
- Cluster `openshift_add_node_boot_artifacts_base_url` → Task 2 ✓
- DHCP-only path explicit (no `openshift_add_node_network_config`) → noted in plan header and Task 1 ✓
- `--tags monitor` unavailable for this worker → noted in plan header ✓

Type/name consistency:
- `F8:B4:6A:AB:55:C7` used identically in Task 1 (inventory write) and Task 3 (expected render).
- `http://10.10.45.242/ocp-add-node/` used identically in Task 2 (inventory write) and Task 3 (expected render).
- Group name `openshift_workers_ocp` matches what the playbook reads (`groups['openshift_workers_' + target_cluster]` with `target_cluster=ocp`).
- iPXE filename `f8b46aab55c7-add-node-ocp.ipxe` in Task 4 derives from the playbook's `{{ mac | replace(':', '') }}-add-node-{{ cluster_host }}.ipxe` pattern (`f8:b4:6a:ab:55:c7` lowercased before colon-stripping is implicit in Ansible's default Jinja string handling — the source MAC is uppercase but lowercase output is conventional; if the actual emitted filename is uppercase, `f8b46aab55c7` becomes `F8B46AAB55C7`. Verify in Task 4 Step 2's diff output and adjust this checklist if needed).

No placeholders. Every code block has the actual value an operator needs.

The destructive operational step (k3s teardown on hpg5) is flagged in the plan header and Task 4 pre-conditions, not silently assumed.
