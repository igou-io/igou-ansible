# Netboot operations runbook

How to add, change, remove, and troubleshoot netboot.xyz menu entries, host pins,
ISOs, kickstart/cloud-init seeds, OpenShift PXE assets, and rb5009 iPXE binaries.

This is operations-focused. For architecture see the design specs:
- `docs/superpowers/specs/2026-05-08-netboot-asset-management-design.md`
- `docs/superpowers/specs/2026-05-08-netboot-binaries-build-design.md`
- `docs/superpowers/specs/2026-05-06-openshift-add-node-iso-netboot-design.md`
- `docs/superpowers/specs/2026-05-09-test-netboot-pxe-headless-design.md`

---

## What's where

| Concern | Playbook | Owns on TrueNAS / rb5009 |
|---|---|---|
| Menu, host pins, kickstart, cloud-init, ISO/kernel/local entries | `playbooks/netboot/deploy_assets.yml` | `config/menus/{menu,entries,host,fragments,local}/`, `assets/{kickstart,cloud-init,iso,local,cache}/` |
| OpenShift add-node iPXE script + boot artifacts | `playbooks/openshift/add_node_iso.yml` | `config/menus/host/MAC-<hex>.ipxe` (per worker), `assets/<cluster>-add-node/` |
| OpenShift agent-install (initial cluster) | `playbooks/openshift/agent-install/deploy_pxe_assets.yml` | `assets/ocp/` (separate; predates deploy_assets) |
| Custom iPXE binaries on rb5009 | `playbooks/routeros/deploy_netboot_binaries.yml` | `flash:/netboot/` on rb5009 + DHCP option-43/66/67 routing |
| Headless PXE smoke test | `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml` | (no writes; spins KubeVirt VMs that PXE-boot and asserts dnsmasq logs) |

**Container:** `ix-netbootxyz-netbootxyz-1` on TrueNAS (TrueCharts deployment).

**HTTP root** = filesystem `/assets/` (nginx serves `/assets/*` at `http://10.10.45.242/*`).

**TFTP root** = filesystem `/config/menus/`. The container's built-in dnsmasq serves
all `.ipxe` files via TFTP.

**`menu.ipxe` is TFTP-only.** iPXE binaries from rb5009 chainload to
`tftp://10.10.45.242/menu.ipxe`. Per-host chaining inside `menu.ipxe` also goes
via TFTP (`chain ${pxetftp}/host/MAC-${mac:hexraw}.ipxe`).

---

## End-to-end boot path (for context)

1. PXE client DHCP-discovers from rb5009 → option-66/67 routes it to a
   `netboot.xyz.{kpxe,efi}` binary on rb5009 TFTP.
2. Binary chainloads `tftp://10.10.45.242/menu.ipxe` (the netbootxyz container).
3. `menu.ipxe`'s top-level header attempts `chain host/MAC-<hexraw>.ipxe`.
   - **Pinned MAC** → file exists → run the per-host script.
   - **Unpinned MAC** → dnsmasq returns `not found` → menu falls through to
     `stock-menu.ipxe` (the upstream netboot.xyz menu).

---

## Common invocations

```bash
# Full deploy (preflight + render + push + fetch + local + verify)
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i igou-inventory/inventory.yaml

# Local-only render — see what menu/host/entries WOULD be generated, no TrueNAS contact
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i igou-inventory/inventory.yaml \
  --tags render -e netbootxyz_host=localhost --check
# Then inspect .cache/netboot-menus/ on the controller.

# Menu touch-up (no slow downloads, no local artifacts)
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i igou-inventory/inventory.yaml \
  --tags render,push,verify

# Just the verify pass (HTTP probes against the live container)
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i igou-inventory/inventory.yaml \
  --tags verify

# Fetch ISOs only (run after adding a kind: iso entry)
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i igou-inventory/inventory.yaml \
  --tags fetch
```

---

## Adding a menu entry

Menu entries live in `igou-inventory/group_vars/all/netboot.yml` under
`netboot_entries`. Four kinds are supported. Each entry needs `id`, `name`, and
`kind`; required fields per kind are listed below.

### `kind: kernel` — chain to upstream installer URLs

Most distro netboot installers publish `vmlinuz` + `initrd` URLs you can iPXE
directly into. No local caching by default.

```yaml
netboot_entries:
  - id: debian-12-preseed
    name: "Debian 12 (preseed)"
    kind: kernel
    kernel: https://deb.debian.org/debian/dists/bookworm/main/installer-amd64/current/images/netboot/debian-installer/amd64/linux
    initrd: https://deb.debian.org/debian/dists/bookworm/main/installer-amd64/current/images/netboot/debian-installer/amd64/initrd.gz
    cmdline: "auto=true url={{ '{{' }} netboot_self {{ '}}' }}/kickstart/debian.preseed"
    kickstart: debian.preseed   # path under playbooks/netboot/files/kickstart/
    cache: false                # set true to download to /assets/cache/<id>/
```

Then `--tags render,push,verify`. If `cache: true`, also `--tags fetch`.

### `kind: iso` — sanboot a pinned ISO from local cache

Downloads upstream once, sha256-checked, served from `/assets/iso/<id>.iso`.

```yaml
netboot_entries:
  - id: talos-1.9
    name: "Talos 1.9"
    kind: iso
    url: https://github.com/siderolabs/talos/releases/download/v1.9.0/metal-amd64.iso
    sha256: 1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd1234abcd
```

`--tags fetch` does the download (slow; ~minutes per ISO). On re-runs, sha256
match makes it a no-op. Then `--tags render,push,verify`.

### `kind: chainload` — chain to a remote `.ipxe` URL

Useful when someone else maintains the iPXE script (e.g. a custom OS vendor).

```yaml
netboot_entries:
  - id: rocky-9-ks
    name: "Rocky 9 (kickstart)"
    kind: chainload
    url: "{{ '{{' }} netboot_self {{ '}}' }}/kickstart/rocky9.ipxe"
```

Render-only; no download.

### `kind: local` — ship a control-node-built kernel/initrd

For custom rescue images / locally compiled kernels.

```yaml
netboot_entries:
  - id: custom-rescue
    name: "Custom rescue kernel"
    kind: local
    kernel_src: "{{ '{{' }} playbook_dir {{ '}}' }}/../../.cache/rescue/vmlinuz"
    initrd_src: "{{ '{{' }} playbook_dir {{ '}}' }}/../../.cache/rescue/initrd.img"
    cmdline: "console=ttyS0 rescue"
```

`--tags local` copies the artifacts; `--tags render,push,verify` wires the menu.

---

## Adding a per-host pin

Pins live in the same file under `netboot_host_pins`. Three forms are supported;
each entry needs `mac`, optionally `hostname`.

### Form 1 — pin to an existing entry id

```yaml
netboot_host_pins:
  - mac: aa:bb:cc:dd:ee:ff
    hostname: worker-01.igou.systems   # optional
    entry: talos-1.9                    # must match a netboot_entries[].id
```

The host fetches the per-host file → it `chain`s to the named entry.

### Form 2 — inline kernel/initrd

```yaml
netboot_host_pins:
  - mac: 11:22:33:44:55:66
    kernel: https://example.com/vmlinuz
    initrd: https://example.com/initrd.img
    cmdline: "console=ttyS0 root=/dev/sda1"
```

### Form 3 — free-form `.ipxe` fragment

```yaml
netboot_host_pins:
  - mac: 22:33:44:55:66:77
    fragment: |
      #!ipxe
      kernel http://example.com/some-kernel
      initrd http://example.com/some-initrd
      imgargs custom-cmdline
      boot
```

After any change: `--tags render,push,verify`.

---

## Adding a hand-written `.ipxe` fragment

For escape-hatch content the declarative schema can't express:

```bash
$EDITOR playbooks/netboot/files/fragments/<my-fragment>.ipxe
```

Anything dropped in there is auto-included on the next render and shows up in
the menu's "Custom" submenu. `--tags render,push,verify`.

---

## Adding a kickstart config or cloud-init seed

Drop the file into the appropriate directory:

```bash
$EDITOR playbooks/netboot/files/kickstart/<distro>.cfg
$EDITOR playbooks/netboot/files/cloud-init/<role>.yaml
```

Both directories are synced to `/assets/{kickstart,cloud-init}/` (no `delete=true`
— extras stay until removed manually). Reference them from menu entries / host
pins via:

- Kickstart: `inst.ks=http://10.10.45.242/kickstart/<distro>.cfg`
- Cloud-init: `cloud-config-url=http://10.10.45.242/cloud-init/<role>.yaml`

After dropping the file: `--tags push,verify`.

---

## Removing entries / pins

- Entry: delete the dict from `netboot_entries`. Next `--tags render,push,verify`
  cleans up `entries/<id>.ipxe` (synchronize `--delete=true`).
- Pin: delete from `netboot_host_pins`. Next `--tags render,push,verify` removes
  the rendered `host/MAC-<hex>.ipxe`.
- Hand-written fragment: `git rm playbooks/netboot/files/fragments/<file>.ipxe`,
  then `--tags render,push,verify`.
- Kickstart / cloud-init file: must be removed manually on TrueNAS — the sync
  is `delete: false` for those directories. Run on the netbootxyz host:
  `docker exec ix-netbootxyz-netbootxyz-1 rm /assets/kickstart/<file>.cfg`.

---

## OpenShift: add a worker via PXE

Two concerns are separated now:
- **Boot artifacts** (kernel/initrd/rootfs, baked with a token that rotates)
  are written by `playbooks/openshift/add_node_iso.yml` into
  `/assets/<cluster>-add-node/`. Re-run when tokens expire.
- **Per-host iPXE script** (the `host/MAC-<hex>.ipxe` chain target) is owned
  by `deploy_assets.yml`, rendered from inventory's `netboot_host_pins`. The
  pin's URL paths are stable across artifact refreshes; render once.

### Initial setup

```bash
# 1. Add the worker to inventory (igou-inventory/inventory.yaml):
#    openshift_workers_<cluster>:
#      hosts:
#        worker-XX.igou.systems:
#          openshift_add_node_mac: aa:bb:cc:dd:ee:ff
#          # optional: openshift_add_node_network_config (nmstate) for static IPs

# 2. Add a netboot_host_pins entry for the worker MAC in
#    igou-inventory/group_vars/all/netboot.yml. Either a direct-boot
#    fragment that points at the OCP add-node URLs, or an interactive
#    menu (e.g. the hpg5 example). The fragment should chain to:
#      http://10.10.45.242/<cluster>-add-node/node.<arch>-vmlinuz
#      http://10.10.45.242/<cluster>-add-node/node.<arch>-initrd.img
#      http://10.10.45.242/<cluster>-add-node/node.<arch>-rootfs.img

# 3. Set on the cluster host (igou-inventory/host_vars/<cluster>.yml):
#    openshift_add_node_arch: x86_64
#    openshift_add_node_boot_artifacts_base_url: http://10.10.45.242/<cluster>-add-node/

# 4. Deploy the per-host pin (one time, for this MAC):
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i igou-inventory/inventory.yaml --tags render,push,verify

# 5. Generate the boot artifacts:
export KUBECONFIG=~/.kube/<cluster>-config
ansible-playbook playbooks/openshift/add_node_iso.yml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=<cluster>

# 6. PXE-boot the worker (BMC, IPMI, manual reboot — whatever you do today).

# 7. Optionally watch the cluster see it:
ansible-playbook playbooks/openshift/add_node_iso.yml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=<cluster> --tags monitor

# 8. Approve any pending CSRs:
oc get csr
oc adm certificate approve <name>
```

### Subsequent runs (token refresh, cluster reinstall, etc.)

Just re-run step 5. The pin file in `host/` doesn't need updating — the
URLs it references stay the same; only the artifacts behind those URLs
rotate.

```bash
ansible-playbook playbooks/openshift/add_node_iso.yml \
  -i igou-inventory/inventory.yaml -e target_cluster=<cluster>
```

`add_node_iso.yml` is intentionally not idempotent: `oc adm node-image
create --pxe` always re-bakes the artifacts, so `changed=2` (or so) on
every run is normal.

### Cleanup of older flat-path / managed-by-add-node files

`add_node_iso.yml` retains a cleanup pass that removes any leftover files
from older versions of itself: anything matching `*-add-node-*.ipxe` or
`MAC-*.ipxe` files that contain the legacy `# Managed by playbooks/
openshift/add_node_iso.yml` header. Idempotent (no-op once the
deployment has migrated). Pin files maintained by deploy_assets.yml are
NOT touched (they have a different managed-by header).

---

## rb5009: refresh iPXE binaries

`netboot.xyz.kpxe` (BIOS) and `netboot.xyz.efi` (UEFI x64) are built from
upstream netboot.xyz with our internal chainload URL embedded.

```bash
# Build, upload, wire DHCP, verify — full pipeline:
ansible-playbook playbooks/routeros/deploy_netboot_binaries.yml \
  -i igou-inventory/inventory.yaml

# Just rebuild and re-upload (skip DHCP wiring):
ansible-playbook playbooks/routeros/deploy_netboot_binaries.yml \
  -i igou-inventory/inventory.yaml \
  --tags build,upload,verify
```

Rebuild when:
- The chainload target URL changes (rare — `tftp://10.10.45.242/menu.ipxe`
  is the long-standing target).
- Upstream netboot.xyz publishes a security fix to the iPXE bundle.
- A new arch is added (e.g. `netboot.xyz.arm64.efi`).

---

## Smoke test the deployment

After any change, run the headless smoke test to confirm:
- nginx is reachable.
- Smoke pin files are deployed correctly (substring check in deployed body).
- Pinned MACs see dnsmasq `sent` for their per-host file.
- Random MACs see dnsmasq `not_found` and fall through to `stock-menu.ipxe`.

```bash
# Serial mode (~12 min; one VM at a time):
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml

# Parallel mode (~6 min; all 4 VMs at once):
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e 'pxe_test_parallel=true'
```

Expect `failed=0`. The 4 default cases exercise BIOS + UEFI × pinned + random.

---

## Verify live state matches inventory

```bash
# 1. Full HTTP probe pass (preflight + verify)
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i igou-inventory/inventory.yaml \
  --tags verify

# 2. Inspect what's actually on disk in the container
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.shell \
  -a 'docker exec ix-netbootxyz-netbootxyz-1 ls -la /config/menus/ /config/menus/host/ /assets/' -b

# 3. Tail dnsmasq-tftp during a real PXE attempt
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.command \
  -a 'docker logs --tail=80 ix-netbootxyz-netbootxyz-1' -b | grep dnsmasq-tftp
```

---

## Troubleshooting

### "My VM didn't get its custom pin"

1. Did dnsmasq see the request?
   ```bash
   ansible truenas ... 'docker logs --tail=200 ix-netbootxyz-netbootxyz-1' -b | grep dnsmasq-tftp | tail -10
   ```
   Expect `sent /config/menus/host/MAC-<hex>.ipxe to <ip>` for the VM's IP.
   - `not found` → the host file doesn't exist on disk. Re-run
     `--tags render,push,verify` and check `_pxe_have_custom_menu` was true.
   - No log line at all → iPXE never reached the netbootxyz container. Check
     rb5009 DHCP/TFTP.

2. Confirm the file body is correct:
   ```bash
   ansible truenas ... 'docker exec ix-netbootxyz-netbootxyz-1 cat /config/menus/host/MAC-<hex>.ipxe' -b
   ```

3. Check that the VM's MAC matches what's in inventory: `${mac:hexraw}` is
   lowercase no-separator (e.g. `f8:b4:6a:ab:55:c7` → `f8b46aab55c7`).

### "Stock netbootxyz menu shows up where I expected my menu"

`menu.ipxe` only renders custom content when `netboot_entries` or
`netboot_host_pins` or `playbooks/netboot/files/fragments/*.ipxe` is non-empty.
With all three empty, the stock menu is preserved as-is and `menu.ipxe` is
left untouched. Add at least one of the three to override.

### "iPXE chain fails on a worker"

1. Rb5009 binary actually delivered? Check `--tags verify` output of
   `playbooks/routeros/deploy_netboot_binaries.yml` (TFTP hit-counter delta).
2. iPXE binary chainloads to the right URL? `xxd` the binary on rb5009 and grep
   for `tftp://10.10.45.242/menu.ipxe`. If wrong, rebuild
   (`deploy_netboot_binaries.yml --tags build,upload`).
3. Is the DHCP `next-server` / `boot-file-name` matcher table targeting the
   right binary for the client's option-93? `/ip dhcp-server matcher print`
   on rb5009 (or check via `--tags verify`).

### "Asset URL returns 404 even though the file is in `/assets/`"

nginx in this container serves `/assets/*` at `http://10.10.45.242/*` (NOT
`http://10.10.45.242/assets/*`). Drop the `/assets/` prefix from URLs.

### "I changed an entry but the deploy says no changes"

`--tags render` writes to `.cache/netboot-menus/` on the controller; `--tags push`
syncs that cache to TrueNAS. If you only ran `--tags push`, no re-render
happened. Default invocation runs all stages; explicit tag-driven runs need
`render,push` (and `verify` for sanity).

### "I want to roll back"

The container preserves the upstream stock menu as `stock-menu.ipxe` the first
time it's overwritten. To roll back to "stock netbootxyz only":

```bash
# 1. Empty the inventory (or delete the relevant entries/pins)
# 2. Re-run deploy
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i igou-inventory/inventory.yaml --tags render,push,verify
# 3. Manually restore the stock menu on TrueNAS:
ansible truenas ... 'docker exec ix-netbootxyz-netbootxyz-1 cp /config/menus/stock-menu.ipxe /config/menus/menu.ipxe' -b
```

---

## Path reference

### TrueNAS bind-mount layout

```
/mnt/ssd/containers/netbootxyz/        # netbootxyz_root
├── config/menus/
│   ├── menu.ipxe                       # rendered (or stock if no custom content)
│   ├── stock-menu.ipxe                 # preserved upstream menu
│   ├── entries/<id>.ipxe               # one per netboot_entries
│   ├── host/MAC-<hex>.ipxe             # one per pin
│   ├── fragments/<file>.ipxe           # auto-included custom .ipxe
│   ├── local/                          # mirror of menu/entries/host/fragments
│   └── <upstream menus>.ipxe           # rhcos.ipxe, ubuntu.ipxe, …  (untouched)
└── assets/
    ├── kickstart/<distro>.cfg          # synced from playbooks/netboot/files/kickstart/
    ├── cloud-init/<role>.yaml          # synced from playbooks/netboot/files/cloud-init/
    ├── iso/<id>.iso                    # one per kind: iso entry (sha256-checked)
    ├── local/<id>/{vmlinuz,initrd}     # one dir per kind: local entry
    ├── cache/<id>/{vmlinuz,initrd}     # opt-in cache for kind: kernel
    ├── ocp/                            # OpenShift agent-install (separate playbook)
    └── <cluster>-add-node/             # OpenShift add-node (separate playbook)
```

### URL paths (all served at `http://10.10.45.242`)

| URL | Filesystem |
|---|---|
| `/` | `/assets/` (autoindex) |
| `/kickstart/<f>` | `/assets/kickstart/<f>` |
| `/cloud-init/<f>` | `/assets/cloud-init/<f>` |
| `/iso/<id>.iso` | `/assets/iso/<id>.iso` |
| `/<cluster>-add-node/<f>` | `/assets/<cluster>-add-node/<f>` |

### TFTP paths (all served by container's dnsmasq at `10.10.45.242:69`)

| TFTP path | Filesystem |
|---|---|
| `/menu.ipxe` | `/config/menus/menu.ipxe` |
| `/host/MAC-<hex>.ipxe` | `/config/menus/host/MAC-<hex>.ipxe` |
| `/entries/<id>.ipxe` | `/config/menus/entries/<id>.ipxe` |
| `/stock-menu.ipxe` | `/config/menus/stock-menu.ipxe` |
| `/fragments/<file>.ipxe` | `/config/menus/fragments/<file>.ipxe` |
