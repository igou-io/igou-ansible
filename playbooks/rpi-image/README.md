# rpi-image playbooks

Automated Raspberry Pi OS image builds. Images come out with the `igou`
user (fleet SSH key, passwordless sudo, key-only sshd) baked in.

**Two build pipelines exist; `build_from_release.yml` is the one that
produces boot-proven images.** `build.yml` (rpi-image-gen) is retained
for when igou-ansible#305 is root-caused — its images have never booted
on a real Pi 4 (ACT LED dark, zero frames; rootfs content verified
fine, boot partition suspect).

State lives in `igou-inventory`: the `rpi_image_builders` group
(rpi-builder.igou.systems — bare-metal Pi 4 8GB, RPi OS Lite Trixie
arm64 on USB SSD) and `group_vars/rpi_image_builders.yml` (pinned
release/rpi-image-gen versions, image definition, publish layout).

## Playbooks

| Playbook | Purpose |
|---|---|
| `build_from_release.yml` | **Recommended.** Fetch the pinned official RasPiOS Lite arm64 release → inject the fleet primitives directly (native arm64 chroot on the builder: user, key, key-only sshd, NOPASSWD sudo, hostname, ssh enabled, firstboot user-wizard disabled, optional extra packages) → verify (content **and** boot plausibility) → publish. Starts from an image that provably boots. |
| `converge.yml` | Owns the build host: prerequisites, pinned rpi-image-gen checkout + `install_deps.sh`, workspace/publish dirs. Idempotent; run after changing the pin. |
| `build.yml` | rpi-image-gen pipeline: render config → build (async, flock-serialised, logged) → verify → publish. ⚠ Produces images that do not boot on Pi 4 — igou-ansible#305. |
| `flash.yml` | Write a published image to a USB device plugged into the builder. Dry-run unless `-e flash_confirm=true`; structurally cannot target the builder's boot SSD or backup micro SD (device-name contract + live boot-disk / mount / transport guards). |

```sh
ansible-playbook playbooks/rpi-image/build_from_release.yml -i ../igou-inventory/inventory.yaml
ansible-playbook playbooks/rpi-image/flash.yml -i ../igou-inventory/inventory.yaml \
  -e flash_device=/dev/sdb -e flash_confirm=true
# per-host image:
ansible-playbook playbooks/rpi-image/build_from_release.yml -i ../igou-inventory/inventory.yaml \
  -e rpi_image_config=upsmonitor -e rpi_image_hostname=upsmonitor
```

`build_from_release.yml` needs release pins
(`group_vars/rpi_image_builders.yml`) — either a single flat
`rpi_image_release_src`(+`rpi_image_release_sha256`), or a per-config
map so one inventory carries both architectures:

```yaml
rpi_image_release_sources:
  igou-pi-lite:          # arm64 — Zero 2 W / Pi 3 / Pi 4 (incl. netboot Pi 4s)
    src: https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-06-19/2026-06-18-raspios-trixie-arm64-lite.img.xz
    sha256: "<from the published .img.xz.sha256>"
  igou-pi-lite-armhf:    # armhf — the ONLY option for original Zero / Zero W
    src: https://downloads.raspberrypi.com/raspios_lite_armhf/images/raspios_lite_armhf-2026-06-19/2026-06-18-raspios-trixie-armhf-lite.img.xz
    sha256: "<from the published .img.xz.sha256>"
```

An explicit `-e rpi_image_release_src=...` (URL or builder-local path)
overrides the map. Architecture is derived from the filename
(`-arm64-`/`-armhf-`), override with `rpi_image_release_arch`.

### Fleet architecture guidance

- **Original Zero / Zero W (ARMv6)** cannot run the arm64 image —
  RasPiOS **armhf** ("All Raspberry Pi models", Raspberry Pi's own
  ARMv6 port, not Debian armhf) is their only option. armhf builds run
  in a `setarch linux32` chroot on the arm64 builder — native AArch32
  execution, no qemu.
- **Zero 2 W / Pi 3 / Pi 4** use the arm64 config (keeps `kernel8.img`
  aligned with the rpi_netboot model contract). Zero 2 W's 512 MB is
  fine for headless Lite; add zram if a workload gets tight.
- Both arch images multiplex every per-SoC kernel/DTB in the boot
  partition, so one image per arch covers its whole model range; the
  verify stage asserts the per-arch kernel/firmware/DTB set.
- Fleet deploys are wired-Ethernet headless — **no Wi-Fi provisioning
  is baked** by design (and note boot-partition `wpa_supplicant.conf`
  is dead post-Bookworm should that ever change).

## Verify stage

The image is loop-mounted on the builder and the build fails unless all
of these hold:

- `authorized_keys` for the image user contains the fleet public key
- `/etc/sudoers.d/010_rpi-nopasswd` grants the user NOPASSWD sudo
- sshd drop-in disables password authentication
- ssh.service is enabled

`build_from_release.yml` additionally asserts **boot plausibility** —
the gap that let #305 ship: `start4.elf`/`fixup4.dat`/`kernel8.img`/
`config.txt`/`cmdline.txt`/Pi 4 DTB present in the boot partition, and
the `cmdline.txt` `root=PARTUUID` matches the image's actual disk
identifier. (Still content-level: the real gate is booting a Pi.)

## Publish layout

```
/srv/images/rpi/<config>/<build_id>/<config>.img.xz + SHA256SUMS
/srv/images/rpi/<config>/latest -> newest build_id
```

Retention: newest `rpi_image_publish_retain` builds per config. Flash
with `flash.yml` (or `xzcat <config>.img.xz | sudo dd of=/dev/sdX bs=4M
conv=fsync`). The netboot pipeline
(`playbooks/rpi_netboot/publish_to_netboot.yml`) consumes the same
`latest` tree.

## History: how the fleet Pis actually got imaged (2026-07)

Captured here because it previously lived only in session notes:

1. rpi-image-gen images were built and content-verified, but **never
   booted** on the Pi 4 (#305).
2. Raspberry Pi **Imager was not used** — the working manual fallback
   was: flash the stock 2026-06-18 RasPiOS Lite Trixie arm64 release,
   then headless-bootstrap it by dropping two files on the boot
   partition: an empty `ssh` sentinel and a `userconf.txt`
   (`igou:<crypted temp password>`), letting raspberrypi-sys-mods
   firstboot create the user. Hostname stayed `raspberrypi`; identity
   came from the MAC-pinned DHCP lease. That is what upsmonitor ran.
3. `build_from_release.yml` supersedes both: same boot-proven base
   image, but the primitives are baked offline (no firstboot, no temp
   password window) and verified before publish.

## Notes

- rpi-image-gen images are **not** Raspberry Pi OS proper: there is no
  raspberrypi-sys-mods firstboot, so Imager customization / `custom.toml`
  does not apply to them. `build_from_release.yml` images ARE RasPiOS
  proper — firstboot's user wizard is explicitly disabled (a user
  already exists), while the SD resize firstboot is left intact.
- Images are key-only by default. Pass `-e rpi_image_user1passhash='...'`
  (e.g. from 1Password, `openssl passwd -6`) to also bake a password —
  needed if you want console login on a device with no network.
- Disaster recovery for the builder itself: reflash the stock Lite image
  (or a pipeline-built one), re-pin via DHCP happens automatically, then
  run `converge.yml` (only needed for the rpi-image-gen pipeline;
  `build_from_release.yml` needs just xz/losetup/fdisk from the stock
  install).
- AAP wiring (build workflow + monthly schedule) is a follow-up; the
  playbooks are AAP-shaped (group-targeted, no interactive input).
