# AGENTS.md — ARM SBC / Armbian fleet

Guidance for AI agents working with the `boards` fleet. This directory
holds the playbooks that drive it; the collection logic lives in the
external `david_igou.armbian` collection.

> **Authoritative reference:** `../../docs/armbian-boot-modes.md`.
> If inventory comments, role docs, or this file disagree with it, that
> doc wins — fix the others to match. Read it before touching board
> kernels, boot modes, or netboot TFTP assets.

## The fleet

Rockchip RK3588/RK3588S ARM SBCs in `igou-inventory` group `boards`:
Orange Pi 5, 5 Pro, 5 Max, Rock 5A, and the FriendlyElec CM3588-NAS.
Powered/cold-cycled over PoE off the `crs328` switch (exception:
cm3588-nas, see per-board notes). **Inventory is the source of truth** —
router pins, TFTP artifacts, and on-disk partition layout are all
derived state rendered from host vars (`armbian_board_model`,
`armbian_boot_mode`, `armbian_local_kernel`, `armbian_local_disks`,
`armbian_poe_*`). Never hand-edit derived state; change inventory and
re-converge.

## Boot chain

Every board's u-boot DHCPs and TFTP-fetches a **per-MAC pxelinux pin**
from rb5009 (`sbc/pxelinux.cfg/01-<mac>`). The pin's `default <label>`
line selects the active mode. The pin is rendered from the
`armbian_boot_mode` host var by `converge_boot_mode.yaml` — inventory
intent in, router state out.

| Mode | Kernel from | Root on | Purpose |
| --- | --- | --- | --- |
| `local_kernel` | local disk (`/boot/Image` via u-boot `localcmd`) | NVMe/eMMC | **Fleet default / steady state.** Kernel owned by the package manager; `apt upgrade` + reboot updates it, kernel and `/lib/modules` stay coherent. |
| `nfs` | rb5009 TFTP | NFS export on netboot server | **Reimaging.** Known-good network rootfs to wipe/provision local disks. |
| `sd` | rb5009 TFTP | microSD (`LABEL=armbi_root`) | Bring-up / recovery of a freshly-flashed SD card. |
| `local` | rb5009 TFTP | local disk (`LABEL=armbi_root_local`) | **Transitional only** — network kernel, local root. Used mid-provisioning. |

**Do not leave a board in a TFTP-kernel mode (`nfs`/`sd`/`local`)
long-term.** `system_update` advances on-disk `/lib/modules` while the
TFTP-staged kernel stays frozen → module loads fail (`BTF: -22`; first
real-world symptom is k3s/containerd unable to mount overlayfs).

## Playbooks / job templates

| Playbook | AAP job template | What it does |
| --- | --- | --- |
| `converge_boot_mode.yaml` | `armbian_converge_boot_mode` | Render + upload the pin for the host's declared `armbian_boot_mode`. Router state only. |
| `set_boot_mode.yaml` | `armbian_set_boot_mode` | Same, with `-e armbian_boot_mode=…` override (e.g. flip to `nfs` for a reimage). |
| `cycle_board.yaml` | `armbian_cycle_board` | PoE cold-cycle + wait-for-SSH + verify the mounted root matches the declared mode. Run after a converge. |
| `stage_netboot_assets.yaml` | `armbian_stage_netboot` | (Re)provision NFS rootfs + per-host TFTP kernel artifacts on the netboot server (TrueNAS). Feeds the `nfs`/`sd`/`local` paths. |
| `provision_local_disk.yaml` | `armbian_provision_local_disk` | Wipe + provision a board's local disk from the running rootfs. |
| `reprovision_to_local.yaml` | `armbian_reprovision_local` | **Full reimage chain:** NFS boot → disk provision → converge back to `local_kernel`. |
| `build_and_publish.yaml` | — | Drive `armbian/build` on the docker builder to produce custom PXE-first images. |
| `bootstrap.yaml` | — | First-boot bootstrap of a board. |

## Common workflows

- **Reimage a board:** `reprovision_to_local.yaml` (flip to `nfs` →
  boot network rootfs → wipe/provision local disk → converge back to
  `local_kernel`). Ensure `stage_netboot_assets.yaml` has staged current
  NFS + TFTP artifacts first.
- **Update in place:** steady-state boards use the repo's normal
  `system-update` / `system-reboot` path — the kernel is local, so no
  netboot step. Use `cycle_board.yaml` for the reboot so the mode gets
  verified afterward.
- **Change boot mode:** edit `armbian_boot_mode` in inventory (or use
  `set_boot_mode.yaml -e …` for a one-off) → `converge_boot_mode.yaml`
  → `cycle_board.yaml`.
- **Build a new image:** `build_and_publish.yaml` on the builder
  (`vscode.igou.systems`). Fleet standard is edge branch + mainline
  u-boot v2026.04 with a baked-in `localcmd`.

## Targeting gotcha

All `armbian_*` job templates take **`target_hosts=<inventory
hostname>`**, *not* `ansible_limit`. A mismatched targeting var silently
matches no hosts and the play reports success having done nothing.
(`k3s_install_cluster`, which touches the same boards via the `rk8s`
group, is the inverse — it takes `ansible_limit`.)

## Per-board quirks (see the doc for the full list)

- **cm3588-nas-01** — `local_kernel` with an NVMe→eMMC `localcmd`
  fallback chain (root currently on eMMC while a dead NVMe batch awaits
  replacement). **Not PoE-powered** (crs328 ether13 reports
  short-circuit; runs off its DC supply), so `cycle_board.yaml` cannot
  actually power-cycle it — it reports success while the board never
  reboots. Use a warm reboot (rtl8125 WoL udev rule keeps the PHY up for
  u-boot) or pull power physically.
- U-boot `localcmd` is baked at image-build time (`persist_via: hook`,
  edge branch only) for most boards; changing the chain means rebuilding
  the image and re-flashing the board's u-boot carrier.

## Verify what actually booted

- Pin's active mode: `ssh -p 3480 rb5009 ':put [/file get
  "sbc/pxelinux.cfg/01-<mac>" contents]'`
- TFTP hit counters (`/ip tftp print` on rb5009) — a `local_kernel` boot
  must **not** increment the board's `vmlinuz` row.
- On the board: `findmnt /` shows a local block device for `local*`
  modes; `modprobe overlay` succeeds ⇒ kernel/module coherence.
