# AGENTS.md — ARM SBC / Armbian fleet

Playbooks for the `boards` fleet. They are thin: the logic lives in the
roles of the `david_igou.armbian` collection, RouterOS transport lives in
`transport/`, and image userpatch bodies live in `templates/userpatches/`.

> **Authoritative reference:** [Managing the Armbian SBC Fleet](https://github.com/igou-io/igou-docs/blob/main/hardware/Managing%20the%20Armbian%20SBC%20Fleet.md).
> If this file, inventory comments or role docs disagree with it, that doc
> wins. Read it before touching board kernels, boot modes or TFTP assets.

## Inventory is the source of truth

Boards are Rockchip RK3588/RK3588S SBCs in `igou-inventory` group
`boards`. Router pins, TFTP assets and disk layouts are rendered from
inventory; never hand-edit them, change inventory and re-run.

- Hardware and build layers: `board_config_{family,model,host}` and
  `board_config_build_{defaults,family,model,host}`, resolved by the
  `board_config` role. The family layer is `group_vars/rk3588_family.yml`.
- Per board: `armbian_board_mac`, `armbian_boot_mode`, `armbian_local_disks`,
  `armbian_power_cycle_tasks`, `armbian_poe_port`. Fleet defaults are in
  `group_vars/boards.yml`.
- Userpatch entries are `{dest, template}`; `template` is a path under
  `templates/` here, rendered in the board's context.

## Targeting

Every playbook requires `-e target_hosts=<board, group or pattern>`; a
missing value fails before anything runs. Board plays intersect it with
`boards`. Builder, netboot-server and router plays derive their board list
from the same value. `reimage.yaml` requires exactly one board. AAP
templates carry `target_hosts: changeme`.

(`k3s_install_cluster`, which reaches the same boards through `rk8s`,
takes `ansible_limit` instead.)

## Playbooks / AAP job templates

| Playbook | Job template | What it does | Optional extra vars |
| --- | --- | --- | --- |
| `pin_boot_mode.yaml` | `armbian_pin_boot_mode` | Render the board's pxelinux pin (`pxelinux_render`) and upload it to rb5009. Router state only. | `armbian_boot_mode` (one-off override) |
| `cycle_board.yaml` | `armbian_cycle_board` | Power-cycle through the board's hook (`board_boot_wait`) and assert the booted root matches `armbian_boot_mode` (`board_boot_verify`). | |
| `stage_netboot_assets.yaml` | `armbian_stage_netboot` | Provision the per-board NFS rootfs and TFTP kernel/initrd/dtb on TrueNAS from the published image (`rootfs_provision`), then copy the TFTP files to rb5009. | `armbian_rootfs_force` |
| `build_and_publish.yaml` | `armbian_build_publish` | Build images with `armbian/build` on the builder (`image_build`) and publish them to `images/<board>/`. | `armbian_build_force` |
| `bootstrap.yaml` | `armbian_firstboot` | On a fresh image, log in as root, create the automation user and lock root (`bootstrap_armbian`). | |
| `provision_local_disks.yaml` | `armbian_provision_local_disks` | On an NFS-booted board, write each `armbian_local_disks` entry from the running rootfs (`disk_provision`). WIPES them. | |
| `reimage.yaml` | `armbian_reimage` | One board, two boots: stage (forced) → pin `nfs` → boot (sshd only) → bootstrap → provision disks → pin the declared mode → boot and verify → delete the stale k3s node object and node-password secret. | |

## Power cycling

`armbian_power_cycle_tasks` selects the hook per board: a path under this
directory, passed to `board_boot_wait` as `{{ playbook_dir }}/<path>` in
`cycle_board.yaml` (the only place it is resolved; `reimage.yaml` imports
it for both boots).

- `transport/power_cycle_poe.yml` (fleet default): PoE off, drain, on via
  `armbian_poe_switch`/`armbian_poe_port`.
- `transport/power_cycle_warm_reboot.yml`: `systemctl reboot` over SSH,
  then wait for sshd to stop. Needs the board up and reachable.

## Boot modes

| Mode | Kernel from | Root on | Purpose |
| --- | --- | --- | --- |
| `local_kernel` | local disk (U-Boot `localcmd`) | NVMe/eMMC | **Fleet default.** apt owns the kernel; kernel and `/lib/modules` stay coherent. |
| `nfs` | rb5009 TFTP | NFS on TrueNAS | Reimaging. |
| `sd` | rb5009 TFTP | microSD (`LABEL=armbi_root`) | Bring-up / recovery. |
| `local` | rb5009 TFTP | local disk (`LABEL=armbi_root_local`) | Debugging a local root with a network kernel. |

Do not leave a board in a TFTP-kernel mode long-term: `system_update`
advances `/lib/modules` while the TFTP kernel stays frozen, and module
loads fail (first symptom: k3s cannot mount overlayfs).

The `localcmd` chain is baked into U-Boot at image build time by
`templates/userpatches/config/sources/families/rockchip-rk3588.conf.j2`
(edge branch only) from `local_kernel.storage` or the ordered
`local_kernel.storage_candidates`. Changing it means rebuilding the image
and re-flashing the board's U-Boot carrier.

## Per-board notes

- **cm3588-nas-01** (`rk8s` control plane): root is permanently eMMC; the
  four NVMe are the ZFS pool backing the k3s data-dir, so never add them to
  `armbian_local_disks` (`prepare-k3s-datastore.yml` owns them).
  `local_kernel.verify_match: ^/dev/mmcblk0` makes `cycle_board.yaml`
  assert the eMMC root. Not PoE-powered: it uses the warm-reboot hook
  (the rtl8125 WoL udev rule in the image keeps the PHY up for PXE), so an
  unreachable board needs a physical power pull. After a reimage, run AAP
  `k3s_prepare_datastore` (the pool imports; only bare drives need
  `zfs_pool_wipe=true`) before `k3s_install_cluster`. `reimage.yaml` skips
  the k3s cleanup for the control plane itself.

## Verify what actually booted

- Active pin: `ssh -p 3480 rb5009 ':put [/file get "sbc/pxelinux.cfg/01-<mac>" contents]'`
- TFTP hit counters (`/ip tftp print` on rb5009): a `local_kernel` boot
  must not increment the board's `vmlinuz` row.
- On the board: `findmnt /` shows a local block device for `local*` modes;
  `modprobe overlay` succeeds when kernel and modules agree.
