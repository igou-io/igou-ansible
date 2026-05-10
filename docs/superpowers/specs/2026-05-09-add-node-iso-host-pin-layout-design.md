# OpenShift add-node ISO — host-pin layout compatibility — design

**Date:** 2026-05-09
**Status:** SUPERSEDED 2026-05-10. The decision to have `add_node_iso.yml` write `host/MAC-<hex>.ipxe` with a managed-by header was reverted. `deploy_assets.yml` is now the sole writer of `host/MAC-<hex>.ipxe`; `add_node_iso.yml` writes only the boot artifacts, and the per-host iPXE script is rendered from inventory's `netboot_host_pins`. The cleanup task added by this spec stays in `add_node_iso.yml` (now scoped to remove its own legacy output). See the playbook header comment in `playbooks/openshift/add_node_iso.yml` for the current design.
**Scope:** Update `playbooks/openshift/add_node_iso.yml` Play 2 so the per-MAC iPXE script lands at the path the live `menu.ipxe` chain actually loads (`config/menus/host/MAC-<hexraw>.ipxe`). Drop the now-dead flat-path and `local/` mirror writes. Add a one-shot cleanup pass for stale flat-path files left by previous runs.

## Background

`add_node_iso.yml` was designed (`docs/superpowers/specs/2026-05-06-openshift-add-node-iso-netboot-design.md`) and shipped against an older netbootxyz layout where `menu.ipxe` chained to flat-named files `config/menus/<hexmac>-add-node-<cluster>.ipxe`. Subsequent work on the netbootxyz container (rendered `menu.ipxe`, `host/`-prefixed per-host dispatch) means the live container's `menu.ipxe` only chains to `config/menus/host/MAC-<hexraw>.ipxe`. Empirical evidence (dnsmasq-tftp logs from the netbootxyz container, captured during the `test_netboot_pxe` headless-verification work):

```
dnsmasq-tftp[23]: file /config/menus/host/MAC-029f47581bf2.ipxe not found for 10.10.9.38
dnsmasq-tftp[23]: sent /config/menus/stock-menu.ipxe to 10.10.9.38
```

The `<hexmac>-add-node-<cluster>.ipxe` files written by `add_node_iso.yml` are not loaded by anything today — a worker booting after `add_node_iso.yml` runs would land in the upstream `stock-menu.ipxe`, not the generated PXE script.

The boot artifact path (kernel/initrd/rootfs under `assets/<cluster>-add-node/`, served at `http://10.10.45.242/<cluster>-add-node/`) is unaffected — nginx maps `location /` to `root /assets`, so the URL still resolves.

## Goals

- Make `add_node_iso.yml` actually work end-to-end against the live netbootxyz layout: a worker PXE-booting after the playbook runs follows the generated iPXE script and chainloads the OpenShift kernel/initrd/rootfs.
- Stay minimal: smallest change that closes the gap. Defer the `deploy_assets.yml` coordination question until that playbook is implemented.
- Leave stale flat-path files behind cleanly with a one-shot cleanup, so operators investigating `config/menus/` later don't trip over dead files.

## Non-goals

- Coordination with `playbooks/netboot/deploy_assets.yml` (designed in `2026-05-08-netboot-asset-management-design.md`, not yet implemented). When that playbook lands, its `synchronize --delete=true` scope on `host/` will need an exclude for add-node-managed files; that is its design's problem, not this one's.
- Reframing add-node as a contributor to `netboot_host_pins` inventory. Out of scope; would block on `deploy_assets.yml` existing.
- Changes to Play 1 (`oc adm node-image create --pxe` + asset generation). Play 1 is fine.
- Changes to the nginx config or the boot-artifact asset path. Both already work with the live layout.
- Coordination with the existing `hpg5` static `netboot_host_pins` entry — see Risks.

## Architecture

### Path change

| Today | After this change |
|---|---|
| `config/menus/<hexmac>-add-node-<cluster>.ipxe` (flat) | `config/menus/host/MAC-<hexraw>.ipxe` |
| `config/menus/local/<hexmac>-add-node-<cluster>.ipxe` (mirror) | (removed) |

`<hexraw>` is the MAC with no separators, lowercased — matches both iPXE's `${mac:hexraw}` syntax and the convention used by the netbootxyz asset-management spec.

The flat-path file is dead code in the live deployment; we don't need to keep writing it. The `local/` mirror was a workaround for netbootxyz overwriting `menus/` on container restart; the live container's `host/` subdirectory is preserved across restarts, so the mirror isn't needed for `host/MAC-…ipxe` either.

### Generated file header

The iPXE script `oc adm node-image create --pxe` generates is copied verbatim into `host/MAC-<hexraw>.ipxe`. We prepend a comment block so operators inspecting the file on TrueNAS know its provenance:

```
#!ipxe
# Managed by playbooks/openshift/add_node_iso.yml
# Cluster: {{ target_cluster }}
# Worker:  {{ inventory_hostname }} (MAC {{ openshift_add_node_mac }})
# Generated: {{ ansible_date_time.iso8601 }}
# DO NOT EDIT — re-run add_node_iso.yml to refresh.
<rest of oc-generated iPXE script>
```

Implementation: read the oc-generated script, prepend the header, write to dest. Preserves the script's `#!ipxe` shebang as the first interpreter directive (the comments are after the shebang).

Wait — `oc adm node-image create --pxe` generates a file already starting with `#!ipxe`. The cleanest is:

```
#!ipxe
# Managed by playbooks/openshift/add_node_iso.yml ...
<rest of oc-generated iPXE script content AFTER its leading #!ipxe>
```

So the implementation strips the leading `#!ipxe` line from the oc output, then writes our own `#!ipxe\n# Managed by ...\n` followed by the rest of the script body. iPXE only needs one shebang; comments are no-ops.

### One-shot cleanup pass

After writing the new files, Play 2 runs:

```yaml
- name: Cleanup -- remove stale flat-path add-node iPXE scripts
  ansible.builtin.file:
    path: "{{ item }}"
    state: absent
  with_fileglob:
    - "{{ truenas_menus_root }}/*-add-node-*.ipxe"
    - "{{ truenas_menus_root }}/local/*-add-node-*.ipxe"
```

Idempotent (no-op once gone). Glob matches the legacy filename pattern only, never `host/MAC-…ipxe`. Logged so operators see what was removed on the first post-migration run.

`with_fileglob` resolves on the *control node*, not on `truenas`. Since this play targets `truenas`, we need a `find` task delegated to truenas plus a `file: state=absent` loop. Concrete shape:

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

## Variables

No new variables. Existing inventory schema is unchanged.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `host/MAC-<hex>.ipxe` collision with a static `netboot_host_pins` entry (e.g. `hpg5` MAC `f8:b4:6a:ab:55:c7` already has an inventory pin with a CentOS-autoinstall + OCP-join menu). | Documented in the playbook header: add-node owns the file; running `add_node_iso.yml` overwrites whatever was there. Today the inventory pin isn't actually deployed (no `deploy_assets.yml`) so there's no functional collision yet; when `deploy_assets.yml` lands, that playbook's design must add an exclude for add-node-managed paths. |
| `oc adm node-image create --pxe` produces a different per-host script for each worker (rather than the spec's claim that the script is identical across workers). | The current Play 2 already loops the iPXE-script copy per worker, so swapping the destination from flat to `host/MAC-<hex>.ipxe` is symmetric. Either way each worker gets its own file at its own path. |
| `local/` mirror was needed by some operations not yet identified. | The asset-management spec calls out `local/` as a netbootxyz container-restart workaround for files at `menus/` flat. The container's `host/` subdir is part of the same bind-mount and survives restart equivalently. If a future investigation surfaces a real need for `local/host/` mirroring, add it then; YAGNI today. |
| Cleanup glob `*-add-node-*.ipxe` accidentally matches a hand-crafted file an operator added under `config/menus/`. | The pattern is specific (must contain `-add-node-`). The cleanup task lists the files it removes (loop label) so the first run shows the operator exactly what was deleted. Recoverable from the netbootxyz container's bind-mount snapshot if catastrophic. |
| Operators relying on the old `<cluster>-add-node-<hexmac>.ipxe` filename for some external automation. | Header comment in the playbook documents the rename. No known external consumers exist; flagged here, not designed-around. |

## Testing strategy

- **Lint:** `ansible-lint --profile=production playbooks/openshift/` and `yamllint playbooks/openshift/`. Both must pass cleanly. No molecule scenario (matches existing `add_node_iso.yml` pattern).
- **Pre-merge functional test (k3s teardown done; no destructive risk):**
  1. Inventory: ensure `openshift_workers_ocp` group exists with at least one worker (e.g. `hpg5.igou.systems`) carrying `openshift_add_node_mac` and `openshift_add_node_boot_artifacts_base_url` in cluster host_vars.
  2. Run: `ansible-navigator run playbooks/openshift/add_node_iso.yml -i igou-inventory/inventory.yaml -e target_cluster=ocp` (or `ansible-playbook` direct).
  3. On TrueNAS, verify `/mnt/ssd/containers/netbootxyz/config/menus/host/MAC-f8b46aab55c7.ipxe` exists with the generated header comment block, contains `kernel http://10.10.45.242/ocp-add-node/...` URL.
  4. Verify the asset directory `/mnt/ssd/containers/netbootxyz/assets/ocp-add-node/` contains the kernel/initrd/rootfs files.
  5. Verify any pre-existing `config/menus/*-add-node-*.ipxe` flat files have been removed (cleanup pass).
  6. PXE-boot the worker. Observe TrueNAS dnsmasq logs (`docker logs ix-netbootxyz-netbootxyz-1 | grep dnsmasq-tftp`): expect `sent /config/menus/host/MAC-f8b46aab55c7.ipxe to <worker-ip>` instead of `not found ... fall through to stock-menu.ipxe`. CSR approval after boot is unchanged (manual `oc adm certificate approve`).
- **Re-run idempotency:** Running the playbook twice with no inventory changes should re-generate fresh assets (the `oc adm node-image create --pxe` step always runs after wiping the work-dir; that's intentional, the assets are operands of the live cluster's certificate state). The cleanup pass shows zero matches on the second run. The `host/MAC-…ipxe` write reports `changed` if the rendered content differs from what's on disk, `ok` otherwise.

## Open items resolved during brainstorming

- "Compatibility" scope: minimal change today, defer `deploy_assets.yml` coordination.
- Path: `config/menus/host/MAC-<hexraw>.ipxe` (matches what the live `menu.ipxe` chains to).
- Drop `local/` mirror: yes (the netbootxyz workaround that motivated the mirror only applies to `menus/` flat, not `host/`).
- hpg5 collision: acceptable; add-node wins; documented in header.
- Stale-file cleanup: one-shot `find` + `file: absent` loop on TrueNAS, scoped to `*-add-node-*.ipxe` glob.
- File header: prepend a `# Managed by ...` comment block documenting provenance.
- Variables: none new.
- Inventory: unchanged.
