# armbian

Runs the real `playbooks/armbian/reimage.yaml`, the ZFS datastore and k3s
install playbooks and the nightly update, unmodified, against two KubeVirt
"boards" behind a RouterOS CHR, and checks the fleet's lifecycle end to end:
a control plane and a worker built from zero, then the control plane reimaged
and taken through a kernel upgrade.

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
- board-b stands in for the control plane (cm3588-nas-01): reboots go
  through `transport/power_cycle_warm_reboot.yml`, and
  `kubernetes/prepare-k3s-datastore.yml` builds zfs-dkms for its kernel and
  creates the pool of striped mirrors on four member disks.
  `kubernetes/install-k3s-cluster.yml` then installs k3s, which starts only
  with its data directory mounted from the pool.
- A second reimage of each board. board-a goes first, as a cluster member:
  `reimage.yaml` deletes its node and node-password secret from k3s, and it
  is Ready again after the reinstall only because of that. Its first reimage,
  before k3s exists, skips the cleanup.
- board-b's second reimage, as production reimaged its control plane on
  2026-11-25: the pool is imported from its drives with the cluster's state
  (a ConfigMap written before), k3s is reinstalled on both boards, and
  board-a is not rebooted. On both boards the preserved partition keeps its
  data and the root filesystem is replaced.
- `tasks/assert_dkms_built.yml` against real DKMS: before that reimage the
  zfs module is uninstalled from the kernel's tree and the guard refuses; the
  reimage then starts from the refused state, so its first reboot (into
  `nfs`) proves the guard never blocks a reimage.
- A kernel upgrade on the control plane: `system-update.yaml` installs a
  newer kernel from apt.armbian.com, Armbian's packaging rebuilds zfs for
  it, the guard passes inside the warm-reboot hook, and `cycle_board.yaml`
  boots the new kernel with the pool online, both datasets mounted and both
  nodes Ready.
- A kernel whose checksum changed on the router is fetched again, and four
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
- PoE: `transport/power_cycle_poe.yml` is not exercised; board-a uses
  `tasks/power_cycle_vm.yml` (VM off and on).
- The image's own kernel under ZFS: the upstream image has no kernel headers
  and apt.armbian.com has none for its kernel, so `kernel_with_headers.yml`
  first puts the control plane on an older kernel from apt with its headers.
  Production's image is built with them (`INSTALL_HEADERS`).
- A kernel that zfs-dkms cannot build for: the upgrade stays within 6.18.
- A drained node, workloads, or the fleet runbook's order beyond what the
  playbooks enforce. The node-password secret
  of the control plane is deleted by hand before its reimage, as the runbook
  says.
- The router as the boards' gateway and Armbian's own redirector: the boards
  route through the netboot server, and the router's DNS points
  apt.armbian.com at an nginx on it that forwards to one mirror.
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
- Egress from the cluster to `fi.mirror.armbian.de` (the image, about 1 GiB,
  once, in prepare, and the boards' Armbian packages), `deb.debian.org`,
  `github.com` (k3s), the registries of the k3s system images and
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
During one, SSH to the other board through the same router stalls.

A full run reimages three times and takes about 70 minutes (67 on Casval,
2026-11-26).

## Topology

| Guest | Role |
| --- | --- |
| `router` | CHR: DHCP with static leases, TFTP from a 1 GiB flash disk, NAT and one port forward per board's sshd. API through a NodePort. It first boots the provisioner's Debian disk, from which prepare downloads CHR and writes it to the `chr` disk. |
| `server` | Debian 13: NFS export, nginx HTTP root (staging directory and the PXELINUX loader), image cache on a data disk. Also the boards' gateway to the internet, because the router's licence would slow their package downloads to 1 Mbit/s, and their apt.armbian.com. |
| `board-a-vm`, `board-b-vm` | BIOS, one NIC on the netboot network, a carrier disk and a local disk; board-b also has four 1 GiB pool disks, 8 cores and 4 GiB. They first boot the provisioner's Debian disk (static address), from which prepare writes the carrier; prepare then leaves only NIC, carrier and local disk in the boot order, so it runs once per create. |

The inventory hosts `board-a` and `board-b` are the boards the playbooks
target; they are reached through the router's forwards, separate from their
VMs' provisioner entries.
