# Consuming the `david_igou.armbian` collection from the homelab

**Date:** 2026-05-30
**Status:** Approved (design); implementation plan to follow
**Branch:** `feat/armbian-collection-consumption`

## Problem

Armbian SBC lifecycle work has, until now, been driven by running playbooks
*directly from* the development collection at
`github.com/david-igou/ansible-collection-armbian`. We want that lifecycle
invoked from this homelab codebase instead — runnable both locally
(`ansible-navigator` / `ansible-playbook`) and as AAP job templates — so the
collection becomes a pinned dependency, not the place we operate from.

The collection author (also the operator) considers the collection's `roles/`
to be the product and its top-level `playbooks/` (including the `routeros/`
transport playbooks) to be **reference examples**, not the production
lifecycle. Networking-gear transport must therefore live **in this repo**, so
rb5009 wiring can change without cutting a collection release.

## What the collection provides (v0.0.3-alpha)

- **Namespace / FQCN prefix:** `david_igou.armbian`
- **Git tag:** `v0.0.3-alpha` (note the `v` prefix; the request said
  "0.0.3-alpha")
- **Runtime dependency:** `ansible.posix` (already in `requirements.yml`)
- **8 roles** — the reusable, single-purpose, transport-agnostic logic:
  `image_build`, `rootfs_provision`, `disk_provision`, `disk_image`,
  `pxelinux_render`, `board_boot_verify`, `board_boot_wait`,
  `bootstrap_armbian`
- **Reference playbooks** (`playbooks/*.yml`, `playbooks/routeros/*.yml`,
  `playbooks/tasks/*.yml`, `playbooks/tests/*.yml`) — examples for readers,
  **not consumed directly** by this design.

### Why recreate orchestration rather than import the collection's playbooks

We evaluated three consumption models:

1. **Thin `import_playbook` wrappers around the collection's composite
   playbooks.** Rejected for the boot-mode flows: `converge_boot_mode.yml`
   references `{{ playbook_dir }}/tasks/*.yml` and spans multiple host groups,
   and its single-play body (`_converge_boot_mode.yml`) lives under the
   collection's `playbooks/tasks/` — not FQCN-addressable from outside the
   collection. Importing it is the *fragile* path, and it keeps transport
   inside the collection lifecycle, which the operator explicitly wants to
   avoid.
2. **Vendor/fork the collection playbooks into the repo.** Rejected — defeats
   the point of a versioned dependency and guarantees drift.
3. **Recreate orchestration in this repo, invoking the collection roles.**
   Chosen. The complex logic is in the roles (flows to us via a
   `requirements.yml` version pin). The orchestration glue is thin:
   `_resolve_board_config` is a ~15-line `set_fact | combine` of three
   inventory layers plus an assert; the retry tasks are generic. Recreating
   that glue gives full, readable ownership of config-resolution and rb5009
   transport with no coupling to collection-internal file paths, and matches
   the author's "roles are the product, playbooks are examples" intent.

**Accepted trade-off:** improvements to the collection's *reference
playbooks* will not auto-flow to this repo. Role-logic improvements still flow
via version bumps. This is the intended ownership boundary, not a regression.

## Design

### 1. Dependency

Add to the root `requirements.yml` (`collections:` section), matching the
existing `type: git` pattern used for `ansible-truenas`:

```yaml
  - name: https://github.com/david-igou/ansible-collection-armbian.git
    type: git
    version: v0.0.3-alpha
```

The root `requirements.yml` feeds both local installs
(`ansible-galaxy collection install -r requirements.yml`) and the EE build
(`galaxy: ../../requirements.yml`). The EE-build workflows watch
`requirements.yml` (e.g. `.github/workflows/igou-awx-ee-build.yml:13`), so a
merge to `main` **auto-triggers** an `igou-awx-ee` rebuild + push to quay —
closing the AAP-availability loop with no manual step.

### 2. Repository layout

New domain directory `playbooks/armbian/`. The root-level
`playbooks/armbian-firstboot.yaml` is retired into it as `bootstrap.yaml`.

```
playbooks/armbian/
  bootstrap.yaml              # role: bootstrap_armbian (+ hostname / root-disable parity)
  build_and_publish.yaml      # resolve → image_build role (per opted-in host) → rsync publish
  stage_netboot_assets.yaml   # resolve → rootfs_provision role (per board, on netboot_server)
  provision_local_disk.yaml   # safety asserts → disk_provision role
  converge_boot_mode.yaml      # resolve → pxelinux_render → [transport] → board_boot_verify
  set_boot_mode.yaml           # -e override wrapper over converge
  reprovision_to_local.yaml    # converge-nfs → disk_provision loop → converge-local w/ revert
  tasks/
    _resolve_board_config.yml  # set_fact combine(family→model→host) + required-field assert
    _resolve_build_profile.yml
    _resolve_rootfs_src.yml
    cold_boot_with_retry.yml   # generic PoE-cycle + wait retry orchestration (Phase 2)
    wait_for_ssh.yml
  transport/                   # homelab-owned rb5009 transport (Phase 2)
    poe_cycle.yml              # community.routeros: PoE off → drain → on
    plumbing_check.yml         # assert /ip tftp rows exist before converging
    upload_pxelinux_cfg.yml    # push rendered pxelinux.cfg to router flash
  tests/
    fleet_e2e.yaml             # six-phase deterministic lifecycle over the fleet (destructive, opt-in)
```

Each orchestration playbook is a small, single-purpose unit: it resolves the
inputs it needs from inventory, then composes one or more collection roles.
Transport task-files under `transport/` have one job each and a documented
variable contract, so they can be swapped without touching the orchestration.

### 3. Flow specifications

#### Phase 1 — transport-free flows

- **`bootstrap.yaml`** — calls the `bootstrap_armbian` role (connect as root
  with `armbian_default_password`, create the inventory user with SSH-key auth
  + passwordless sudo). Adds homelab parity tasks for the behaviours today's
  `armbian-firstboot.yaml` has that the role may not: set `/etc/hostname` and
  disable root SSH login / root password. (Role parity is verified during
  implementation; parity tasks only added where the role does not cover them.)
- **`build_and_publish.yaml`** — three plays mirroring the collection
  reference: (1) resolve `armbian_board_config` + `armbian_build` per board
  host and compute custom-build opt-in; (2) on the builder group, run
  `image_build` per opted-in host and rsync per-host output to controller
  staging; (3) on the netboot-server group, publish staged per-host dirs to
  `images/<host>/`. rsync runs via `command:`/`argv:` over SSH (not
  `ansible.posix.synchronize`) to preserve the agent-disabled, cross-host
  semantics the reference documents.
- **`stage_netboot_assets.yaml`** — resolve `armbian_board_config` +
  `armbian_rootfs_src` per board host, then loop the `rootfs_provision` role
  per board on the netboot-server group.
- **`provision_local_disk.yaml`** — assert `armbian_local_disk_device` is set
  and is not the currently-mounted root device, then invoke `disk_provision`
  with a synthesized single-root-partition binding. Transport-agnostic.

#### Phase 2 — boot-mode flows + native transport

- **`converge_boot_mode.yaml`** — recreated natively against a board target:
  pre-flight `transport/plumbing_check.yml` (assert router TFTP rows) →
  resolve `armbian_board_config` → render pxelinux.cfg via the
  `pxelinux_render` role → `transport/upload_pxelinux_cfg.yml` (push to
  router) → `tasks/cold_boot_with_retry.yml` (invokes
  `transport/poe_cycle.yml`) → `tasks/wait_for_ssh.yml` → `board_boot_verify`
  role. PoE / TFTP are native homelab tasks here — no collection seam
  variables, no `{{ playbook_dir }}` coupling.
- **`set_boot_mode.yaml`** — asserts `armbian_boot_mode` was passed via `-e`,
  then runs the converge flow (highest-precedence `-e` overrides the
  inventory-declared mode).
- **`reprovision_to_local.yaml`** — full local-disk reprovision: converge to
  NFS + assert `/` is NFS-rooted, cross-binding validation (no duplicate mount
  paths, exactly one `/`), loop `disk_provision` over `armbian_local_disks`,
  converge to local with auto-revert to NFS on failure.

#### `tests/fleet_e2e.yaml` (Phase 2, destructive, opt-in)

Six-phase deterministic lifecycle over a target group, composing the recreated
flows/roles, mirroring the collection's `test_fleet_e2e.yml`:

- Phase 0 — PoE-off all targets (clean slate).
- Phase 1 — force-refresh per-host NFS rootfs on the netboot server (boards
  stay off).
- Phase 2 — converge NFS, power on + wait, run `bootstrap.yaml`
  unconditionally, verify rootfs on NFS.
- Phase 3 — from NFS, `dd` the canonical SD image via the `disk_image` role.
- Phase 4 — converge SD, cycle + wait, bootstrap, verify rootfs on local
  block device.
- Phase 5 — converge NFS, reprovision NVMe via `disk_provision`, converge
  `local_kernel`, verify rootfs on NVMe.

Because it wipes SD + NVMe, it is **guarded by an explicit opt-in**: it asserts
`armbian_e2e_confirm | default(false) | bool` before any destructive phase and
defaults `target_hosts` to a narrow pattern, never the whole fleet implicitly.

### 4. Transport contract (`transport/*.yml`)

Homelab-owned, RouterOS/rb5009-specific, seeded from the collection's
reference versions but maintained here:

- **`poe_cycle.yml`** (task file) — power-cycle the board's PoE port
  (off → drain → on) via `community.routeros`. Inputs: the board's switch
  port / router identity from inventory hostvars.
- **`plumbing_check.yml`** — assert `/ip tftp` rows exist for each
  `(host, asset)` pair before converging; fail with a pointer to the staging
  step if missing.
- **`upload_pxelinux_cfg.yml`** — upload rendered `01-<mac>` pxelinux.cfg
  files to router flash and register the TFTP rows.

`community.routeros` (already pinned at 3.20.0 in `requirements.yml`) is the
only added transport dependency, and it is already bundled in `igou-awx-ee`.

### 5. AAP config-as-code (`igou-inventory/group_vars/aap/job_templates.yml`)

- **Repoint** the existing `armbian_firstboot` template from
  `playbooks/armbian-firstboot.yaml` to `playbooks/armbian/bootstrap.yaml`;
  update `extra_vars` to the role's contract (`armbian_bootstrap_user`,
  `armbian_bootstrap_ssh_keys`), keep the `armbian_default` credential.
- **Add** templates for the new flows, following existing conventions
  (`project: igou_ansible`, `execution_environment: igou-awx-ee`, `sbc` label,
  `ask_variables_on_launch: true`):
  - `armbian_build_publish`, `armbian_stage_netboot`,
    `armbian_provision_local_disk` — credential `ansible_user_ed25519`.
  - `armbian_converge_boot_mode`, `armbian_set_boot_mode`,
    `armbian_reprovision_local` — also a RouterOS credential for the transport;
    `concurrent_jobs_enabled: false`.
- **`fleet_e2e.yaml`** is **not** auto-registered as a template (destructive).
  Optionally added later as a gated template
  (`concurrent_jobs_enabled: false`, `ask_variables_on_launch: true`,
  requires `armbian_e2e_confirm`).

Templates are applied by the existing `aap_sync_templates` job.

## Dependencies, assumptions, and risks

- **Inventory contract (igou-inventory).** These flows require per-board vars:
  `armbian_board_config_{family,model,host}` layers, `armbian_build`,
  `armbian_rootfs_src`, `armbian_local_disks` / `armbian_local_disk_device`,
  `armbian_default_password`, the router pointer, and the relevant group names
  (boards, builders, netboot_server, router). Populating these in
  `igou-inventory` is a **related but separable workstream**; this design
  covers the consumption mechanism only. The recreated `_resolve_*` includes
  assert required fields and fail fast and actionably when inventory is
  incomplete.
- **Bootstrap parity.** Verify the `bootstrap_armbian` role covers
  user/keys/sudo; add homelab tasks for `/etc/hostname` and root-disable only
  if the role does not, to match today's `armbian-firstboot.yaml`.
- **EE rollout ordering.** AAP can run the new playbooks only after the
  `requirements.yml` change has rebuilt `igou-awx-ee` and the new image is
  pulled. Local `ansible-navigator` runs work as soon as the collection is
  installed locally.
- **Group-name mapping.** The collection defaults to group names like
  `boards`, `armbian_builders`, `netboot_server`. The recreated playbooks must
  use this repo's actual inventory group names (parameterized with sensible
  defaults).
- **Netboot topology.** Per the established netboot architecture, rb5009 owns
  TFTP/binaries while public.igou.systems nginx owns assets; the
  staging/publish flows must target the correct host for each artifact class.

## Out of scope

- Populating the igou-inventory per-board variables (separate workstream).
- Any change to the collection itself (no upstream commits required).
- Migrating other (non-armbian) playbooks.

## Verification

- `ansible-galaxy collection install -r requirements.yml` resolves
  `david_igou.armbian` at `v0.0.3-alpha`.
- `ansible-navigator run`/`ansible-playbook --syntax-check` on each new
  playbook; `ansible-lint --profile=production` and `yamllint` clean.
- A local non-destructive dry run (e.g. `--check` where meaningful, or a
  single-board converge with `armbian_cycle_board=false`) before any
  fleet-wide or destructive invocation.
- EE rebuild succeeds and `ansible-galaxy collection list` in the built image
  shows `david_igou.armbian`.
