# netbootxyz on rb5009 — design

**Date:** 2026-05-07
**Status:** approved (brainstorming complete)
**Scope:** one new playbook `playbooks/routeros/deploy_netbootxyz.yml` (plus six task files under `playbooks/routeros/tasks/`) that stands up the official netbootxyz container on the MikroTik RB5009 using the RouterOS container package, replacing the existing TrueNAS-hosted netbootxyz container as the homelab's PXE server.

## Goals

- Deploy `ghcr.io/netbootxyz/netbootxyz` as a RouterOS container on rb5009.
- Reachable on the existing LAN bridge at static IP **10.10.99.100/24** (gateway `10.10.99.1`).
- Serve the custom iPXE menus from `/home/igou/igou-node-bootstrap/netbootxyz-menus`.
- Re-runnable: idempotent for every RouterOS resource the playbook creates; tag-driven for selective re-runs (image rebuild vs. menu refresh vs. container restart).

## Non-goals

- DHCP/PXE option configuration on the router (next-server, bootfile-name, UEFI/BIOS chaining). Tracked as a follow-up sibling playbook.
- TrueNAS netbootxyz decommission. Out of scope; will be done manually after rb5009 cutover is verified.
- Pre-populating the container's `assets/` directory with cached ISOs.
- Multi-host. The playbook targets the rb5009 only.
- Molecule scenarios. RouterOS doesn't run in a container; consistent with the existing routeros playbook suite.
- Automating `/system/device-mode` enablement. RouterOS gates that behind a physical-button confirmation; the playbook fails loud with the manual command if it isn't already set.

## Inventory & connection

The rb5009 is already in `igou-inventory/inventory.yaml`:

```
routeros_routers:
  rb5009.igou.systems
routeros_netboot:
  rb5009.igou.systems
  crs328.igou.systems
```

Connection settings live in `igou-inventory/group_vars/routeros.yml` (network_cli, port 3480, user `igou+cet1024w`). No connection changes needed for this work.

## File layout

```
playbooks/routeros/
  deploy_netbootxyz.yml          # new — single-file, tagged stages
  tasks/
    netbootxyz_preflight.yml     # new — device-mode + free-space + arch checks
    netbootxyz_image.yml         # new — local podman pull/save → tar; net_put → router
    netbootxyz_network.yml       # new — veth, bridge attach
    netbootxyz_container.yml     # new — global config, mounts, envs, container add/start
    netbootxyz_menus.yml         # new — push menus from local source dir
    netbootxyz_verify.yml        # new — HTTP probe from control node
```

Per-stage task files exist because each stage is meaningfully independent (each maps to a tag), and a single ~250-line monolithic playbook is harder to navigate than six focused includes. Matches the existing pattern of `tasks/wait_for_routeros.yml` and `tasks/fetch_artifact.yml` used by the upgrade playbooks.

Conventions (consistent with the rest of `playbooks/routeros/`):

- `hosts: "{{ host | default('routeros_routers') }}"`. The default scope is the routers group, but `routeros_netboot` is a single-host group right now and not used as the default to avoid surprise if the netboot membership grows.
- `gather_facts: false`.
- `serial: 1`.
- All RouterOS-side mutation uses `community.routeros.command`.
- The image-build half of the `image` stage runs `delegate_to: localhost`.

## Layout on the router's NAND

Created by the playbook, idempotent:

```
netbootxyz/
  image.tar                      # netbootxyz image tar, kept after import (~250 MB)
  config/                        # mounted into container as /config
    menus/                       # custom iPXE menus pushed from local source
      local/                     # mirror of menus/ (existing TrueNAS workaround)
containers-scratch/              # /container/config tmpdir
```

## New inventory variables

Appended to `igou-inventory/group_vars/routeros.yml`:

```yaml
netbootxyz_image: ghcr.io/netbootxyz/netbootxyz:latest
netbootxyz_image_platform: linux/arm64        # rb5009 is ARM64
netbootxyz_local_image_dir: "{{ playbook_dir }}/../../.cache/netbootxyz"
netbootxyz_menu_source_dir: /home/igou/igou-node-bootstrap/netbootxyz-menus

netbootxyz_veth_name: veth-netbootxyz
netbootxyz_address: 10.10.99.100/24
netbootxyz_gateway: 10.10.99.1
netbootxyz_bridge: bridge

netbootxyz_root_dir: netbootxyz                # under RouterOS / (flash root)
netbootxyz_tmpdir: containers-scratch
netbootxyz_container_hostname: netbootxyz
netbootxyz_container_logging: true
netbootxyz_env_extra: []                       # additional env entries; netbootxyz defaults are fine
netbootxyz_force_restart: false                # set via -e to force container recreate without other changes
```

Variables use a `netbootxyz_*` prefix to distinguish them from the fleet-wide `routeros_*` block. They're inventory-level (not per-host) because they describe one specific deployment; per-host overrides are still possible via `host_vars/`.

## Playbook design

### `deploy_netbootxyz.yml`

Top-level orchestration. Imports each task file with the matching tag:

```yaml
- import_tasks: tasks/netbootxyz_preflight.yml
  tags: [preflight, image, network, container, menus, verify]
- import_tasks: tasks/netbootxyz_image.yml
  tags: [image]
- import_tasks: tasks/netbootxyz_network.yml
  tags: [network]
- import_tasks: tasks/netbootxyz_container.yml
  tags: [container]
- import_tasks: tasks/netbootxyz_menus.yml
  tags: [menus]
- import_tasks: tasks/netbootxyz_verify.yml
  tags: [verify]
```

Preflight runs under every tag — every stage depends on at least the device-mode check. Image, network, container, menus, and verify each run only when their tag is selected (or no `--tags` filter is given).

Header comment block documents: the device-mode prerequisite, what the playbook does, the `-e host=` and `--tags` invocations, and the post-deploy follow-up (separate DHCP-options playbook).

### Stage `preflight` — `tasks/netbootxyz_preflight.yml`

1. `/system/device-mode print` — assert `container: yes`. Fail loud with the exact manual command (`/system/device-mode/update container=yes`, then physical button-press confirmation) if not.
2. `/system/resource print` — parse `free-hdd-space`, assert ≥ 600 MB free. (~250 MB image tar plus working space during import.)
3. `/system/resource print` — assert `architecture-name: arm64` matches `netbootxyz_image_platform`. Catches "ran from x86 control node without `--platform`" early.
4. `delegate_to: localhost`: `command -v podman` must succeed. Skipped automatically when `--skip-tags image` is in effect (handled via `tags: [image]` on this single task).

### Stage `image` — `tasks/netbootxyz_image.yml`

Build half (`delegate_to: localhost`) → upload half (network_cli):

1. `ansible.builtin.file` mkdir `netbootxyz_local_image_dir`, mode `0755`.
2. `podman pull --platform={{ netbootxyz_image_platform }} {{ netbootxyz_image }}`. `changed_when` based on stdout; pull-when-already-current reports `changed=false`.
3. `podman save {{ netbootxyz_image }} -o {{ netbootxyz_local_image_dir }}/netbootxyz.tar` — only when the pull was changed.
4. `ansible.builtin.stat` the local tar → register size.
5. On router: `/file/print where name="{{ netbootxyz_root_dir }}/image.tar"` → if size matches the local size, skip upload; if absent or size differs, `ansible.netcommon.net_put` the tar.
6. Set fact `netbootxyz_image_changed: true` only when net_put fired. Consumed by the `container` stage to decide whether the container row needs to be removed and re-added.

This stage does **not** run `/container/add`; that's the `container` stage's job. This stage only ensures the image tar is on the router.

### Stage `network` — `tasks/netbootxyz_network.yml`

1. `/interface/veth/print where name={{ netbootxyz_veth_name }}` → register existing.
2. If absent: `/interface/veth/add name={{ netbootxyz_veth_name }} address={{ netbootxyz_address }} gateway={{ netbootxyz_gateway }}`. If present and address or gateway differs: `/interface/veth/set ...`. Set fact `netbootxyz_network_changed: true` on either branch.
3. `/interface/bridge/port/print where interface={{ netbootxyz_veth_name }}` → if not bound to `{{ netbootxyz_bridge }}`, `/interface/bridge/port/add bridge={{ netbootxyz_bridge }} interface={{ netbootxyz_veth_name }}`.

The veth gets the LAN IP; the host side of the veth has no IP. Bridging the veth to `bridge` puts the container on the LAN at L2.

### Stage `container` — `tasks/netbootxyz_container.yml`

1. **Global container config.** `/container/config/print` → if `tmpdir` differs from `{{ netbootxyz_tmpdir }}`, `/container/config/set tmpdir={{ netbootxyz_tmpdir }}`. `registry-url` left default; offline tar import doesn't use it.
2. **Mount.** `/container/mounts/print where name=netbootxyz-config` → if absent, `/container/mounts/add name=netbootxyz-config src={{ netbootxyz_root_dir }}/config dst=/config`. If present with different src/dst, `/container/mounts/set ...`.
3. **Envs.** `/container/envs/print where name=netbootxyz-env` → reconcile against `netbootxyz_env_extra`. Default is empty list (rely on netbootxyz defaults). Add/remove individual rows to match.
4. **Container row.** `/container/print where root-dir={{ netbootxyz_root_dir }}` →
   - If absent: `/container/add file={{ netbootxyz_root_dir }}/image.tar interface={{ netbootxyz_veth_name }} root-dir={{ netbootxyz_root_dir }} mounts=netbootxyz-config envlist=netbootxyz-env hostname={{ netbootxyz_container_hostname }} logging={{ netbootxyz_container_logging }}`. Then poll `/container/print` until `status` ≠ `extracting` (timeout 120 s).
   - If present and any of `netbootxyz_image_changed`, `netbootxyz_network_changed`, or `netbootxyz_force_restart` is true: `/container/stop`, wait for `status: stopped`, `/container/remove`, then re-add as above. Updating an in-place container's image isn't supported; remove+re-add is the documented path.
   - Else: leave as-is.
5. **Start.** `/container/start <id>` if `status` ≠ `running`. Then poll `/container/print` until `status: running` (timeout 60 s). Fail the host if the timeout expires.

### Stage `menus` — `tasks/netbootxyz_menus.yml`

1. `delegate_to: localhost`: `find {{ netbootxyz_menu_source_dir }} -type f` → register list of relative paths.
2. For each file, compute local size.
3. For each file, in two passes:
   - Pass A: dest `{{ netbootxyz_root_dir }}/config/menus/<rel-path>`.
   - Pass B: dest `{{ netbootxyz_root_dir }}/config/menus/local/<rel-path>` — mirrors the TrueNAS workaround. If a future netbootxyz image release no longer needs this, pass B drops in a follow-up.
4. Each `net_put` is preceded by `/file/print where name=...`. If size matches, skip; else `net_put` and report `changed=true`.
5. No container restart triggered from this stage. netbootxyz's nginx serves config dir contents directly; menu changes appear immediately. If a future image caches menus, set `netbootxyz_force_restart: true` on the next run.

### Stage `verify` — `tasks/netbootxyz_verify.yml`

1. `wait_for` from localhost: `host=10.10.99.100 port=80 delay=5 timeout=60`.
2. `ansible.builtin.uri`: GET `http://10.10.99.100/menu.ipxe` → assert `status: 200` and body contains `#!ipxe`.
3. On router: `/container/print where root-dir={{ netbootxyz_root_dir }}` → debug-print final status, image, mounts. Useful evidence in the recap.

No TFTP probe. HTTP success is sufficient verification at deploy time; real PXE boot from a client is the manual final-mile validation in the testing ladder below.

## Idempotency model

Every "did it change?" decision is gated on a `print where ...` read, never on `add` exit code (RouterOS errors on duplicates).

| Resource | Idempotency check |
|---|---|
| veth | `/interface/veth/print where name=...` matches name + address + gateway |
| bridge port | `/interface/bridge/port/print where interface=<veth>` exists |
| container global tmpdir | `/container/config/print` `tmpdir` matches |
| container mount | `/container/mounts/print where name=...` matches src + dst |
| container envs | `/container/envs/print where name=...` matches list contents |
| container row | `/container/print where root-dir=...` exists, no change-flags set |
| image tar on flash | `/file/print where name=...` size matches local size |
| menu files | `/file/print where name=...` size matches local size |

`changed_when` is explicit per task — read-only `print` is `changed_when: false`; `set`/`add`/`remove` is `changed_when: true`. The recap accurately reflects what mutated.

A second run with no upstream image change, no menu change, and no var change reports `changed=0` for all stages, including `image`.

## Risks and mitigations

1. **Device-mode not allowing containers.** Pre-flight asserts `container: yes` and fails loud with the exact manual command. Not auto-recoverable; RouterOS requires a physical button-press to enable.
2. **NAND space exhaustion.** Pre-flight asserts ≥ 600 MB free. If it fails, the user investigates manually (likely a stale image.tar from a previous failed run).
3. **Architecture mismatch.** `--platform=linux/arm64` on `podman pull` plus router-side `architecture-name: arm64` assert. Catches accidentally-amd64 tars before the broken container is added.
4. **IP collision on 10.10.99.100.** Out of scope to detect proactively. The header comment in `deploy_netbootxyz.yml` documents that 10.10.99.100 must be reserved (DHCP exclusion or static lease).
5. **Container has no internet route.** netbootxyz fetches upstream menu archives at startup unless `MENU_VERSION` is pinned. The verify stage's HTTP probe catches this — if the container can't bootstrap menus, `/menu.ipxe` won't return.
6. **Custom menu drift on container restart.** netbootxyz can overwrite `config/menus/` from baked-in menus on first start unless `MENU_VERSION=local` is set. The TrueNAS playbook's `local/` mirror is the existing workaround; we mirror it here.
7. **`net_put` is one-file-at-a-time.** ~10 menu files × 2 passes = 20 transfers per full menu deploy. Tolerable; no parallelism inside one host.

## Testing strategy

No molecule (consistent with rest of routeros playbook suite).

Manual ladder:

1. `playbooks/routeros/test_connection.yaml -e host=rb5009.igou.systems` — sanity.
2. `deploy_netbootxyz.yml --tags preflight -e host=rb5009.igou.systems` — verify device-mode + free-space + arch are correct. Fix manually if the assert fires.
3. Full deploy: `deploy_netbootxyz.yml -e host=rb5009.igou.systems`. Verify HTTP probe passes.
4. Re-run with no changes: every stage reports `changed=0`.
5. Touch one menu file, run `--tags menus,verify`: only that file's net_put fires.
6. Bump `netbootxyz_image: ...:vX` in inventory, run `--tags image,container,verify`: container is removed and recreated, status `running`.
7. From a PXE-capable client on the LAN, manually configure DHCP options or use iPXE to chain to `http://10.10.99.100/menu.ipxe`, and verify a real PXE boot.

`ansible-lint --profile=production` and `yamllint` clean before commit. Pre-commit hook enforces both.

## Repo-level changes outside the playbook

- `.gitignore`: add `.cache/netbootxyz/` (the local podman save tar isn't checked in).
- `igou-inventory/group_vars/routeros.yml`: append the `netbootxyz_*` variable block. Same pattern as the existing `routeros_*` block. The change is committed in the `igou-inventory` repo, not in `igou-ansible`.

## Documentation

Header comment block at the top of `deploy_netbootxyz.yml` covering: prerequisites (device-mode container=yes; 10.10.99.100 reserved on DHCP), what the playbook does in each stage, `-e host=` and `--tags` invocation patterns, and the post-deploy step (separate playbook later for DHCP options on the router pointing PXE clients at 10.10.99.100). Matches the comment-header convention in `playbooks/routeros/backup.yml` and friends.

## Open items resolved during brainstorming

- Role of rb5009 vs TrueNAS: rb5009 replaces TrueNAS as the sole netbootxyz host. Decommission of TrueNAS container is out of scope for this work.
- Storage: onboard NAND, no USB drive.
- Networking: veth on existing `bridge` with static `10.10.99.100/24`, gateway `10.10.99.1`.
- Image source: offline tar via `podman pull` + `podman save` on the control node, `net_put` to the router, `/container/add file=...`.
- Menus: reuse `/home/igou/igou-node-bootstrap/netbootxyz-menus`. `assets/` left empty; netbootxyz fetches on demand.
- DHCP options: out of scope for this playbook; tracked as a future sibling playbook.
- Verify: HTTP probe only; no TFTP probe.
- File organization: single top-level `deploy_netbootxyz.yml` plus six per-stage task files under `tasks/`. Tags align 1:1 with stages.
