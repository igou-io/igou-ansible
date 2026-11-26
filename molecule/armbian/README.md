# armbian

Runs the real `playbooks/armbian/reimage.yaml`, unmodified, against two
KubeVirt "boards" behind a RouterOS CHR, and checks the boot lifecycle end to
end.

## What it proves

- `stage_netboot_assets.yaml`: an upstream Armbian image becomes a per-board
  NFS rootfs on the netboot server; the router pulls kernel and initrd over
  HTTP and records their checksums; repeated stages fetch nothing new.
- `pin_boot_mode.yaml`: each board gets its own per-MAC pxelinux pin through
  the RouterOS API, and the TFTP directory rows serve it.
- The `nfs` boot of a fresh image, `bootstrap.yaml` (automation user with a
  key, root locked), `provision_local_disks.yaml` (systemd-repart layout with a
  preserved partition) and the final `local_kernel` boot.
- After the reimage each board runs from its local disk with the local disk's
  kernel (GRUB's `BOOT_IMAGE=/vmlinuz`, matching `/boot/vmlinuz-*`), and the
  router log shows its pin but no kernel or initrd request during that boot.
- A second reimage of board-a keeps the preserved partition's data, replaces
  the root filesystem, and does not reboot board-b.
- That second reimage runs the way production reimages its control plane
  (cm3588-nas-01): through `transport/power_cycle_warm_reboot.yml`, a reboot
  over SSH. Before it, `tasks/assert_dkms_built.yml` passes with a module
  built for the kernel and refuses one that is not; the reimage then starts
  from the refused state, so its first reboot (into `nfs`) proves the guard
  never blocks a reimage. `dkms` is a two-line stand-in, not the package.
- `reimage.yaml`'s k3s cleanup: board-b is the stand-in control plane
  (`rk8s_control_plane`). Its own reimage skips the cleanup, board-a's first
  reimage finds no k3s there, and the second asks a stand-in `k3s` for
  exactly board-a's node and node-password secret.
- A kernel whose checksum changed on the router is fetched again, and three
  stages and pins leave one TFTP row per directory and one checksum sidecar,
  equal to the staged kernel.
- Each board's final boot is served its own pin, not prepare's fallback
  `pxelinux.cfg/default`, and its root is partition 1 of the local disk.

## What it cannot prove

- U-Boot, its `localcmd` chain and SPI environment: a BIOS guest boots
  PXELINUX from the router, and `localboot 0` falls through to a small GRUB
  "carrier" disk (written in `prepare.yml`) that boots `/vmlinuz` from the
  disk labelled `armbi_root_local`.
- Rockchip kernels and device trees: the x86 board has no dtb.
- PoE: `transport/power_cycle_poe.yml` is not exercised; converge uses
  `tasks/power_cycle_vm.yml` (VM off and on).
- Real DKMS, ZFS, k3s or an apt kernel upgrade: the guard and the cleanup
  run against stand-ins, and no pool has to survive the reimage.
- Boot timing: an `nfs` boot is dominated by the CHR's TFTP rate, so the
  waits are the scenario's own and say nothing about a real board's.
- The published-image manifest: prepare publishes the upstream image on the
  netboot server and `board_config_rootfs_src_override` points staging at it;
  the collection's `board_config` scenario covers manifest resolution.
- rb5009 itself: CHR 7.23.8 on the plain API (8728) rather than api-ssl,
  with a free licence that caps what it sends at about 1 Mbit/s.

## Prerequisites

- A Casval lease and the `ocp-ansible-molecule` profile (see
  `../README.md`).
- No shared cluster objects beyond the `molecule` namespace: `create.yml`
  makes the run's Layer2 UserDefinedNetwork (`network.yml`) and waits for
  it, and `destroy.yml` removes it after the guests.
- `sshpass` on the controller (the bootstrap logs in with the image's root
  password).
- Egress from the cluster to `fi.mirror.armbian.de` (about 1 GiB, once, in prepare) and
  `download.mikrotik.com` (the pinned CHR image, 45 MiB).

## Run

```bash
use ocp-ansible-molecule
oc whoami --show-server
molecule test -s armbian
```

While developing, keep the guests and run `molecule create`, `prepare`,
`converge` and `verify` separately: every `nfs` boot pulls the kernel and
initrd through the CHR's TFTP at about 1 Mbit/s and takes about 14 minutes.

A full run reimages three times and takes about 70 minutes (67 on Casval,
2026-11-26).

## Topology

| Guest | Role |
| --- | --- |
| `router` | CHR: DHCP with static leases, TFTP from a 1 GiB flash disk, NAT and one port forward per board's sshd. API through a NodePort. It first boots the provisioner's Debian disk, from which prepare downloads CHR and writes it to the `chr` disk. |
| `server` | Debian 13: NFS export, nginx HTTP root (staging directory and the PXELINUX loader), image cache on a data disk. |
| `board-a-vm`, `board-b-vm` | BIOS, one NIC on the netboot network, a carrier disk and a local disk. They first boot the provisioner's Debian disk (static address), from which prepare writes the carrier; prepare then leaves only NIC, carrier and local disk in the boot order, so it runs once per create. |

The inventory hosts `board-a` and `board-b` are the boards the playbooks
target; they are reached through the router's forwards, separate from their
VMs' provisioner entries.
