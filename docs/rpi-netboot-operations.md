# Raspberry Pi netboot operations runbook

How to enroll, netboot, mode-flip, reimage, and troubleshoot the Raspberry Pi
fleet (Pi 4 today, Pi 5 spec'd). Everything here was bench-verified on
2026-07-17 against `rpi-bench-pi4` (Pi 4 4GB on crs328 ether1): diskless
NFS-root boot by default, automated net↔sd flips, and diskless reimage of the
Pi's own SD card.

This is the **native EEPROM netboot** path — no U-Boot, no pxelinux. The ARM
SBC (Rockchip) fleet uses per-MAC pxelinux pins instead: see
`docs/armbian-boot-modes.md`. The x86/iPXE path is `docs/netboot-operations.md`.

Architecture and decisions log: igou-ansible#335 (design spec; **D2 was
amended by the bench — see the DHCP section**). Domain contract and inventory
scaffolding reference: `playbooks/rpi_netboot/README.md`. Bench evidence and
open follow-ups: the 2026-07-17 report comment on #335.

---

## What's where

| Concern | Owner | Where |
|---|---|---|
| DHCP (leases + the mandatory option 66) and TFTP | rb5009 | `/ip/dhcp-server/lease`, `/ip/dhcp-server/option`, `/ip tftp` rows → `flash:/rpi/...` |
| Shared firmware/kernel/DTB set (per model+build) | rb5009 flash | `rpi/shared/<model>/<build_id>/{start4.elf,fixup4.dat,kernel8.img,*.dtb}` — uploaded once per build, size-gated |
| Per-Pi boot pins (the mode switch) | rb5009 flash | `rpi/per-host/<hostname>/{config.txt,cmdline.txt}` + `/ip tftp` rows keyed `<serial>/<file>` |
| Per-Pi NFS rootfs | TrueNAS | `/mnt/ssd/netboot/rootfs/<hostname>` (NFSv3, shared export with the armbian fleet) |
| Image download/stage cache | TrueNAS | `/mnt/ssd/armbian/cache/` (`downloads/`, `rpi-tftp/`) |
| Published images | rpi-builder → TrueNAS | `/srv/images/rpi/<config>/<build_id>/` on the builder; exported to `/mnt/ssd/public/boot-files/images/rpi/...` + `manifest.json` by `publish_to_netboot.yml` |
| Access-port config (PVID 9 + edge) | crs328 | `host_vars/crs328.igou.systems.yml` |

**Boot flow (Pi 4):** power-on → SPI EEPROM bootloader walks `BOOT_ORDER`
nibbles LSB-first (`0xf12` = NET, then SD, then restart-loop) → NET does DHCP
→ TFTP-fetches a fixed file set from a **serial-named prefix directory**
(`TFTP_PREFIX=0`, e.g. `1fb5d725/`) → `cmdline.txt` selects the root. Kernel
and firmware come from TFTP in **both** managed modes; only `root=` differs.

| `rpi_boot_mode` | meaning |
|---|---|
| `net` | diskless — `root=/dev/nfs nfsroot=<truenas>:<rootfs>/<hostname>,vers=3` |
| `sd` | TFTP kernel, local root (see the `rpi_sd_root` caveat below) |
| `local` | not netboot-managed; asset tracking only (e.g. rpi-builder) |

RasPiOS Trixie `kernel8` has `CONFIG_ROOT_NFS` built in — **no initramfs** is
used or needed in net mode.

---

## Lifecycle playbooks

All in `playbooks/rpi_netboot/`; run with ansible-navigator from the repo root
(AAP job templates are still a follow-up — see Known issues).

```bash
cd igou-ansible
# one-time per Pi, against the SD-booted Pi:
ansible-navigator run playbooks/rpi_netboot/enroll.yml \
  -i ../igou-inventory/inventory.yaml -e target_hosts=<host> --mode stdout
# after each new image build:
ansible-navigator run playbooks/rpi_netboot/publish_to_netboot.yml -i ... --mode stdout
ansible-navigator run playbooks/rpi_netboot/stage_netboot_assets.yml -i ... --mode stdout
# pins only (mode flips):
ansible-navigator run playbooks/rpi_netboot/converge_boot_mode.yml -i ... --mode stdout
# PoE cold-cycle + assert booted root matches declared mode:
ansible-navigator run playbooks/rpi_netboot/cycle_pi.yml \
  -i ... -e target_hosts=<host> --mode stdout
```

Scope multi-tier plays with `-e rpi_group=<group>`, **never `--limit`** — but
see Known issues before scoping to a subset: the router prune currently
computes "active builds" from the scoped group only.

---

## Bring-up runbook: adding a Pi to the fleet

1. **Image + flash.** `playbooks/rpi-image/build_from_release.yml` (the
   boot-proven pipeline; pins in `group_vars/rpi_image_builders.yml`), then
   `flash.yml` with the card in the builder's USB reader. Images are key-only
   (fleet key); bake `-e rpi_image_user1passhash` if you want console login.
2. **Inventory.** Add the host under `rpis`→`rpi4` (contract in the domain
   README: `rpi_board_mac`, `rpi_serial: ""`, `rpi_boot_mode`, PoE
   coordinates). Add the rb5009 MAC-only static lease (outside
   `10.10.9.24/29`, no client-id) + DNS A record, **attach
   `dhcp-option: rpi-tftp-66` to the lease** (mandatory — next section), and
   the crs328 access port (PVID 9, upsmonitor `frame-types`, `edge: "yes"` —
   STP convergence can eat the firmware's DHCP window). Apply via the
   `routeros_configure` AAP template, sequentially per device.
3. **Enroll** (SD-booted Pi reachable over SSH): converges the EEPROM
   (`BOOT_ORDER=0xf12`, `BOOT_UART=1`, `NET_BOOT_MAX_RETRIES=3`,
   `TFTP_PREFIX=0`), reboots through the flash, re-verifies every managed
   key, and prints the captured serial. Put `rpi_serial` into inventory —
   the TFTP prefix is derived from it.
4. **Stage + converge + cycle.** `stage_netboot_assets.yml` →
   `converge_boot_mode.yml` → `cycle_pi.yml`. Go signal: nonzero rb5009
   `/ip tftp` hits on `<serial>/kernel8.img` and `findmnt / = nfs`.

The SD card can stay in as the `0xf12` fallback (recommended — a failed NET
attempt then still boots something debuggable) or be pulled for fully
diskless operation.

---

## DHCP: what the Pi firmware actually requires (D2 amendment)

The spec's original D2 said the Pi ignores `boot-file-name` and needs zero
DHCP option changes because VLAN9 already carries `next-server`. **That is
wrong**, bench-proven by packet capture: the Pi 4 firmware DHCP-Discovers
with vendor class `PXEClient:Arch:00000` and **rejects any offer** that does
not carry option 43 (`Raspberry Pi Boot` PXE TLV) **or option 66 as an ASCII
dotted-quad**. It never falls back to bare `siaddr`/next-server — it just
loops Discover until the NET attempt budget expires and falls to SD. The
symptom is maddening: DHCP replies on the wire, zero TFTP requests.

The fleet fix is option 66, attached **per-lease** so no other VLAN9 netboot
client sees it:

```yaml
# host_vars/rb5009.igou.systems.yml
/ip/dhcp-server/option:
  data:
    - code: 66
      comment: RPi EEPROM netboot TFTP server (ASCII dotted-quad, hex-pinned)
      name: rpi-tftp-66
      value: '0x31302e31302e392e31'   # = ASCII "10.10.9.1" — see trap below
/ip/dhcp-server/lease:
  data:
    - address: 10.10.9.33
      dhcp-option: rpi-tftp-66        # every netboot Pi's lease gets this
      mac-address: ...
```

**⚠ RouterOS encoding trap (bench-proven twice):** RouterOS canonicalizes ANY
IP-shaped option value — even the CLI quoted-string form `'10.10.9.1'` — to
**4 binary bytes** on the wire (`raw-value 0a0a0901`), which the Pi firmware
also rejects. The string form *looks* right in the config and then regresses
on the next declarative apply. Only the explicit hex form survives
reconciliation. After any apply that touches DHCP options, verify the wire
bytes:

```bash
curl -sk -u <user>:<pass> https://rb5009.igou.systems/rest/ip/dhcp-server/option \
  | jq -r '.[] | select(.name=="rpi-tftp-66") | .["raw-value"]'
# must be 31302e31302e392e31 (ASCII), NOT 0a0a0901 (binary)
```

(The armbian option 66 is binary on the wire too — U-Boot tolerates that;
the Pi firmware does not.)

Do **not** add option 43 alongside option 66 — the spec's warning about the
Pi firmware's 43/66 precedence bugs still stands. Do not set option 66 on the
whole VLAN9 network; per-lease keeps the blast radius at one MAC.

---

## The ASCII rule

Two separate bench failures had the same root cause: **Pi firmware config
parsers stop at the first non-ASCII byte and silently discard the rest of the
file.** A UTF-8 em-dash in a template header:

- truncated the flashed **EEPROM bootconf** to nothing (Pi silently ran
  bootloader defaults — no BOOT_ORDER, no netboot), and
- made netboot fetch **config.txt** and then abort the attempt before
  `start4.elf`.

Both templates are now pure ASCII and guarded by asserts (`rpi_eeprom`
refuses to stage a non-ASCII or key-incomplete render; `rpi_boot_render`
refuses to emit non-ASCII `config.txt`/`cmdline.txt`). The rule for
operators: **anything that ends up in front of a Pi firmware parser —
`rpi_eeprom_config` values, `rpi_boot_render_config_extra`,
`rpi_boot_render_cmdline_extra` — must be pure ASCII.** The guards will fail
the play loudly rather than flash a broken config, but don't make them work.

Related: `rpi-eeprom-config --apply` performs **no validation** of the config
it embeds. The guards in the roles are the only safety net.

---

## Mode flips and diskless SD reimage

Verified sequences (each step ends with `cycle_pi.yml` asserting the booted
root matches the requested mode):

```bash
# net -> sd (see rpi_sd_root caveat below)
ansible-navigator run playbooks/rpi_netboot/converge_boot_mode.yml -i ... \
  -e rpi_boot_mode=sd -e rpi_sd_root=/dev/mmcblk0p2 --mode stdout
ansible-navigator run playbooks/rpi_netboot/cycle_pi.yml -i ... \
  -e target_hosts=<host> -e rpi_boot_mode=sd -e rpi_sd_root=/dev/mmcblk0p2 --mode stdout

# sd -> net (inventory declares net, so no overrides)
ansible-navigator run playbooks/rpi_netboot/converge_boot_mode.yml -i ... --mode stdout
ansible-navigator run playbooks/rpi_netboot/cycle_pi.yml -i ... -e target_hosts=<host> --mode stdout
```

**Diskless SD reimage** — while the Pi runs from NFS its SD is idle, so it can
rewrite its own card, then flip to sd to boot the fresh image:

```bash
ssh igou@<pi> 'set -o pipefail
  findmnt -n -o FSTYPE / | grep -q nfs || { echo NOT-NFS; exit 1; }
  curl -fsSk --retry 3 -o /tmp/img.xz "<publish-url>/igou-pi-lite.img.xz" || exit 1
  sha256sum -c <(printf "%s  /tmp/img.xz\n" "<sha256 from SHA256SUMS>") || exit 1
  xz -dc /tmp/img.xz | sudo dd of=/dev/mmcblk0 bs=4M conv=fsync status=none || exit 1
  sync; sudo blockdev --rereadpt /dev/mmcblk0; rm /tmp/img.xz'
```

**⚠ Pipeline lesson (this bit us):** `curl | xz -dc | dd` without
`set -o pipefail` reports success when curl dies instantly — `dd` happily
writes zero bytes and exits 0. Always pipefail **and** checksum the download
before `dd`. Verify the write physically: the fresh image's `p2` is small
(~2.3G) until RasPiOS firstboot expands it, so `lsblk` before/after is a
truth test. A marker file planted on the old filesystem is another.

**⚠ HTTP source caveat:** `public.igou.systems` (the nginx on TrueNAS vlan45)
is currently unreliable for >500 MB transfers (truncated one staging download
at 585/635 MB; failed the Pi's download outright — suspected `send_timeout`
under ssd-pool load). Retries usually get staging through; for the on-Pi
reimage the reliable fallback is streaming from the builder over SSH:
`ssh igou@rpi-builder 'cat /srv/images/rpi/<config>/latest/<config>.img.xz' > /tmp/img.xz`.

**⚠ `rpi_sd_root` caveat:** managed sd mode defaults to
`root=LABEL=igou_root`, which `build_from_release` images do **not** carry
(official RasPiOS base labels the root `rootfs`). Until the default or the
pipeline changes, pass `-e rpi_sd_root=/dev/mmcblk0p2` (or a PARTUUID) with
every sd-mode converge/cycle.

---

## Troubleshooting ladder

Work top-down; each step isolates one layer.

1. **Is the declared state on the router?**
   `/ip tftp` rows for `<serial>/{config.txt,cmdline.txt,start4.elf,fixup4.dat,kernel8.img,<dtb>}`
   must exist and point into `rpi/shared/<model>/<build_id>/` +
   `rpi/per-host/<hostname>/`. `stage_netboot_assets.yml` /
   `converge_boot_mode.yml` re-assert this (plumbing check).
2. **Hit counters** (`/ip tftp` `hits`) are per-served-request and are the
   cheapest boot telemetry. Baseline before a cycle, read after. Interpretation
   matrix from the bench:
   - **zero hits + Pi falls to SD** → the firmware never got a usable DHCP
     offer. Check the lease carries `rpi-tftp-66` and the option's
     `raw-value` is ASCII (`31302e...`) — the Discover-loop failure mode.
   - **config.txt/cmdline.txt hits but nothing bigger** → the attempt died
     early; historically the non-ASCII config.txt bug. Fetch the served pin
     yourself and inspect it byte-wise.
   - **full sequence hits but wrong root** → kernel booted; the problem is
     `cmdline.txt` content or the NFS export.
3. **Serve the rows manually** from any VLAN9 host — proves RouterOS TFTP and
   the row mapping independent of the firmware:
   `curl -s tftp://10.10.9.1/<serial>/config.txt`.
4. **Packet capture on rb5009** — the tool that actually cracked the bench:
   ```
   /tool/sniffer/set filter-mac-address=<pi-mac>/FF:FF:FF:FF:FF:FF file-name=rpi-debug file-limit=4096KiB
   /tool/sniffer/start   ... cycle the Pi ...   /tool/sniffer/stop
   scp -P 3480 <admin>@rb5009.igou.systems:rpi-debug ./rpi-debug.pcap
   tcpdump -r rpi-debug.pcap -nn -vvv
   ```
   MAC-filtering (not IP) catches the DHCP broadcasts. Look for: Discover
   without a following Request = offer rejected (option 66 problem); decode
   the Offer's options to see exactly what the firmware was given.
   Delete the capture file and reset the sniffer filter afterwards.
5. **Probe rows**: temporary `/ip tftp` rows for candidate filenames (bare
   names, optional probes like `recover4.elf`) pointing at a nonexistent
   real-filename — their hit counters enumerate what the firmware requested
   without serving anything. Remove them afterwards.
6. **On-Pi diagnostics** (SD-fallback booted): `sudo rpi-eeprom-config`
   (verify the golden config landed — a header-only file means an ASCII
   truncation), `vcgencmd bootloader_version`. `sudo vclog --msg` only covers
   the start.elf stage of the *current* boot — the bootloader's NET-attempt
   trace goes to serial only (`BOOT_UART=1`, GPIO14/15, 115200).
7. **PoE cycle by hand** when the Pi is wedged mid-retry:
   `/interface/ethernet set ether1 poe-out=off` … `poe-out=auto-on` on the
   Pi's switch (or `cycle_pi.yml`, which also asserts the outcome).

**Recovery:** nothing in this stack can hard-brick a Pi 4. Worst cases and
exits: wedged NET retries → PoE cycle (SD fallback boots); broken EEPROM
config → boots bootloader defaults, re-run `enroll.yml`; interrupted EEPROM
flash → staged update re-applies on next power-on, or Raspberry Pi Imager's
"Bootloader restore" rescue SD. `0xf12` always keeps SD as the escape hatch —
keep a card in bench/fleet Pis unless you have a reason not to.

---

## Known issues and caveats (state as of 2026-07-17)

Tracked in the #338 review comment and the #335 bench report; the ones with
operational impact:

- **Scoped prune deletes sibling builds** (`stage_netboot_assets.yml`):
  `_active_builds` only considers the scoped `rpi_group`, but the prune sweeps
  the whole `shared/<model>/` tree. Until fixed, run stage with the default
  full `rpis` scope.
- **Top-level `rpi` flash dir** is not created by the playbooks (created
  manually on rb5009 during the bench). First stage against a fresh router
  needs `/file add name=rpi type=directory` or the code fix.
- **`custom` TFTP prefix mode is single-host only** — two Pis sharing a
  custom prefix create ambiguous duplicate rows pointing at different NFS
  roots. Stick with the default `serial` mode. Related: a 32-char
  `rpi_tftp_prefix_str` renders a 33-char EEPROM value (off-by-one vs the
  firmware limit); keep custom prefixes ≤31 chars.
- **Stage only powered-off (or non-NFS-booted) Pis**: `rpi_rootfs`'s mount
  guard checks the *server's* mounts and cannot see a remote Pi using the
  export — `force_refresh`/new-build rsync `--delete` will rewrite a running
  Pi's root out from under it.
- **`rpi_eeprom` enrollment force-flashes the distro's newest bootloader
  image** (`rpi-eeprom-config --apply` → `rpi-eeprom-update -d -i -f`): on a
  Pi whose live bootloader is newer than the unpinned `rpi-eeprom` apt
  package, enrollment silently stages a downgrade. Recoverable, but check
  `vcgencmd bootloader_version` vs the package before enrolling
  recently-Imager-flashed units.
- **`publish_to_netboot.yml` fetches the whole image under `become`** →
  slurp fallback holds ~1.3× the image in memory on the builder and the
  controller. Works, but slow; don't run it on a memory-starved controller.
- **NFS root rides the TrueNAS ssd pool**: heavy pool saturation (CDI imports
  are the known offender) stalls diskless Pis (hard-mount hangs, recovers).
  Acceptable for upsmonitor-class nodes; keep `BOOT_UART` + SD fallback on
  anything you care about.
- **No AAP job templates yet** for the rpi_netboot domain (P2) — everything
  above is ansible-navigator from a checkout.

## Bench evidence (what "verified" means above)

2026-07-17, `rpi-bench-pi4` = Pi 4 4GB (ex k8s worker-1), crs328 ether1
PoE, serial `1fb5d725`, bootloader 2026-05-17 (upgraded from 2023-01-11 at
enrollment): cold PoE cycle → NFS root asserted by `cycle_pi.yml` (three
separate green runs); net→sd and sd→net flips asserted; diskless reimage
proven by partition-table change (29.3G→2.3G) + destroyed marker file +
fresh image booting from SD. Four code bugs found at hardware contact and
merged the same day: #409 (ansible-core 2.19 regex escapes), #410 (EEPROM
bootconf ASCII truncation), #411 (fact-less hostvars materialization),
#412 (config.txt ASCII truncation). DHCP option-66 fix: inv#197 + inv#198.
