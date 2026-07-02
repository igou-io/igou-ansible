# rpi-image playbooks

Automated Raspberry Pi OS image builds with
[rpi-image-gen](https://github.com/raspberrypi/rpi-image-gen), replacing
manual Raspberry Pi Imager runs. Images come out with the `igou` user
(fleet SSH key, passwordless sudo, key-only sshd) baked in.

State lives in `igou-inventory`: the `rpi_image_builders` group
(rpi-builder.igou.systems — bare-metal Pi 4 8GB, RPi OS Lite Trixie
arm64 on USB SSD) and `group_vars/rpi_image_builders.yml` (pinned
rpi-image-gen release, image definition, publish layout).

## Playbooks

| Playbook | Purpose |
|---|---|
| `converge.yml` | Owns the build host: prerequisites, pinned rpi-image-gen checkout + `install_deps.sh`, workspace/publish dirs. Idempotent; run after changing the pin. |
| `build.yml` | Render config → build (async, flock-serialised, logged) → verify → publish. |

```sh
ansible-playbook playbooks/rpi-image/converge.yml -i ../igou-inventory/inventory.yaml
ansible-playbook playbooks/rpi-image/build.yml -i ../igou-inventory/inventory.yaml
```

## Verify stage

The produced image is loop-mounted on the builder and the build fails
unless all of these hold:

- `authorized_keys` for the image user contains the fleet public key
- `/etc/sudoers.d/010_rpi-nopasswd` grants the user NOPASSWD sudo
- sshd drop-in disables password authentication
- ssh.service is enabled

## Publish layout

```
/srv/images/rpi/<config>/<build_id>/<config>.img.xz + SHA256SUMS
/srv/images/rpi/<config>/latest -> newest build_id
```

Retention: newest `rpi_image_publish_retain` builds per config. Flash
with `xzcat <config>.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync`.

## Notes

- rpi-image-gen images are **not** Raspberry Pi OS proper: there is no
  raspberrypi-sys-mods firstboot, so Imager customization / `custom.toml`
  does not apply to them. Bake identity at build time
  (`-e rpi_image_hostname=...`) or lean on MAC-pinned DHCP.
- Images are key-only by default. Pass `-e rpi_image_user1passhash='...'`
  (e.g. from 1Password, `openssl passwd -6`) to also bake a password —
  needed if you want console login on a device with no network.
- Disaster recovery for the builder itself: reflash the stock Lite image
  (or a pipeline-built one), re-pin via DHCP happens automatically, then
  run `converge.yml`.
- AAP wiring (converge → build workflow + monthly schedule) is a
  follow-up; both playbooks are AAP-shaped (group-targeted, no
  interactive input).
