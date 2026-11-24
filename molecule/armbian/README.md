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

## What it cannot prove

- U-Boot, its `localcmd` chain and SPI environment: a BIOS guest boots
  PXELINUX from the router, and `localboot 0` falls through to a small GRUB
  "carrier" disk (written in `prepare.yml`) that boots `/vmlinuz` from the
  disk labelled `armbi_root_local`.
- Rockchip kernels and device trees: the x86 board has no dtb.
- PoE: `transport/power_cycle_poe.yml` is not exercised; the boards use
  `tasks/power_cycle_vm.yml` (VM off and on).
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
- Egress from the cluster to `dl.armbian.com` (about 1 GiB per run) and
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
2026-11-24).

## Topology

| Guest | Role |
| --- | --- |
| `router` | CHR: DHCP with static leases, TFTP from a 1 GiB flash disk, NAT and one port forward per board's sshd. API through a NodePort. It first boots the provisioner's Debian disk, from which prepare downloads CHR and writes it to the `chr` disk. |
| `server` | Debian 13: NFS export, nginx HTTP root (staging directory and the PXELINUX loader), image cache on a data disk. |
| `board-a-vm`, `board-b-vm` | BIOS, one NIC on the netboot network, a carrier disk and a local disk. They first boot the provisioner's Debian disk (static address), from which prepare writes the carrier; prepare then leaves only NIC, carrier and local disk in the boot order, so it runs once per create. |

The inventory hosts `board-a` and `board-b` are the boards the playbooks
target; they are reached through the router's forwards, separate from their
VMs' provisioner entries.
