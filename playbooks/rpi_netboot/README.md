# rpi_netboot — Raspberry Pi netboot lifecycle

Netboots the Pi fleet (Pi 4 today, Pi 5 ready) in the same shape as the
armbian `boards` fleet: declared boot state in inventory, per-host boot
pins on the rb5009 TFTP server, per-host NFS rootfs on the
`netboot_server`, PoE cycle + verify. Design spec: igou-ansible#335.

Unlike the Rockchip boards there is no U-Boot/pxelinux — the Pi's SPI
EEPROM bootloader natively TFTP-fetches a fixed file set
(firmware + `config.txt` + `cmdline.txt` + DTB + kernel) from a
**prefix directory**, and `cmdline.txt` is the per-host pin (the analog
of `pxelinux.cfg/01-<mac>`). Kernel and firmware come from TFTP in both
managed modes; only `root=` differs:

| `rpi_boot_mode` | meaning |
|---|---|
| `net`   | diskless — NFS root from `<rpi_nfs_rootfs_path>/<hostname>` |
| `sd`    | TFTP kernel, local root (`rpi_sd_root`, default `LABEL=igou_root`) |
| `local` | not netboot-managed (asset tracking only, e.g. rpi-builder) |

## Configurable TFTP prefix

`rpi_tftp_prefix_mode` selects how the firmware names its TFTP prefix
directory. `enroll.yml` derives the EEPROM `TFTP_PREFIX` key and the
router playbooks derive their `/ip tftp` `req-filename` keys from the
same variable, so firmware and router cannot disagree. Settable at any
inventory scope (fleet group_vars down to per-host).

| mode | EEPROM | request path | when to use |
|---|---|---|---|
| `serial` (default) | `TFTP_PREFIX=0` | `<8-hex-serial>/…` | firmware default; serial captured by enroll |
| `mac` | `TFTP_PREFIX=2` | `<aa-bb-cc-dd-ee-ff>/…` | matches the fleet's MAC-keyed identity; known before first boot |
| `custom` | `TFTP_PREFIX=1` + `TFTP_PREFIX_STR` | `<rpi_tftp_prefix_str>/…` | shared prefix for interchangeable nodes (max 32 chars) |

## Playbooks (lifecycle order)

| playbook | what it does |
|---|---|
| `enroll.yml` | ON the SD-booted Pi: converge EEPROM (BOOT_ORDER, TFTP prefix mode, UART), capture serial/MAC, print the inventory stanza. The one imperative step netboot can't avoid. |
| `publish_to_netboot.yml` | Copy the newest rpi-image-gen build from the builder's `/srv/images/rpi` tree to the netboot server's HTTP assets export + write `manifest.json`. |
| `stage_netboot_assets.yml` | Per-Pi NFS rootfs + boot-partition split on the netboot server; upload the shared `<model>/<build_id>` firmware/kernel/dtb set to rb5009 flash and register per-Pi rows. Scope with `-e rpi_group=<group>`, **not `--limit`**. |
| `converge_boot_mode.yml` | Render + upload only the per-Pi pins (`config.txt`/`cmdline.txt`). Router state only. |
| `cycle_pi.yml` | PoE cold-cycle + verify the booted rootfs matches `rpi_boot_mode`. |

Roles: `rpi_boot_render`, `rpi_rootfs_provision`, `rpi_eeprom`
(repo-local, pure functions with argument_specs).

## Inventory contract (lands in igou-inventory)

```yaml
# inventory.yaml
rpis:
  children:
    rpi4:
      hosts:
        upsmonitor.igou.systems:
          rpi_board_mac: "DC:A6:32:42:52:AA"
          rpi_serial: "a1b2c3d4"        # captured by enroll.yml
          rpi_boot_mode: net
          rpi_poe_switch: crs328.igou.systems
          rpi_poe_port: ether5
    rpi5: {}

# group_vars/rpis.yml — fleet topology + EEPROM golden config
rpi_router: rb5009.igou.systems
rpi_netboot_server_ip: 10.10.9.213
rpi_nfs_rootfs_path: /mnt/ssd/netboot/rootfs
rpi_image_cache: /mnt/ssd/armbian/cache        # shared download/stage cache
rpi_assets_export: /mnt/ssd/public/boot-files
rpi_assets_base_url: https://public.igou.systems/boot-files
rpi_tftp_flash_dir: rpi
rpi_tftp_cache_dir: /tmp/rpi-tftp
rpi_tftp_prefix_mode: serial
rpi_eeprom_config:
  BOOT_ORDER: "0xf12"      # NET first, SD fallback, loop
  BOOT_UART: "1"
  NET_BOOT_MAX_RETRIES: "3"
  TFTP_IP: ""              # '' = drop key; router next-server is the TFTP server

# group_vars/rpi4.yml
rpi_model_config:
  firmware_files: [start4.elf, fixup4.dat]
  kernel: kernel8.img
  dtb: bcm2711-rpi-4-b.dtb

# group_vars/rpi5.yml
rpi_model_config:
  firmware_files: []       # start.elf is embedded in the Pi 5 bootloader
  kernel: kernel_2712.img
  dtb: bcm2712-rpi-5-b.dtb
```

Also per Pi (existing patterns): rb5009 MAC-only static DHCP lease
outside `10.10.9.24/29` + DNS record; crs328 PoE port PVID 9 with
`edge=yes` (STP convergence can eat the firmware's 45 s DHCP window).
No DHCP option changes — the Pi ignores `boot-file-name` and uses the
existing VLAN9 `next-server 10.10.9.1`.

## Router flash layout

```
rpi/shared/<model>/<build_id>/{start4.elf,fixup4.dat,kernel8.img,*.dtb}   # once per build
rpi/per-host/<hostname>/{config.txt,cmdline.txt}                          # per Pi (~KB)
/ip tftp rows: <prefix>/<file> -> the paths above
```

Stale rows are retargeted (`transport/retarget_tftp_row.yml`) and
superseded build dirs pruned as part of staging.

## Not yet validated (P0 gate from the spec)

The Pi firmware's TFTP client (blksize 1024/tsize negotiation) has not
been bench-tested against RouterOS TFTP. Validate with one Pi before
fleet rollout; the fallback is a dnsmasq-tftp container on the netboot
server plus `TFTP_IP` in `rpi_eeprom_config` — no other change.
