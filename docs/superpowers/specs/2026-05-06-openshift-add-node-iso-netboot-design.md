# OpenShift add-node ISO netboot playbook — design

**Date:** 2026-05-06
**Author:** David Igou (with Claude)
**Status:** Approved for implementation planning

## Summary

Add a new playbook, `playbooks/openshift/add_node_iso.yml`, that automates the OpenShift "Adding worker nodes to an on-premise cluster" procedure (`oc adm node-image create --pxe`) and delivers the resulting PXE assets to the existing TrueNAS netbootxyz server. The playbook reads worker definitions from per-cluster inventory groups, generates a multi-host `nodes-config.yaml`, runs the `oc` command, and copies kernel/initramfs/rootfs plus per-MAC iPXE scripts into the netbootxyz layout already used by `deploy_pxe_assets.yml`.

This is a distinct procedure from the existing agent-based installer flow. The agent installer creates an *initial* cluster; this playbook adds workers to an *existing* one. Both deliver via the same TrueNAS netbootxyz layout but use parallel namespaces so they never collide.

Source procedure: <https://github.com/openshift/openshift-docs/blob/main/nodes/nodes/nodes-nodes-adding-node-iso.adoc>.

## Goals

- Render `nodes-config.yaml` from inventory and run `oc adm node-image create --pxe` against a live cluster, producing PXE assets for one or more workers in a single run.
- Deliver the assets and per-MAC iPXE scripts to TrueNAS netbootxyz under a parallel namespace (`<cluster>-add-node`) so they coexist with agent-install assets.
- Keep credential handling minimal: the operator supplies `KUBECONFIG`; the pull secret is extracted from the cluster itself at runtime.
- Provide an opt-in `monitor` tag for the `oc adm node-image monitor` step. CSR approval remains manual.

## Non-goals

- Cluster-side preparation (MachineSets, BareMetalHost, etc.). The procedure deliberately works without those.
- Auto-approving CSRs. The operator runs `oc get csr` / `oc adm certificate approve <name>` after the worker boots.
- Booting the worker (BMC/PXE wake-up). Booting is whatever you do today.
- ISO output mode. PXE-only.
- Reusable role abstraction. Logic lives in the playbook for now (YAGNI).
- Multi-cluster orchestration in a single run. One `target_cluster` per invocation.

## Architecture

### File layout

```
playbooks/openshift/add_node_iso.yml          # new
playbooks/openshift/templates/                # new (or co-located)
  └── nodes-config.yaml.j2                    # rendered per run
```

The playbook sits as a sibling of `sno_iso_provision.yml` rather than under `agent-install/`, since `oc adm node-image create` is a different procedure from the agent installer.

### Plays

**Play 1 — Generate PXE assets**

- `hosts: "{{ target_cluster }}"`, `connection: local` (the cluster host already has `ansible_connection: local`).
- Mirrors the `connection: local` + work-dir pattern from `playbooks/openshift/agent-install/deploy_pxe_assets.yml`.

**Play 2 — Deliver to TrueNAS netbootxyz**

- `hosts: truenas`, `become: true`, `gather_facts: false`.
- Mirrors the second play of `deploy_pxe_assets.yml`.

### Invocation

```bash
export KUBECONFIG=...   # operator must set this before running
ansible-navigator run playbooks/openshift/add_node_iso.yml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  [--tags monitor]
```

`--tags monitor` adds the `oc adm node-image monitor` step at the end of Play 1; default behavior stops at delivery.

## Inventory schema

### Per-cluster worker group

`openshift_workers_<cluster>` — e.g., `openshift_workers_ocp`. The playbook iterates `groups['openshift_workers_' + target_cluster]`. Empty or missing groups produce a clear preflight failure.

### Per-worker host_vars

```yaml
---
# Required
openshift_add_node_mac: "aa:bb:cc:dd:ee:ff"

# Optional — fall back to inventory_hostname if unset
openshift_add_node_hostname: "worker-01.igou.systems"

# Optional — root device hint, omitted from nodes-config.yaml if unset
openshift_add_node_root_device:
  deviceName: /dev/sda

# Optional — full nmstate networkConfig, omitted if unset (cluster defaults / DHCP apply)
openshift_add_node_network_config:
  interfaces:
    - name: eth0
      type: ethernet
      state: up
      mac-address: "aa:bb:cc:dd:ee:ff"
      ipv4:
        enabled: true
        dhcp: true

# Optional — interface name; defaults to eth0
openshift_add_node_interface_name: eth0
```

### Per-cluster host_vars (additions to e.g. `host_vars/ocp.yml`)

```yaml
openshift_add_node_arch: x86_64                                   # default x86_64
openshift_add_node_boot_artifacts_base_url: http://10.10.45.242/ocp-add-node/
openshift_add_node_work_dir: "{{ ansible_env.HOME }}/openshift-add-node/{{ target_cluster }}"
```

`openshift_add_node_boot_artifacts_base_url` must point at the HTTP path netbootxyz exposes for the asset subdir. The convention is `<existing-base>/<cluster>-add-node/`.

### Variable summary

| Variable | Scope | Required | Default |
|---|---|---|---|
| `target_cluster` | extra-var | yes | — |
| `openshift_add_node_mac` | per-worker host_vars | yes | — |
| `openshift_add_node_hostname` | per-worker host_vars | no | `inventory_hostname` |
| `openshift_add_node_root_device` | per-worker host_vars | no | omitted |
| `openshift_add_node_network_config` | per-worker host_vars | no | omitted (DHCP) |
| `openshift_add_node_interface_name` | per-worker host_vars | no | `eth0` |
| `openshift_add_node_arch` | cluster host_vars | no | `x86_64` |
| `openshift_add_node_boot_artifacts_base_url` | cluster host_vars | yes | — |
| `openshift_add_node_work_dir` | cluster host_vars | no | `~/openshift-add-node/{{ target_cluster }}` |

## Play 1 — Asset generation (cluster host)

### Preflight

- Assert `lookup('env', 'KUBECONFIG') | length > 0`. Fail with a message telling the operator to export `KUBECONFIG`.
- Assert `groups['openshift_workers_' + target_cluster] | default([]) | length > 0`. Fail with a message naming the expected group.
- For each worker in the group, assert `hostvars[w].openshift_add_node_mac is defined` and matches a MAC regex.

### Setup

- Remove and recreate `openshift_add_node_work_dir` so every run starts clean.
- Fetch the cluster pull secret to `<work_dir>/auth.json`:
  ```
  oc -n openshift-config get secret pull-secret \
    -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d
  ```
  Mode `0600`, `no_log: true`. The operator's `KUBECONFIG` must grant `get` on secrets in `openshift-config` (cluster-admin does by default).

### Render `nodes-config.yaml`

Jinja template that iterates `groups['openshift_workers_' + target_cluster]` and emits one `hosts[]` entry per worker. Optional fields are conditionally emitted.

```yaml
hosts:
{% for w in groups['openshift_workers_' + target_cluster] %}
{%   set h = hostvars[w] %}
  - hostname: {{ h.openshift_add_node_hostname | default(w) }}
    interfaces:
      - name: {{ h.openshift_add_node_interface_name | default('eth0') }}
        macAddress: "{{ h.openshift_add_node_mac }}"
{%   if h.openshift_add_node_root_device is defined %}
    rootDeviceHints:
      {{ h.openshift_add_node_root_device | to_nice_yaml | indent(6) }}
{%   endif %}
{%   if h.openshift_add_node_network_config is defined %}
    networkConfig:
      {{ h.openshift_add_node_network_config | to_nice_yaml | indent(6) }}
{%   endif %}
{% endfor %}
bootArtifactsBaseURL: {{ openshift_add_node_boot_artifacts_base_url }}
```

`bootArtifactsBaseURL` at the top level of `nodes-config.yaml` is the supported way to make the generated iPXE script reference the right HTTP path (per the procedure's "Cluster configuration reference" table).

### Generate PXE assets

```
oc adm node-image create --pxe \
  --dir <work_dir> \
  --registry-config <work_dir>/auth.json
```

Run via `ansible.builtin.command`, with `creates:` set to the expected ipxe-script path so reruns are idempotent. Stat the expected outputs and assert they exist; record their absolute paths to a fact (`openshift_add_node_pxe_assets`) for Play 2.

### Tagged: `monitor` (`tags: [monitor, never]`)

- Preflight: for every worker in the group, assert a static IP is derivable from `hostvars[w].openshift_add_node_network_config.interfaces[].ipv4.address[].ip`. If any worker lacks one, fail with a message instructing the operator to run `oc adm node-image monitor` manually with discovered IPs. DHCP discovery is out of scope for this playbook.
- Build the comma-separated IP list from those static addresses.
- Run `oc adm node-image monitor --ip-addresses <comma-list>`. Stream output; non-zero exit fails the play.

## Play 2 — Delivery to TrueNAS netbootxyz

```yaml
hosts: truenas
become: true
gather_facts: false
vars:
  cluster_host: "{{ target_cluster }}"
  work_dir: "{{ hostvars[cluster_host]['openshift_add_node_work_dir'] }}"
  arch: "{{ hostvars[cluster_host]['openshift_add_node_arch'] | default('x86_64') }}"
  truenas_assets_root: /mnt/ssd/containers/netbootxyz/assets
  truenas_menus_root: /mnt/ssd/containers/netbootxyz/config/menus
  asset_subdir: "{{ cluster_host }}-add-node"
```

### Tasks

1. **Stat preflight.** Verify `truenas_menus_root` exists and is a directory; fail clearly if not (matches existing pattern in `deploy_pxe_assets.yml`).

2. **Ensure asset directory.** Create `{{ truenas_assets_root }}/{{ asset_subdir }}/`, mode `0755`, owner/group `1000:1000`.

3. **Copy boot artifacts** with `ansible.posix.synchronize`:
   - src: `{{ work_dir }}/`
   - dest: `{{ truenas_assets_root }}/{{ asset_subdir }}/`
   - `delete: true`
   - rsync excludes: `*.ipxe`, `auth.json`, `nodes-config.yaml`
   - `--chown=1000:1000`

4. **Copy iPXE script per worker.** Loop over `groups['openshift_workers_' + cluster_host]`:
   - src: `{{ work_dir }}/node.{{ arch }}.ipxe`
   - dest: `{{ truenas_menus_root }}/{{ openshift_add_node_mac | replace(':', '') }}-add-node-{{ cluster_host }}.ipxe`
   - mode `0644`, owner/group `1000:1000`

5. **Duplicate iPXE script to `/local/`.** Same loop, dest `{{ truenas_menus_root }}/local/<same-filename>.ipxe`. This is the netbootxyz workaround already used by `deploy_pxe_assets.yml`.

The iPXE script content is identical across workers — the bootloader picks the right script by MAC at boot time, but the kernel/initramfs/rootfs URLs inside the script are the same for everyone (the per-host network config is delivered via the assisted-installer rendezvous, not the iPXE script).

## Operator workflow

1. Add the worker(s) to inventory under `openshift_workers_<cluster>` with at minimum a MAC address. Add network config if static IPs are required.
2. `export KUBECONFIG=<path-to-ocp-kubeconfig>`.
3. `ansible-navigator run playbooks/openshift/add_node_iso.yml -i igou-inventory/inventory.yaml -e target_cluster=ocp`.
4. PXE-boot the worker (BMC, manual reboot, etc.).
5. Optional: `ansible-navigator run … -e target_cluster=ocp --tags monitor` to watch progress (only valid if the worker has a static IP in inventory).
6. `oc get csr` and `oc adm certificate approve <name>` for any pending CSRs once the worker reports in.

## Testing

- **Lint:** `ansible-lint --profile=production` and `yamllint .`. Pre-commit covers both.
- **No molecule scenario.** This playbook hits live infra (a running OCP cluster, TrueNAS, the network); molecule wouldn't add real coverage. The existing `deploy_pxe_assets.yml` similarly has none.
- **Manual validation pre-merge:**
  1. Define one worker host in `openshift_workers_ocp` with a fake MAC. Run with `--check --diff` to inspect rendered `nodes-config.yaml` and the rsync plan.
  2. Real run with `target_cluster=ocp`. Confirm `node.x86_64.ipxe` and friends land in `/mnt/ssd/containers/netbootxyz/assets/ocp-add-node/`.
  3. Inspect `/menus/<mac>-add-node-ocp.ipxe`. Confirm URLs point at `openshift_add_node_boot_artifacts_base_url`.
  4. PXE-boot the worker. Run with `--tags monitor` to watch join progress. Manually approve any CSRs.

## Known unknowns to verify during implementation

1. **PXE asset filenames produced by `oc adm node-image create --pxe`.** Assumed pattern: `node.<arch>.ipxe`, `node.<arch>-vmlinuz`, `node.<arch>-initrd.img`, `node.<arch>-rootfs.img` (mirroring the agent-install equivalents). If the actual filenames differ, the stat list and the iPXE-script copy task must be updated. Verify with one real run on a test cluster before relying on the rsync exclude pattern.
2. **Whether top-level `bootArtifactsBaseURL` in `nodes-config.yaml` is honored when `--pxe` is set.** The docs describe this parameter for the YAML-config flow, but only by implication for the `--pxe` case. If the generated iPXE script ignores it, fall back to a post-generation regex/sed pass on the script file. Track as an implementation contingency.
3. **Pull-secret extraction permissions.** The kubeconfig the operator supplies must have `get` on `secrets` in `openshift-config`. Cluster-admin always does. Surface as a clear error if `oc get secret` fails.
4. **`oc adm node-image monitor` and reverse DNS.** Per the docs, monitor skips CSR checks if reverse DNS isn't available for the node. This is fine — CSR approval is manual anyway. Document the behavior in playbook comments so it isn't surprising.

## Out of scope (explicit non-changes)

- No changes to `playbooks/openshift/agent-install/deploy_pxe_assets.yml`.
- No changes to existing `openshift_agent_install_*` variables or the `David-Igou.openshift_agent_install` role.
- No changes to TrueNAS netbootxyz container configuration (asset/menu paths are reused as-is).
