#!/usr/bin/env bash
# Build an x86 Debian fixture for firmware PXE/NFS/local-root boot tests. This is
# explicitly a virtual-board fixture, not a Rockchip firmware image.
set -euo pipefail
LAB_BOOTSTRAP_KEY=${LAB_BOOTSTRAP_KEY:-$(cat /home/debian/.ssh/authorized_keys)}
image=/srv/netboot/fixture.img
mountpoint=/mnt/lab-fixture
truncate -s 2G "$image"
parted -s "$image" mklabel gpt mkpart primary ext4 1MiB 100%
loop_device=$(losetup --find --show --partscan "$image")
mkdir -p "$mountpoint"
trap 'umount "$mountpoint" 2>/dev/null || true; losetup -d "$loop_device"' EXIT
udevadm settle --timeout=30
mkfs.ext4 -q -F -E nodiscard -L armbi_root "${loop_device}p1"
mount "${loop_device}p1" "$mountpoint"
command -v debootstrap >/dev/null || apt-get install -y debootstrap
snapshot=${LAB_DEBIAN_SNAPSHOT:?}
mirror="https://snapshot.debian.org/archive/debian/$snapshot"
debootstrap --variant=minbase trixie "$mountpoint" "$mirror"
printf 'Acquire::Check-Valid-Until "false";\n' > "$mountpoint/etc/apt/apt.conf.d/99-snapshot"
printf '#!/bin/sh\nexit 101\n' > "$mountpoint/usr/sbin/policy-rc.d"
chmod +x "$mountpoint/usr/sbin/policy-rc.d"
chroot "$mountpoint" apt-get update
DEBIAN_FRONTEND=noninteractive chroot "$mountpoint" apt-get install -y --no-install-recommends \
  systemd-sysv dbus openssh-server python3 python3-apt sudo nfs-common rsync \
  linux-image-amd64 linux-headers-amd64 grub-pc-bin grub2-common initramfs-tools busybox iproute2 \
  parted e2fsprogs xz-utils tftp-hpa curl ca-certificates
mkdir -p "$mountpoint"/{etc/ssh,var/lib/dbus,root,boot/dtb}
printf 'fixture\n' > "$mountpoint/etc/hostname"
printf '127.0.0.1 localhost\n127.0.1.1 fixture\n' > "$mountpoint/etc/hosts"
printf '/dev/vdc1 / ext4 defaults 0 1\n' > "$mountpoint/etc/fstab"
printf 'fixture payload\n' > "$mountpoint/root/payload"
printf 'synthetic-dtb-not-board-firmware\n' > "$mountpoint/boot/dtb/fixture.dtb"
kernel=$(find "$mountpoint/boot" -maxdepth 1 -type f -name 'vmlinuz-*' | sort -V | tail -1)
kernel_release=${kernel##*/vmlinuz-}
printf '%s\n' "$kernel_release" > /srv/netboot/fixture-kernel-release
printf 'virtio_pci\nvirtio_net\nvirtio_blk\next4\nnfs\nnfsv3\n' >> "$mountpoint/etc/initramfs-tools/modules"
# Keep the TFTP initrd small, and DHCP only on the isolated PXE interface.
# BOOT remains selected by root=/dev/nfs versus root=LABEL=... on the PXE pin.
printf 'MODULES=list\nCOMPRESS=xz\nDEVICE=enp2s0\n' > "$mountpoint/etc/initramfs-tools/conf.d/lab-nfs"
chroot "$mountpoint" update-initramfs -u -k "$kernel_release"
mkdir -p "$mountpoint/etc/systemd/network"
printf '[Match]\nName=enp1s0\n[Network]\nDHCP=ipv4\n' > "$mountpoint/etc/systemd/network/10-management.network"
printf '[Match]\nName=enp2s0\n[Network]\nDHCP=ipv4\nKeepConfiguration=yes\n[DHCPv4]\nUseRoutes=no\nUseDNS=no\n' > "$mountpoint/etc/systemd/network/20-lab.network"
systemctl --root="$mountpoint" enable systemd-networkd ssh
mkdir -p "$mountpoint/etc/default/grub.d"
cat > "$mountpoint/etc/default/grub.d/99-netboot-lab.cfg" <<'EOF'
GRUB_TIMEOUT=0
GRUB_CMDLINE_LINUX="console=ttyS0,115200 lab.boot=local_kernel"
GRUB_DISABLE_OS_PROBER=true
EOF
chroot "$mountpoint" useradd -m -s /bin/bash debian
chroot "$mountpoint" usermod -p '*' debian
mkdir -p "$mountpoint/home/debian/.ssh"
printf '%s\n' "$LAB_BOOTSTRAP_KEY" > "$mountpoint/home/debian/.ssh/authorized_keys"
chmod 700 "$mountpoint/home/debian/.ssh"
chmod 600 "$mountpoint/home/debian/.ssh/authorized_keys"
chroot "$mountpoint" chown -R debian:debian /home/debian/.ssh
printf 'debian ALL=(ALL) NOPASSWD: ALL\n' > "$mountpoint/etc/sudoers.d/debian"
chmod 440 "$mountpoint/etc/sudoers.d/debian"
if [[ -n ${LAB_BOOTSTRAP_PASSWORD:-} ]]; then
  printf 'root:%s\n' "$LAB_BOOTSTRAP_PASSWORD" | chroot "$mountpoint" chpasswd
  sed -i '/^#\?PasswordAuthentication/d; /^#\?PermitRootLogin/d' "$mountpoint/etc/ssh/sshd_config"
  sed -i '1iPasswordAuthentication yes\nPermitRootLogin yes' "$mountpoint/etc/ssh/sshd_config"
fi
rm -f "$mountpoint"/etc/ssh/ssh_host_*
: > "$mountpoint/etc/machine-id"
: > "$mountpoint/var/lib/dbus/machine-id"
cat > "$mountpoint/etc/systemd/system/lab-ssh-hostkeys.service" <<'EOF'
[Unit]
Description=Generate missing fixture SSH host keys
Before=ssh.service
[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
systemctl --root="$mountpoint" enable lab-ssh-hostkeys
cp /run/systemd/resolve/resolv.conf "$mountpoint/etc/resolv.conf"
# The update playbook will move this snapshot's packages and kernel forward.
printf 'deb https://deb.debian.org/debian trixie main\ndeb https://security.debian.org/debian-security trixie-security main\ndeb https://deb.debian.org/debian trixie-updates main\n' > "$mountpoint/etc/apt/sources.list"
rm "$mountpoint/etc/apt/apt.conf.d/99-snapshot"
chroot "$mountpoint" apt-get clean
rm -rf "$mountpoint/var/lib/apt/lists/"*
rm "$mountpoint/usr/sbin/policy-rc.d"
umount "$mountpoint"
losetup -d "$loop_device"
trap - EXIT
xz -T2 -1 -c "$image" > "${image}.xz.part"
mv "${image}.xz.part" "${image}.xz"
