# Armbian SBC boot modes

How the `boards` fleet (ARM SBCs, `igou-inventory` group `boards`)
boots, what each mode is for, and which playbooks/AAP job templates
operate on them. This is the authoritative reference — if inventory
comments or role docs disagree, fix them to match this file.

## The boot chain

Every board's u-boot DHCPs and fetches a **per-MAC pxelinux pin** from
rb5009 over TFTP (`sbc/pxelinux.cfg/01-<mac>` on the router's flash).
The pin's `default <label>` line selects the active boot mode. The pin
is rendered from inventory intent (`armbian_boot_mode` host var) by
`playbooks/armbian/converge_boot_mode.yaml` — the router pin is
**derived state**; inventory is the source of truth.

## The modes

| Label | Kernel from | Root on | Purpose |
| --- | --- | --- | --- |
| `local_kernel` | **Local disk** (`/boot/Image` via u-boot `localcmd`) | NVMe | **Steady state.** Kernel is owned by the package manager — `apt upgrade` + reboot updates it iteratively, kernel and `/lib/modules` always match. |
| `nfs` | rb5009 TFTP | NFS export on netboot server | **Reimaging.** Boot a known-good network rootfs to wipe/provision the board's local disks (`provision_local_disk`, `reprovision_to_local`). |
| `sd` | rb5009 TFTP | microSD (`LABEL=armbi_root`) | Bring-up / recovery for a freshly dd'd SD card. |
| `local` | rb5009 TFTP | local disk (`LABEL=armbi_root_local`) | **Transitional only** — network kernel with local root. Used mid-provisioning, and as cm3588-nas-01's interim mode (below). |

**The fleet default is `local_kernel`.** Modes that load the kernel
from TFTP (`nfs`/`sd`/`local`) are for provisioning and recovery; a
board left in them long-term will hit kernel/module drift: the daily
`system_update` advances `/lib/modules` on disk while the TFTP-staged
kernel stays frozen, and module loads start failing with
`BTF: -22` (first symptom in practice: k3s/containerd can't mount
overlayfs). See igou-io/igou-ansible#220.

## How local_kernel works

The pin's `local_kernel` label is `localboot 0`, which makes u-boot run
its `localcmd` env var: scan a storage device, `ext4load` Image/uInitrd/
dtb from its `/boot`, `booti`. `localcmd` is provisioned per board via
`armbian_local_kernel.persist_via` (host var):

- `hook` — baked into the image's u-boot default environment at build
  time (`__999_local_kernel_bake` userpatch, edge branch only).
  Changing the chain means rebuilding the image and re-flashing the
  board's u-boot carrier.
- `spi` — written to SPI-flash u-boot env (`fw_setenv`) by the
  collection's `persist_uboot_env.yml`; requires
  `uboot_env.fw_env_config` in the model layer (rock-5b).

The storage target is configurable per board in the
`armbian_board_config` layers (family → model → host): either a single
`local_kernel.{storage,storage_scan}` pair (family default
`nvme 0:1`), or an **ordered fallback list**
`local_kernel.storage_candidates` — one `if ext4load …; booti …; fi`
block per candidate; `booti` never returns on success, so a later
candidate runs only when an earlier one fails. cm3588-nas uses
`[nvme 0:1, mmc 0:1]`: it prefers NVMe but falls back to its eMMC
(where root lives while the dead NVMe batch awaits replacement —
once NVMe is provisioned, the same u-boot prefers it automatically).
Boards with non-NVMe fallbacks also set `local_kernel.verify_match`
(e.g. `^/dev/(nvme|mmcblk0)`) for `board_boot_verify`.

Because the kernel/initrd/dtb come from `/boot` on the rootfs, kernel
updates are just `apt upgrade` + reboot. The per-host TFTP kernel
files (`sbc/armbian/<fqdn>/{vmlinuz,initrd.img,board.dtb}`) are then
only used when a board is deliberately flipped to `nfs`/`sd`/`local`.

## Operations (AAP job templates ↔ playbooks)

| Job template | Playbook | What it does |
| --- | --- | --- |
| `armbian_converge_boot_mode` | `playbooks/armbian/converge_boot_mode.yaml` | Render + upload the pin for the host's declared `armbian_boot_mode`. Router state only. |
| `armbian_set_boot_mode` | `playbooks/armbian/set_boot_mode.yaml` | Same, with `-e armbian_boot_mode=nfs\|sd\|local\|local_kernel` override (e.g. flip to `nfs` for a reimage). |
| `armbian_cycle_board` | `playbooks/armbian/cycle_board.yaml` | PoE cold-cycle + wait for SSH + verify rootfs matches the declared mode. Run after a converge. |
| `armbian_stage_netboot` | `playbooks/armbian/stage_netboot_assets.yaml` | (Re)provision NFS rootfs + TFTP kernel artifacts on the netboot server — feeds the `nfs`/`sd`/`local` paths. |
| `armbian_provision_local_disk` | `playbooks/armbian/provision_local_disk.yaml` | Wipe + provision a board's local disk from the running rootfs. |
| `armbian_reprovision_local` | `playbooks/armbian/reprovision_to_local.yaml` | Full reimage chain: NFS boot → disk provision → converge back to local. |
| `k3s_install_cluster` | `playbooks/kubernetes/install-k3s-cluster.yml` | k3s on the `rk8s` group (`-e ansible_limit=rk8s`). Boards must be in `local_kernel` (or otherwise have loadable modules) for containerd overlayfs. |

All armbian templates take `target_hosts=<inventory hostname>` —
**not** `ansible_limit` (and `k3s_install_cluster` takes
`ansible_limit` — not `host`). Mismatched targeting vars silently
match no hosts.

## Per-board notes

- **cm3588-nas-01** is on `local_kernel` with the NVMe→eMMC fallback
  chain (see above). Its u-boot lives on the SD carrier (eMMC has no
  room for the 8MiB ITB offset); re-baking the chain means dd'ing
  fresh `idbloader.img`/`u-boot.itb` to `/dev/mmcblk1` sectors
  64/16384. **It is NOT PoE-powered** (crs328 ether13 reports
  `short-circuit`, board runs from its DC supply), so
  `armbian_cycle_board` cannot actually power-cycle it — the playbook
  reports success while the board never reboots. Use a warm reboot
  (the rtl8125 WoL udev rule keeps the PHY up so u-boot can fetch the
  pin) or pull power physically.
- **rock-5b-01** runs the Armbian *vendor* kernel
  (`linux-image-vendor-rk35xx`) for the rk-llama NPU workload
  (igou-kubernetes `apps/rk-llama/`). In `local_kernel` mode this is
  self-contained — `/boot/Image` symlinks to the vendor kernel. Do
  not "fix" it back to the edge kernel.

## Verifying what actually booted

- `# Active mode:` header in the pin:
  `ssh -p 3480 rb5009 ':put [/file get "sbc/pxelinux.cfg/01-<mac>" contents]'`
- TFTP hit counters — a `local_kernel` boot must NOT increment the
  board's `vmlinuz` row: `/ip tftp print` on rb5009.
- On the board: `boot_mode[local*]` ⇒ `findmnt /` shows a local block
  device; kernel/module coherence ⇒ `modprobe overlay` succeeds.
- `/proc/cmdline` of a `local_kernel` boot comes from `localcmd`
  (`root=LABEL=armbi_root_local`, no `armbianEnv.txt` extraargs).
