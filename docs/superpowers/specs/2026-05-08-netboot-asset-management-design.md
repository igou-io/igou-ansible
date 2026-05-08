# netboot.xyz asset management — design

**Date:** 2026-05-08
**Status:** approved (brainstorming complete)
**Scope:** one new playbook tree at `playbooks/netboot/` that replaces `playbooks/truenas/configure_netbootxyz.yml` and folds in `playbooks/truenas/sync_boot_files.yml`. Drives the menu, per-host pins, kickstart/cloud-init seeds, and binary asset fetches for the TrueNAS-hosted netbootxyz container from inventory and from in-repo source content. Eliminates the hardcoded `/home/igou/igou-node-bootstrap/netbootxyz-menus` reference.

## Goals

- Move all custom iPXE menu content, kickstart configs, cloud-init seeds, and per-host overrides from the external `igou-node-bootstrap` checkout into this repo, so the same review/lint/CI flow applies and the playbook can run from any control node (laptop, AAP, AWX).
- Drive the menu declaratively via a `netboot_entries` inventory list — adding an entry is a YAML edit plus optionally a small file in `playbooks/netboot/files/`.
- Support per-host PXE pins by MAC and/or hostname, auto-served from the netbootxyz HTTP root with no manual chainload wiring on the client.
- Provide an escape hatch (`fragments/`) for hand-written `.ipxe` content the declarative schema can't express.
- Idempotent: re-running with no input change reports `changed=0`. Tag-driven so a menu touch-up doesn't re-fetch ISOs.
- Abstract the netbootxyz host via inventory (`netbootxyz_host`, `netbootxyz_root`, `netbootxyz_self_url`) so a future move (e.g. back to rb5009) is a one-line edit.

## Non-goals

- Refactoring the OpenShift add-node and agent-install flows. They keep writing per-cluster files directly to `/mnt/ssd/containers/netbootxyz/config/menus/` outside this playbook's purview. A later refactor can fold them in.
- Touching the iPXE binary build (`playbooks/routeros/deploy_netboot_binaries.yml`). That stays as-is.
- Migrating the `igou-node-bootstrap` repo. The user manually translates its current content into `netboot_entries` / `fragments/` during the cutover; the playbook doesn't import legacy content.
- Multi-host netbootxyz. One TrueNAS container is the only consumer. The host abstraction is forward-looking, not a parallel deployment today.
- Molecule scenarios. Consistent with the rest of `playbooks/truenas/` and `playbooks/routeros/`.

## Inventory & connection

No connection changes. TrueNAS already in `igou-inventory/inventory.yaml`; `igou-inventory/group_vars/truenas.yml` already configures the connection.

New inventory variables live in `igou-inventory/group_vars/all/netboot.yml` (split-dir style — converts existing `all.yml` to `all/main.yml`, new file sits beside).

```yaml
netbootxyz_host: truenas
netbootxyz_root: /mnt/ssd/containers/netbootxyz
netbootxyz_self_url: http://10.10.45.242

netboot_entries: []
netboot_host_pins: []
```

`netbootxyz_self_url` is the externally-reachable URL the menu uses for `chain` calls back to itself (per-host hooks, kickstart references). `netbootxyz_root` is the TrueNAS-side filesystem path that maps to the container's `/config` and `/assets` mounts.

## File layout

```
playbooks/netboot/
  deploy_assets.yml                     # new — orchestrator, tagged stages
  tasks/
    preflight.yml                       # new — schema validation (always runs)
    render_menu.yml                     # new — generate menu.ipxe + entries/ + host/ to .cache/
    push_text.yml                       # new — sync menus, kickstart, cloud-init to TrueNAS
    fetch_binaries.yml                  # new — idempotent download of upstream URLs into /assets
    push_local_artifacts.yml            # new — copy local-built kernels/initrds into /assets
    verify.yml                          # new — HTTP probes for menu.ipxe, host files, ISOs
  templates/
    menu.ipxe.j2                        # new — top-level menu, per-host header + entry list + fragments
    entry-kernel.ipxe.j2                # new — per-entry boot of kernel/initrd
    entry-iso.ipxe.j2                   # new — per-entry sanboot of ISO
    host-mac.ipxe.j2                    # new — per-host pinned recipe
  files/
    fragments/                          # new — hand-written .ipxe escape hatch (auto-listed)
    kickstart/                          # new — text kickstart configs
    cloud-init/                         # new — text cloud-init seeds (user-data, meta-data)
```

`.cache/netboot-menus/` (gitignored) holds the rendered menu artifacts before push.

Per-stage task files exist because each maps 1:1 to a tag and the stages are meaningfully independent. Matches the pattern set by `deploy_netbootxyz.yml` and `deploy_netboot_binaries.yml`.

Conventions (consistent with the rest of `playbooks/`):

- `hosts: "{{ netbootxyz_host | default('truenas') }}"`
- `gather_facts: false`
- `become: true` only on push/fetch tasks that touch the container's filesystem
- `delegate_to: localhost` on render and validation
- File-on-disk owner/group `1000:1000`, mode `0644` files / `0755` dirs

## Inventory schema

### `netboot_entries`

Each entry is a dict. `id` is the slug used as a filename (`entries/<id>.ipxe`); `kind` discriminates the template.

```yaml
netboot_entries:

  # --- kind: kernel — netbootxyz proxies the upstream URLs at boot time ---
  - id: debian-12-preseed
    name: "Debian 12 (preseed)"
    kind: kernel
    kernel: https://deb.debian.org/debian/dists/bookworm/main/installer-amd64/current/images/netboot/debian-installer/amd64/linux
    initrd: https://deb.debian.org/debian/dists/bookworm/main/installer-amd64/current/images/netboot/debian-installer/amd64/initrd.gz
    cmdline: "auto=true url=${netboot_self}/assets/kickstart/debian.preseed"
    kickstart: debian.preseed             # path under playbooks/netboot/files/kickstart/
    cache: false                          # opt-in: download to /assets and serve locally

  # --- kind: iso — sanboot a pre-staged ISO; sha256 required ---
  - id: talos-1.9
    name: "Talos 1.9"
    kind: iso
    url: https://github.com/siderolabs/talos/releases/download/v1.9.0/metal-amd64.iso
    sha256: 1234abcd...

  # --- kind: chainload — chain to another .ipxe URL ---
  - id: rocky-9-ks
    name: "Rocky 9 (kickstart)"
    kind: chainload
    url: ${netboot_self}/assets/kickstart/rocky9.ipxe

  # --- kind: local — ship a locally-built kernel/initrd from the control node ---
  - id: custom-rescue
    name: "Custom rescue kernel"
    kind: local
    kernel_src: "{{ playbook_dir }}/../../.cache/rescue/vmlinuz"
    initrd_src: "{{ playbook_dir }}/../../.cache/rescue/initrd.img"
    cmdline: "console=ttyS0 rescue"
```

Behavior per kind:

| kind | Menu content | /assets fetch | Required fields |
|---|---|---|---|
| `kernel` | `kernel ${k}` + `initrd ${i}` + `imgargs ${cmdline}` + `boot` | none unless `cache: true`, then download both into `/assets/cache/<id>/` and rewrite URLs | `kernel`, `initrd` |
| `iso` | `kernel ${memdisk} raw iso` + `initrd /assets/iso/<id>.iso` + `boot` | `get_url` upstream into `/assets/iso/<id>.iso` with `checksum: "sha256:<sha>"` | `url`, `sha256` |
| `chainload` | `chain ${url}` | none | `url` |
| `local` | `kernel /assets/local/<id>/vmlinuz` + `initrd /assets/local/<id>/initrd` + `imgargs ${cmdline}` + `boot` | `copy` from control node into `/assets/local/<id>/` | `kernel_src`, `initrd_src` |

`${netboot_self}` is rendered to `{{ netbootxyz_self_url }}` at template time so kickstart/cloud-init URLs always resolve regardless of where netbootxyz lives.

### `netboot_host_pins`

Each pin binds a MAC and/or hostname to a boot recipe. Pinned hosts short-circuit the menu UI at the top of `menu.ipxe`.

```yaml
netboot_host_pins:

  # --- form 1: pin to an existing entry by id ---
  - mac: aa:bb:cc:dd:ee:ff
    hostname: worker-01.igou.systems    # optional; both can match
    entry: talos-1.9

  # --- form 2: inline kernel/initrd ---
  - mac: 11:22:33:44:55:66
    kernel: https://...
    initrd: https://...
    cmdline: "..."

  # --- form 3: free-form .ipxe escape hatch ---
  - mac: 22:33:44:55:66:77
    fragment: |
      #!ipxe
      kernel ...
      initrd ...
      boot
```

Generated:
- `host/MAC-<hexraw>.ipxe` — one per pin (MAC lowercase, no separators).
- `host/HOSTNAME-<name>.ipxe` — one per pin with `hostname` set, chains to the MAC file if both are present (avoids duplication).

Validation enforced by the `preflight` stage:

- MAC regex: `^([0-9a-f]{2}:){5}[0-9a-f]{2}$` (case-insensitive). Rendered filename uses lowercase hexraw.
- `id` slugs: `^[a-z0-9][a-z0-9._-]*$`.
- For `kind: iso`, `sha256` is required.
- Each pin must specify exactly one of `entry` / inline `kernel`+`initrd` / `fragment`. Mixing fails preflight.
- `entry: <id>` references must resolve to an `id` in `netboot_entries`.
- `kickstart:` / `cloud-init:` references must resolve to a file under `playbooks/netboot/files/{kickstart,cloud-init}/`.

## Generated layout on TrueNAS

```
/mnt/ssd/containers/netbootxyz/
  config/menus/
    menu.ipxe                           # rendered from menu.ipxe.j2
    entries/<id>.ipxe                   # one per netboot_entries item (per kind)
    host/MAC-<hexraw>.ipxe              # one per host pin
    host/HOSTNAME-<name>.ipxe           # one per host pin with hostname
    fragments/<filename>.ipxe           # copied verbatim from playbooks/netboot/files/fragments/
    local/                              # mirror of menu.ipxe + entries/ + host/ + fragments/ (netbootxyz workaround)
    <existing-openshift-files>.ipxe     # untouched — flat-named files written by other playbooks
  assets/
    kickstart/                          # synced from playbooks/netboot/files/kickstart/
    cloud-init/                         # synced from playbooks/netboot/files/cloud-init/
    iso/<id>.iso                        # one per kind: iso entry
    local/<id>/{vmlinuz,initrd}         # one dir per kind: local entry
    cache/<id>/{vmlinuz,initrd}         # opt-in cache for kind: kernel
```

The `local/` mirror under `config/menus/` exists because netbootxyz overwrites `config/menus/` on container start unless a `local/` mirror is present — same workaround the existing playbook applies.

## Generated `menu.ipxe` header

```
#!ipxe
:per_host
isset ${hostname} && chain {{ netbootxyz_self_url }}/menus/host/HOSTNAME-${hostname}.ipxe || goto check_mac
:check_mac
isset ${mac} && chain {{ netbootxyz_self_url }}/menus/host/MAC-${mac:hexraw}.ipxe || goto main_menu
:main_menu
... menu UI listing each entries/<id>.ipxe + a "Custom" submenu listing fragments/*.ipxe ...
```

The `||` falls through silently when no per-host file exists. A pinned host short-circuits before ever rendering the menu UI; everything else gets the normal menu.

`${mac:hexraw}` is the canonical iPXE syntax for the booted NIC's MAC with no separators (confirmed against `roles/netbootxyz/templates/disks/netboot.xyz.j2:101` in the upstream netboot.xyz Ansible build).

## Playbook stages

Top-level orchestration. Each `import_tasks` is wrapped in a tag so any subset can be run with `--tags`.

```yaml
- import_tasks: tasks/preflight.yml             # tags: [render, push, fetch, local, verify]
- import_tasks: tasks/render_menu.yml           # tags: [render]
- import_tasks: tasks/push_text.yml             # tags: [push]
- import_tasks: tasks/fetch_binaries.yml        # tags: [fetch]
- import_tasks: tasks/push_local_artifacts.yml  # tags: [local]
- import_tasks: tasks/verify.yml                # tags: [verify]
```

Preflight runs under every tag — every other stage depends on validated input.

### Stage `preflight`

`delegate_to: localhost`. Schema validation only; doesn't touch TrueNAS.

1. Assert `netboot_entries` is a list of dicts. For each entry: validate `id` slug, `kind` membership, kind-specific required fields.
2. Assert `netboot_host_pins` MACs match regex. Each pin must specify exactly one of `entry` / inline `kernel`+`initrd` / `fragment`.
3. Assert `entry: <id>` references in pins resolve to a real entry id.
4. Assert `kickstart:` / `cloud-init:` references resolve to files under `playbooks/netboot/files/`.
5. `find` `playbooks/netboot/files/fragments/*.ipxe` → register list of fragment filenames for the `render` stage to consume.

Fails with one error per offending entry, naming the offending field.

### Stage `render` — `tasks/render_menu.yml`

`delegate_to: localhost`. Output written to `.cache/netboot-menus/`.

1. `template menu.ipxe.j2 → .cache/netboot-menus/menu.ipxe`. Inputs: `netboot_entries`, `netboot_host_pins`, fragment list from preflight, `netbootxyz_self_url`.
2. For each entry, render the appropriate per-kind template:
   - `entry-kernel.ipxe.j2` for `kind: kernel` and `kind: local`
   - `entry-iso.ipxe.j2` for `kind: iso`
   - `chainload` entries embed inline in `menu.ipxe`, no separate file
3. For each host pin, render `host/MAC-<hexraw>.ipxe`. If `hostname:` set, render `host/HOSTNAME-<name>.ipxe` chaining to the MAC file.

### Stage `push` — `tasks/push_text.yml`

Runs against `netbootxyz_host`.

- `synchronize` (or `copy` with `directory_mode` for podman EE compatibility — final choice during implementation) the cache dir + `playbooks/netboot/files/{fragments,kickstart,cloud-init}/` into:
  - `{{ netbootxyz_root }}/config/menus/` (menu.ipxe, entries/, host/, fragments/)
  - `{{ netbootxyz_root }}/config/menus/local/` (mirror)
  - `{{ netbootxyz_root }}/assets/kickstart/`
  - `{{ netbootxyz_root }}/assets/cloud-init/`
- Owner/group `1000:1000`, mode `0644` files / `0755` dirs.
- `delete=true` scoped to `menus/entries/`, `menus/host/`, `menus/fragments/` only — never `menus/` itself, so the OpenShift add-node files survive.

### Stage `fetch` — `tasks/fetch_binaries.yml`

Runs against `netbootxyz_host`.

For every `kind: iso` entry:

1. `stat` `{{ netbootxyz_root }}/assets/iso/<id>.iso`.
2. If absent, or size mismatches, or sha256 mismatches: `get_url` with `checksum: "sha256:{{ entry.sha256 }}"`, `force: false`. Re-runs are no-ops once the file is correct.

For every `kind: kernel` entry with `cache: true`:

3. Same pattern, target `{{ netbootxyz_root }}/assets/cache/<id>/{vmlinuz,initrd}`. Default `cache: false` — netbootxyz proxies on demand.

### Stage `local` — `tasks/push_local_artifacts.yml`

For every `kind: local` entry:

1. `stat` `kernel_src` / `initrd_src` on the control node → register size + checksum.
2. Compare to `{{ netbootxyz_root }}/assets/local/<id>/{vmlinuz,initrd}` via `stat`.
3. `copy` only if missing or size mismatch. Owner/group `1000:1000`.

### Stage `verify` — `tasks/verify.yml`

`delegate_to: localhost`.

1. HTTP GET `{{ netbootxyz_self_url }}/menu.ipxe` → 200, body starts with `#!ipxe`, body contains every entry's `name`.
2. For each `kind: iso` (and each `kind: kernel` with `cache: true`): HTTP HEAD `{{ netbootxyz_self_url }}/iso/<id>.iso` (or `/cache/<id>/vmlinuz`) → 200, content-length matches local sha-checked size.
3. For each host pin: HTTP GET `{{ netbootxyz_self_url }}/menus/host/MAC-<hexraw>.ipxe` → 200.

## Idempotency model

Every "did it change?" check is gated on an explicit pre-read.

| Resource | Idempotency check |
|---|---|
| Rendered `menu.ipxe` | Local content hash vs. remote `stat` checksum |
| Per-entry `entries/<id>.ipxe` | Local content hash vs. remote |
| Per-host `host/MAC-*.ipxe` / `HOSTNAME-*.ipxe` | Local content hash vs. remote |
| Static fragments / kickstart / cloud-init | `synchronize` (or copy with checksum) — diff-based |
| `kind: iso` asset on disk | sha256 from inventory vs. remote sha256 (or size if no checksum) |
| `kind: local` artifact | Source size + mtime vs. remote |
| Cached `kind: kernel` asset | sha256 if provided, else size |

`changed_when` is explicit per task. Read-only stats are `changed_when: false`. A re-run with no input changes reports `changed=0`.

## Migration plan

A single PR cuts over. No phased migration:

1. New playbook tree at `playbooks/netboot/`.
2. New inventory file `igou-inventory/group_vars/all/netboot.yml` (split-dir style — `all.yml` becomes `all/main.yml`, new file sits beside) with initial `netboot_entries` and `netboot_host_pins`. Initial content: whatever currently lives in `~/igou-node-bootstrap/netbootxyz-menus/` translated by hand into the schema.
3. Custom `.ipxe` content from `~/igou-node-bootstrap/netbootxyz-menus/` that doesn't fit the declarative schema is dropped into `playbooks/netboot/files/fragments/` verbatim.
4. `playbooks/truenas/configure_netbootxyz.yml` deleted.
5. `playbooks/truenas/sync_boot_files.yml` deleted; `truenas_boot_files_*` vars removed from `igou-inventory/group_vars/truenas.yml`.
6. `.gitignore` adds `.cache/netboot-menus/`.
7. Header comment block at the top of `playbooks/netboot/deploy_assets.yml` covering: how to add an entry, how to add a per-host pin, how to add a fragment, tag invocation patterns, and the manual translation step from `igou-node-bootstrap` content.

`igou-node-bootstrap` is left alone in this PR. The user can archive it after the cutover lands and is verified.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Per-host `chain` URL hits 404 → user sees a transient connection error before the menu | `||` fall-through to `:main_menu` makes 404 silent; `verify` HTTP GETs every host file at deploy time and catches a misconfigured pin |
| `synchronize delete=true` in wrong scope wipes OpenShift add-node files | Scoped to `menus/entries/`, `menus/host/`, `menus/fragments/` only — never `menus/` itself |
| Large ISO download blocks the playbook for minutes | `kind: iso` only fetches when sha256 mismatches; `--tags fetch` separates the slow stage |
| User edits a generated file directly on TrueNAS | Header comment in every rendered file: `# Managed by playbooks/netboot/deploy_assets.yml — do not edit manually` |
| Inventory typo (e.g. wrong MAC in `netboot_host_pins`) silently produces a host file no one references | `verify` does a positive HTTP GET on every host file; doesn't catch typos but ensures every generated file is reachable |
| MAC variable syntax wrong for the booted NIC | `${mac:hexraw}` (the auto-aliased MAC of the booted NIC); confirmed against `roles/netbootxyz/templates/disks/netboot.xyz.j2:101` in the upstream netboot.xyz Ansible build |
| File ownership drift if the netbootxyz container is redeployed under a different UID | Locked at `1000:1000` in `truenas_docker_containers`; flagged here, not designed around |
| Two writers (this playbook + OpenShift add-node) into the same `config/menus/` directory | Different filename namespaces (`entries/`, `host/`, `fragments/` vs. flat `<hexmac>-add-node-<cluster>.ipxe`) and a scoped `delete=true` keep them isolated |

## Testing strategy

No molecule (consistent with the rest of `playbooks/truenas/`).

Manual ladder:

1. `--tags render -e netbootxyz_host=localhost --check` — local-only render, no TrueNAS contact. Eyeball generated `menu.ipxe` + per-host files in `.cache/netboot-menus/`.
2. Full deploy with `--check --diff` first.
3. Real run; verify HTTP probes pass.
4. Re-run; every stage reports `changed=0`.
5. Touch one entry's `cmdline:` in inventory; re-run with `--tags render,push,verify`; only that entry file changes.
6. Add a `netboot_host_pins` entry for a known MAC; PXE-boot that host; confirm it boots the pinned recipe instead of seeing the menu. PXE-boot a different host; confirm it sees the menu.
7. `ansible-lint --profile=production` and `yamllint` clean before commit.

## Repo-level changes outside the playbook

- `.gitignore`: add `.cache/netboot-menus/`.
- `igou-inventory/group_vars/all.yml` → `all/main.yml` (split-dir conversion).
- `igou-inventory/group_vars/all/netboot.yml`: new file with `netbootxyz_host`, `netbootxyz_root`, `netbootxyz_self_url`, `netboot_entries`, `netboot_host_pins`.
- `igou-inventory/group_vars/truenas.yml`: remove `truenas_boot_files_*` block.

## Documentation

Header comment block at the top of `playbooks/netboot/deploy_assets.yml` covering:

- The TrueNAS netbootxyz container as the only consumer.
- How to add a `netboot_entries` item (per kind), with a one-line example for each.
- How to add a `netboot_host_pins` item (all three forms).
- How to add a fragment (drop a file in `files/fragments/`).
- `--tags render,push,fetch,local,verify` invocation patterns.
- The manual translation step from `igou-node-bootstrap` content (one-time cutover).

Matches the comment-header convention in `playbooks/truenas/configure_docker_containers.yml` and `playbooks/routeros/deploy_netboot_binaries.yml`.

## Open items resolved during brainstorming

- Scope: custom .ipxe menus + boot media (kernels/initrds/ISOs/rootfs) + per-host iPXE scripts. iPXE binaries (.kpxe/.efi) stay in their own playbook.
- Pain points addressed: hardcoded laptop path, separate-repo review/CI gap, manual workflow when adding a new menu/asset.
- Source-of-truth: in-repo (`playbooks/netboot/files/` + inventory). `igou-node-bootstrap` is archived after cutover.
- Add-entry workflow: hybrid declarative (`netboot_entries` list) + fragment escape hatch.
- Deployment target: TrueNAS only; host abstracted via inventory for future portability.
- Per-host pins: served via HTTP from TrueNAS at `menus/host/`, hooked from a header at the top of `menu.ipxe`. The TFTP-on-rb5009 alternative (which would require switching to the `disks/` binary variant) is out of scope.
- OpenShift add-node and agent-install playbooks: left unchanged. Filename namespace separation prevents collision. Folding them in is a later refactor.
- ISO sha256: required for `kind: iso`. Catches corrupted downloads at fetch time.
- `kind: kernel` cache: opt-in (`cache: true`), defaults off (netbootxyz proxies upstream).
- `synchronize delete=true`: scoped to subdirs we own; flat `menus/` is never deleted from.
- Migration: single PR, manual translation of `igou-node-bootstrap` content into the new schema, deletion of the two legacy playbooks.
