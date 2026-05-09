# Add-node ISO host-pin layout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update `playbooks/openshift/add_node_iso.yml` Play 2 so the per-MAC iPXE script lands at `config/menus/host/MAC-<hexraw>.ipxe` (the path the live netbootxyz `menu.ipxe` actually chains to), drops the now-dead flat-path + `local/` mirror, prepends a managed-by header to the deployed script, and cleans up stale flat-path files left by previous runs.

**Architecture:** Single playbook touch — Play 2 only. Three logical changes: (a) ensure `config/menus/host/` exists on TrueNAS, (b) replace the two iPXE-script copy tasks (flat + `local/` mirror) with one `copy: content=` task that writes to `host/MAC-<hex>.ipxe` with a managed-by header prepended to the oc-generated script, (c) add a one-shot cleanup find+absent loop scoped to `*-add-node-*.ipxe` under `menus/` and `menus/local/`. Top-of-file comment is refreshed.

**Tech Stack:** Ansible (ansible.builtin.file/copy/find), the existing `ansible.posix.synchronize` for boot artifacts is unchanged.

---

## Reference material

- **Spec:** `docs/superpowers/specs/2026-05-09-add-node-iso-host-pin-layout-design.md` — read this first.
- **Playbook to modify:** `playbooks/openshift/add_node_iso.yml`. The full file content is in the conversation context if you need it; key sections are referenced by line below.
- **Live menu.ipxe behaviour:** the netbootxyz container's `menu.ipxe` chains via TFTP to `host/MAC-<hexraw>.ipxe` (lowercase, no separators) and falls through to `stock-menu.ipxe` on `not_found`. Verified empirically via `docker logs ix-netbootxyz-netbootxyz-1 | grep dnsmasq-tftp` during the headless-test work.
- **Path conventions:**
  - `truenas_menus_root` = `/mnt/ssd/containers/netbootxyz/config/menus`
  - `truenas_assets_root` = `/mnt/ssd/containers/netbootxyz/assets`
  - Both already defined in Play 2's `vars:` block.
- **MAC normalization:** strip colons, lowercase. Use `replace(':', '') | lower`.

## Conventions

- **Run from:** `/workspace/igou-ansible`.
- **Linters:** `ansible-lint --profile=production playbooks/openshift/add_node_iso.yml` and `yamllint playbooks/openshift/add_node_iso.yml`. Both must be clean before each commit.
- **Style:** YAML 1.2 booleans, 2-space indent, `gather_facts` unchanged from current values.
- **Each task ends with a commit** to keep history tight.

## Files Created/Modified/Deleted

**Modify:**
- `playbooks/openshift/add_node_iso.yml`

**Create:** none.
**Delete:** none.

## Pre-flight assumptions

- `KUBECONFIG` is set in the operator's environment, points at the `ocp` cluster, and grants `get` on secrets in the `openshift-config` namespace (cluster-admin works). Required only for the e2e step in Task 4 — earlier tasks are static edits + lint.
- Inventory has `openshift_workers_ocp` populated (per the prior commit `9fbdc7a` "Add OpenShift add-node ISO inventory wiring plan" — verify by `ansible-inventory -i igou-inventory/inventory.yaml --list | jq -r '.openshift_workers_ocp.hosts // []'`).
- `truenas` connection works (`ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.ping -b` returns SUCCESS).

If any of the above is broken, surface it before starting Task 4.

---

## Task 1: Switch Play 2 to write `host/MAC-<hex>.ipxe` with managed-by header; drop `local/` mirror

**Files:**
- Modify: `playbooks/openshift/add_node_iso.yml` (Play 2 body — the play named "Deliver PXE assets to TrueNAS netbootxyz")

This task replaces three tasks in Play 2 with two new tasks. Order in the file matters — keep them adjacent.

- [ ] **Step 1: Add `host/` directory ensure**

In `playbooks/openshift/add_node_iso.yml` Play 2, locate the existing task `Ensure asset destination directory exists` (the one that creates `{{ truenas_assets_root }}/{{ asset_subdir }}`). Immediately AFTER it, insert:

```yaml
    - name: Ensure host pin directory exists on TrueNAS
      ansible.builtin.file:
        path: "{{ truenas_menus_root }}/host"
        state: directory
        mode: "0755"
        owner: "1000"
        group: "1000"
```

- [ ] **Step 2: Replace the iPXE-script copy tasks**

Locate these two existing tasks (they appear sequentially near the bottom of Play 2):

```yaml
    - name: Copy iPXE script per worker into menus dir
      ansible.builtin.copy:
        src: "{{ work_dir }}/node.{{ arch }}.ipxe"
        dest: "{{ truenas_menus_root }}/{{ hostvars[item].openshift_add_node_mac | replace(':', '') }}-add-node-{{ cluster_host }}.ipxe"
        mode: "0644"
        owner: "1000"
        group: "1000"
      loop: "{{ groups['openshift_workers_' + cluster_host] }}"
      loop_control:
        label: "{{ item }}"

    # Mirrors the netbootxyz workaround in deploy_pxe_assets.yml: a duplicate
    # copy under /local/ that the menu chain references.
    - name: Copy iPXE script per worker into menus/local dir
      ansible.builtin.copy:
        src: "{{ work_dir }}/node.{{ arch }}.ipxe"
        dest: "{{ truenas_menus_root }}/local/{{ hostvars[item].openshift_add_node_mac | replace(':', '') }}-add-node-{{ cluster_host }}.ipxe"
        mode: "0644"
        owner: "1000"
        group: "1000"
      loop: "{{ groups['openshift_workers_' + cluster_host] }}"
      loop_control:
        label: "{{ item }}"
```

REPLACE both with the single new task below:

```yaml
    - name: Render iPXE script with managed-by header into host/MAC-<hex>.ipxe
      ansible.builtin.copy:
        dest: "{{ truenas_menus_root }}/host/MAC-{{ hostvars[item].openshift_add_node_mac | replace(':', '') | lower }}.ipxe"
        content: |
          #!ipxe
          # Managed by playbooks/openshift/add_node_iso.yml
          # Cluster: {{ cluster_host }}
          # Worker:  {{ item }} (MAC {{ hostvars[item].openshift_add_node_mac }})
          # DO NOT EDIT -- re-run add_node_iso.yml to refresh.
          {{ lookup('file', work_dir + '/node.' + arch + '.ipxe') | regex_replace('^#!ipxe\r?\n', '') }}
        mode: "0644"
        owner: "1000"
        group: "1000"
      loop: "{{ groups['openshift_workers_' + cluster_host] }}"
      loop_control:
        label: "{{ item }}"
```

Notes for the implementer:
- `lookup('file', ...)` reads the oc-generated `node.x86_64.ipxe` from the controller (where Play 1 created it). Strip its leading `#!ipxe\n` so the rendered file has exactly one shebang at the top.
- `replace(':', '') | lower` normalizes the MAC to the iPXE `${mac:hexraw}` form that menu.ipxe chains to.
- `content:` (rather than `src:` + a separate template file) keeps the change in-place; the templating is light enough that a separate `templates/host-pin.ipxe.j2` would be over-engineering.

- [ ] **Step 3: Lint**

```bash
yamllint playbooks/openshift/add_node_iso.yml
ansible-lint --profile=production playbooks/openshift/add_node_iso.yml
```

Both must pass. If `ansible-lint` complains about template injection in the `content` field (e.g. `template-instead-of-copy`), confirm the lint rule isn't a blocker — `copy: content=` with Jinja is the canonical pattern when there's no separate template file to maintain. If it is a hard block, pull the rendered string into a `set_fact` first and reference it from `content:`.

- [ ] **Step 4: Commit**

```bash
git add playbooks/openshift/add_node_iso.yml
git commit -m "$(cat <<'EOF'
add_node_iso: write iPXE script to host/MAC-<hex>.ipxe with header

The live netbootxyz menu.ipxe only chains via TFTP to
config/menus/host/MAC-<hex>.ipxe. The previous flat-path destination
(<hexmac>-add-node-<cluster>.ipxe) is dead code in the current
deployment -- a worker would never load it. Move the iPXE script to
host/MAC-<hex>.ipxe and drop the local/ mirror (host/ survives
container restart on its own).

The deployed script gains a managed-by header naming the playbook,
cluster, worker, and MAC. Operators investigating /config/menus/host/
on TrueNAS can see the file's provenance at a glance.

See docs/superpowers/specs/2026-05-09-add-node-iso-host-pin-layout-design.md
for the full design and the (deferred) deploy_assets.yml coordination
note.
EOF
)"
```

---

## Task 2: Add stale-file cleanup pass

**Files:**
- Modify: `playbooks/openshift/add_node_iso.yml` (Play 2 — append two tasks at the end)

- [ ] **Step 1: Append cleanup tasks**

At the END of Play 2's `tasks:` block (after the new "Render iPXE script ..." task from Task 1), append:

```yaml
    - name: Cleanup -- locate stale flat-path add-node iPXE scripts on truenas
      ansible.builtin.find:
        paths:
          - "{{ truenas_menus_root }}"
          - "{{ truenas_menus_root }}/local"
        patterns: "*-add-node-*.ipxe"
        file_type: file
      register: _stale_add_node_files

    - name: Cleanup -- remove each stale flat-path file
      ansible.builtin.file:
        path: "{{ item.path }}"
        state: absent
      loop: "{{ _stale_add_node_files.files }}"
      loop_control:
        label: "{{ item.path }}"
```

Notes:
- `find` runs on the play's target (truenas), so it only matches files that actually exist there.
- The `patterns: "*-add-node-*.ipxe"` glob is specific to the legacy filename shape; it never matches `host/MAC-<hex>.ipxe` files because those don't contain `-add-node-`.
- The `loop` over `_stale_add_node_files.files` makes the loop label include each file path, so the first run output explicitly names every file removed.

- [ ] **Step 2: Lint**

```bash
yamllint playbooks/openshift/add_node_iso.yml
ansible-lint --profile=production playbooks/openshift/add_node_iso.yml
```

Both must pass.

- [ ] **Step 3: Commit**

```bash
git add playbooks/openshift/add_node_iso.yml
git commit -m "$(cat <<'EOF'
add_node_iso: cleanup pass for stale flat-path iPXE scripts

One-shot find + absent loop scoped to *-add-node-*.ipxe under
config/menus/ and config/menus/local/. Removes the dead files
left by previous runs that wrote to the flat path. Idempotent
once gone (zero matches reported on subsequent runs).
EOF
)"
```

---

## Task 3: Refresh the playbook top-of-file comment block

**Files:**
- Modify: `playbooks/openshift/add_node_iso.yml` (header comment block, lines 1–25 area)

- [ ] **Step 1: Replace the existing header**

The existing top-of-file comment runs from line 1 through the `#  oc adm certificate approve <name>` line (around line 25). It currently describes the operator workflow steps. REPLACE the entire comment block (everything from `# Add worker nodes` down to the line just before `- name: Generate OpenShift add-node PXE assets`) with:

```yaml
# Add worker nodes to an existing OpenShift cluster by generating PXE
# assets via `oc adm node-image create --pxe` and copying them to the
# TrueNAS netbootxyz container.
#
# Source procedure:
#   https://github.com/openshift/openshift-docs/blob/main/nodes/nodes/nodes-nodes-adding-node-iso.adoc
#
# Layout (after this playbook runs against `truenas`):
#   /mnt/ssd/containers/netbootxyz/
#     assets/<cluster>-add-node/
#       node.<arch>-vmlinuz, node.<arch>-initrd.img, node.<arch>-rootfs.img
#     config/menus/host/MAC-<hexraw>.ipxe   # one per worker; chained from
#                                             menu.ipxe via TFTP
#
# The deployed iPXE script in host/MAC-<hex>.ipxe carries a managed-by
# header. host/MAC-<hex>.ipxe is shared with future
# playbooks/netboot/deploy_assets.yml (which would render it from
# inventory's netboot_host_pins[].fragment); add-node currently
# overwrites whatever was there. If a worker MAC also has a static
# netboot_host_pins entry (e.g. hpg5 / f8:b4:6a:ab:55:c7), running
# add_node_iso.yml replaces it. Re-run is the only way to refresh.
#
# Operator workflow:
#   1. Add worker(s) to inventory under group `openshift_workers_<cluster>`
#      with at minimum `openshift_add_node_mac`. Add
#      `openshift_add_node_network_config` (nmstate) for static IPs.
#   2. Set on the cluster host (e.g., host_vars/ocp.yml):
#        openshift_add_node_arch: x86_64
#        openshift_add_node_boot_artifacts_base_url: http://<netboot-host>/<cluster>-add-node/
#   3. export KUBECONFIG=<path-to-cluster-kubeconfig>
#   4. ansible-navigator run playbooks/openshift/add_node_iso.yml \
#        -i igou-inventory/inventory.yaml \
#        -e target_cluster=<cluster>
#   5. PXE-boot the worker.
#   6. Optionally: re-run with `--tags monitor` to watch the join.
#   7. Approve any pending CSRs manually:
#        oc get csr
#        oc adm certificate approve <name>
```

- [ ] **Step 2: Lint**

```bash
yamllint playbooks/openshift/add_node_iso.yml
ansible-lint --profile=production playbooks/openshift/add_node_iso.yml
```

Both must pass (header comments don't typically trip linters, but verify).

- [ ] **Step 3: Commit**

```bash
git add playbooks/openshift/add_node_iso.yml
git commit -m "$(cat <<'EOF'
add_node_iso: refresh header to document host/ layout + collisions

Spells out the on-disk layout the playbook produces, the shared-
namespace contract with future deploy_assets.yml, and the static-
pin overwrite behaviour for MACs like hpg5.
EOF
)"
```

---

## Task 4: End-to-end verification + push

**Files:**
- None changed in this task (verification only).

This task runs the playbook against the live `ocp` cluster to confirm the new layout actually works. No destructive risk per the user (k3s deprovisioned). Skip if `KUBECONFIG` isn't set or the cluster is unreachable; in that case fall back to a syntax-only check and mark Step 2/3 SKIPPED in the report.

- [ ] **Step 1: Quick reachability check**

```bash
test -n "$KUBECONFIG" && oc whoami 2>&1 | head -3
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.ping -b 2>&1 | head -3
ansible-inventory -i igou-inventory/inventory.yaml --list 2>/dev/null \
  | jq '.openshift_workers_ocp // {}' | head -20
```

Expected: `oc whoami` returns a username (not an error); the truenas ping returns SUCCESS; the inventory query lists at least one worker host with `openshift_add_node_mac`.

If any of these fails, SKIP steps 2-3 and proceed to Step 4 (commit nothing — just push the commits already made).

- [ ] **Step 2: Real run**

```bash
ansible-playbook playbooks/openshift/add_node_iso.yml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp 2>&1 | tee /tmp/add-node-iso-e2e.log
```

Expected: `failed=0`, `unreachable=0`. Play 1 generates assets in `~/openshift-add-node/ocp/`. Play 2 syncs boot artifacts to `assets/ocp-add-node/`, ensures `config/menus/host/`, writes `host/MAC-<hex>.ipxe` per worker, removes any stale `*-add-node-*.ipxe` files.

If failures: read the failure message and decide whether to fix in this branch or escalate.

- [ ] **Step 3: Verify on-disk state on TrueNAS**

```bash
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.shell \
  -a 'docker exec ix-netbootxyz-netbootxyz-1 ls -la /config/menus/host/' \
  -b 2>&1 | tail -20

ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.shell \
  -a 'docker exec ix-netbootxyz-netbootxyz-1 sh -c "head -10 /config/menus/host/MAC-*.ipxe | head -40"' \
  -b 2>&1 | tail -50

ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.shell \
  -a 'ls -la /mnt/ssd/containers/netbootxyz/assets/ocp-add-node/' \
  -b 2>&1 | tail -20

# Confirm cleanup
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.shell \
  -a 'find /mnt/ssd/containers/netbootxyz/config/menus -maxdepth 2 -name "*-add-node-*.ipxe"' \
  -b 2>&1 | tail -10
```

Expected:
- `host/` listing shows one file per worker MAC, e.g. `MAC-f8b46aab55c7.ipxe`.
- The `head -10` output shows the managed-by comment block at the top of each file.
- Boot artifacts (`*-vmlinuz`, `*-initrd.img`, `*-rootfs.img`) present in `assets/ocp-add-node/`.
- Cleanup `find` output is empty (no stale flat-path files remain).

- [ ] **Step 4: Push**

```bash
git push origin main 2>&1 | tail -5
```

This pushes the three commits from Tasks 1-3. No PR — main pushes are the project default for small playbook changes per recent history.

- [ ] **Step 5: (Optional) PXE-boot a worker to confirm chain**

If a worker host is available and willing to PXE-boot (the user already mentioned k3s is deprovisioned so destructive risk is gone), boot it and check the dnsmasq-tftp logs:

```bash
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.command \
  -a 'docker logs --tail=50 ix-netbootxyz-netbootxyz-1' \
  -b 2>&1 | grep dnsmasq-tftp | tail -10
```

Expected: a `sent /config/menus/host/MAC-<workerhex>.ipxe to <worker-ip>` line — confirms the live menu.ipxe chain reaches the new file. If the line is `not found ...` instead, the path or hex normalization is wrong; investigate.

This step is OPTIONAL because it depends on a willing worker; it's a stronger end-to-end signal but isn't required to declare the task done.

---

## Self-review summary (against the spec)

**Spec coverage:**
- Goal: make add-node work end-to-end against live layout → Tasks 1+2 (path swap + cleanup), Task 4 verification.
- Goal: stay minimal → Single playbook touched; no new files; no inventory changes.
- Goal: clean up stale files with one-shot pass → Task 2.
- Non-goal: deploy_assets.yml coordination → not implemented; spec is explicit.
- Non-goal: Play 1 changes → Play 1 untouched.
- Non-goal: nginx/asset path changes → unchanged.
- Architecture: path change `host/MAC-<hex>.ipxe` → Task 1 step 2.
- Architecture: file header comment → Task 1 step 2 (in the `content:` block).
- Architecture: cleanup task → Task 2.
- Risks: hpg5 collision, local/ mirror unused, cleanup glob safety → Task 3 header comment + Task 4 Step 5 verification.
- Testing: lint clean, e2e against live cluster, optional PXE confirmation → Task 4 covers all three.

**Placeholder scan:** none. Each step has the actual file content or command + expected output.

**Identifier consistency:**
- `truenas_menus_root`, `truenas_assets_root`, `cluster_host`, `work_dir`, `arch`, `asset_subdir` — all play-level vars, used identically wherever they appear.
- `host/MAC-<hexraw>.ipxe` — produced identically in Task 1 (write), Task 4 step 3 (verify), and the spec's Architecture table.
- `*-add-node-*.ipxe` glob — used identically in Task 2 (cleanup) and Task 4 step 3 (verify nothing remains).
- `_stale_add_node_files` register fact name — defined and consumed in Task 2 only.
