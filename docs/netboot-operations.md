# Netboot operations runbook

How to add, change, remove, and troubleshoot netboot menu entries, host pins,
ISOs, kickstart/cloud-init seeds, OpenShift PXE assets, and rb5009 iPXE binaries.

This is operations-focused. For architecture see the design specs:
- `docs/superpowers/specs/2026-05-08-netboot-asset-management-design.md` (initial design — *superseded*)
- `docs/superpowers/specs/2026-05-08-netboot-binaries-build-design.md` (initial design — *binary build still current; chainload target updated*)
- `docs/superpowers/specs/2026-05-06-openshift-add-node-iso-netboot-design.md` (initial design — paths updated)
- `docs/superpowers/specs/2026-05-09-test-netboot-pxe-headless-design.md` (initial design — verification mechanism replaced)
- `docs/superpowers/plans/2026-05-11-netboot-public-nginx.md` (**current architecture** — the netbootxyz container retirement plan)

---

## What's where

| Concern | Playbook | Owns on rb5009 / public nginx |
|---|---|---|
| Menu, kickstart, cloud-init, ISO/kernel/local entries | `playbooks/netboot/deploy_assets.yml` | `/mnt/ssd/public/boot-files/{menu.ipxe,entries,fragments,kickstart,cloud-init,iso,local,cache}/` on truenas |
| Per-host PXE pins (MAC-/HOSTNAME-) | `playbooks/netboot/deploy_assets.yml` (push_pins_rb5009 stage) | `flash:/netboot/per-host/{MAC,HOSTNAME}-*.ipxe` on rb5009 + matching `/ip tftp` rows |
| OpenShift add-node iPXE script + boot artifacts | `playbooks/openshift/add_node_iso.yml` | `/mnt/ssd/public/boot-files/<cluster>-add-node/` |
| OpenShift agent-install (initial cluster) | `playbooks/openshift/agent-install/deploy_pxe_assets.yml` | `/mnt/ssd/public/boot-files/ocp/` |
| Custom iPXE binaries on rb5009 | `playbooks/routeros/deploy_netboot_binaries.yml` | `flash:/netboot/` on rb5009 + DHCP option-43/66/67 routing |
| Headless PXE smoke test | `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml` | (no writes; spins KubeVirt VMs that PXE-boot and asserts rb5009 `/ip tftp` hit counters) |

### Servers

**rb5009** (`10.10.9.1`, RouterOS) — owns DHCP and TFTP for both the bootstrap iPXE binaries and per-host pin files. Files live in `flash:/netboot/`; `/ip tftp` rows map bare filenames (`MAC-<hex>.ipxe`) to those flash paths.

**Public nginx** (`10.10.45.241`, `public.igou.systems`, TrueCharts/compose on truenas vlan45) — owns HTTPS asset serving for everything that isn't a per-host pin: the unpinned-host fallback `menu.ipxe`, entries, kickstart, cloud-init, OpenShift install/add-node kernels+initrds+rootfs, ISOs, Armbian images. Real Let's Encrypt cert; iPXE validates from its built-in CA bundle.

**Retired (do not reference):** the TrueNAS netbootxyz TrueCharts container at `10.10.45.242` and `10.10.45.240/hub/`.

---

## End-to-end boot path

1. PXE client DHCP-discovers from rb5009 → option-66/67 routes it to a `netboot.xyz.{kpxe,efi}` binary on rb5009 TFTP.
2. Binary's embedded `:tftpmenu` autoexec runs against `${tftp-server}` (= rb5009):
   ```
   chain tftp://rb5009/local-vars.ipxe                 || (always fails today, harmless)
   isset ${hostname} && chain tftp://rb5009/HOSTNAME-${hostname}.ipxe || (skipped unless DHCP set hostname)
   chain tftp://rb5009/MAC-${mac:hexraw}.ipxe          || (pinned hosts hit this and stop)
   chain tftp://rb5009/menu.ipxe                       || (deliberately absent → falls through)
   ```
3. Unpinned hosts fall through to `:menu` → `chain https://public.igou.systems/boot-files/menu.ipxe` (the custom fallback).
4. The fallback offers localboot (default after 30s), OCP add-node ISO, any `netboot_entries`, and an iPXE shell.

The boot.cfg / version.ipxe / sigs chains inside the netbootxyz menu template are not used by this deployment — `generate_menus: false` is set in `user_overrides.yml.j2`. Pin fragments that need centos_mirror or similar set the value inline.

---

## Common invocations

```bash
# Full deploy (preflight + render + push to nginx + push pins to rb5009 + fetch + local + verify).
# Inline localhost connection because `localhost` is in inventory.
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml

# Local-only render — see what menu/entries/per_host WOULD be generated, no remote contact
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml \
  --tags render --check
# Then inspect .cache/netboot-menus/ on the controller.

# Menu touch-up (skip slow downloads, skip local artifacts)
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml \
  --tags render,push,verify

# Just verify (HTTPS probes + rb5009 /file + /ip tftp checks)
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml \
  --tags verify

# Fetch ISOs only (after adding a kind: iso entry)
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml \
  --tags fetch
```

---

## Adding a menu entry

Menu entries live in `igou-inventory/group_vars/all/netboot.yml` under `netboot_entries`. Four kinds are supported. Each entry needs `id`, `name`, and `kind`; required fields per kind below. Entries appear in the unpinned-host fallback menu at `https://public.igou.systems/boot-files/menu.ipxe`.

### `kind: kernel` — chain to upstream installer URLs

```yaml
netboot_entries:
  - id: debian-12-preseed
    name: "Debian 12 (preseed)"
    kind: kernel
    kernel: https://deb.debian.org/debian/dists/bookworm/main/installer-amd64/current/images/netboot/debian-installer/amd64/linux
    initrd: https://deb.debian.org/debian/dists/bookworm/main/installer-amd64/current/images/netboot/debian-installer/amd64/initrd.gz
    cmdline: "auto=true url={{ '{{' }} netboot_public_url {{ '}}' }}/kickstart/debian.preseed"
    kickstart: debian.preseed   # path under playbooks/netboot/files/kickstart/
    cache: false                # set true to download to /boot-files/cache/<id>/
```

Then `--tags render,push,verify`. If `cache: true`, also `--tags fetch`.

### `kind: iso` — sanboot a pinned ISO

Downloads upstream once, sha256-checked, served from `https://public.igou.systems/boot-files/iso/<id>.iso`.

```yaml
netboot_entries:
  - id: talos-1.9
    name: "Talos 1.9"
    kind: iso
    url: https://github.com/siderolabs/talos/releases/download/v1.9.0/metal-amd64.iso
    sha256: 1234abcd...
```

`--tags fetch` does the download (slow). On re-runs, sha256 match is a no-op. Then `--tags render,push,verify`.

### `kind: chainload` — chain to a remote `.ipxe` URL

```yaml
netboot_entries:
  - id: rocky-9-ks
    name: "Rocky 9 (kickstart)"
    kind: chainload
    url: "{{ '{{' }} netboot_public_url {{ '}}' }}/kickstart/rocky9.ipxe"
```

Render-only; no download.

### `kind: local` — ship a control-node-built kernel/initrd

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

Pins live in `igou-inventory/group_vars/all/netboot.yml` under `netboot_host_pins`. **Only Form 3 (fragment) is supported.** Form 1 (entry-pinned) and Form 2 (inline kernel/initrd) are rejected by `preflight.yml` — the rb5009-served pin layout doesn't have a way to chain into the public-nginx-hosted entries.

```yaml
netboot_host_pins:
  - mac: 22:33:44:55:66:77
    hostname: worker-01.igou.systems   # optional
    fragment: |
      #!ipxe
      kernel {{ '{{' }} netboot_public_url {{ '}}' }}/path/to/vmlinuz cmdline
      initrd {{ '{{' }} netboot_public_url {{ '}}' }}/path/to/initrd
      boot
```

Pin fragments may use Jinja `{{ '{{' }} netboot_public_url {{ '}}' }}` because Ansible recursively templates string values. iPXE's own `${var}` syntax (e.g., `${mac:hexraw}`) is orthogonal.

After any change: `--tags render,push,verify`. Push writes the pin file to `flash:/netboot/per-host/MAC-<hex>.ipxe` on rb5009 and creates a `/ip tftp` row mapping `MAC-<hex>.ipxe` → that flash path. Stale pins (entries removed from inventory) are pruned from both `flash:/netboot/per-host/` and `/ip tftp`.

---

## Adding a hand-written `.ipxe` fragment

For escape-hatch content the declarative schema can't express:

```bash
$EDITOR playbooks/netboot/files/fragments/<my-fragment>.ipxe
```

Anything dropped in there is auto-included on the next render and shows up as a menu item in the fallback `menu.ipxe`. `--tags render,push,verify`.

---

## Adding a kickstart config or cloud-init seed

Drop the file into the appropriate directory:

```bash
$EDITOR playbooks/netboot/files/kickstart/<distro>.cfg
$EDITOR playbooks/netboot/files/cloud-init/<role>.yaml
```

Both directories are synced to `/mnt/ssd/public/boot-files/{kickstart,cloud-init}/` with `delete: true` — files removed from `playbooks/netboot/files/` are removed from the public host on the next push. Reference them from menu entries / host pins via:

- Kickstart: `inst.ks=https://public.igou.systems/boot-files/kickstart/<distro>.cfg`
- Cloud-init: `cloud-config-url=https://public.igou.systems/boot-files/cloud-init/<role>.yaml`

Or with the `netboot_public_url` variable inside pin fragments:
`inst.ks={{ netboot_public_url }}/kickstart/<distro>.cfg`

After dropping the file: `--tags push,verify`.

---

## Removing entries / pins

- Entry: delete the dict from `netboot_entries`. Next `--tags render,push,verify` cleans up `entries/<id>.ipxe` (synchronize `--delete=true`).
- Pin: delete from `netboot_host_pins`. Next `--tags render,push,verify` removes the pin file from rb5009 flash AND the matching `/ip tftp` row.
- Hand-written fragment: `git rm playbooks/netboot/files/fragments/<file>.ipxe`, then `--tags render,push,verify`.
- Kickstart / cloud-init file: `git rm playbooks/netboot/files/{kickstart,cloud-init}/<file>`, then `--tags push,verify`. Synchronize is `delete: true` for both directories now (was `delete: false` under the netbootxyz container; the move to public nginx made it safer to enforce).

---

## OpenShift: add a worker via PXE

Two concerns are separated:
- **Boot artifacts** (kernel/initrd/rootfs, baked with a token that rotates) are written by `playbooks/openshift/add_node_iso.yml` into `/mnt/ssd/public/boot-files/<cluster>-add-node/`. Re-run when tokens expire.
- **Per-host iPXE script** (the `MAC-<hex>.ipxe` chain target on rb5009) is owned by `deploy_assets.yml`, rendered from inventory's `netboot_host_pins`. The pin's URL paths are stable across artifact refreshes; render once.

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
#      {{ netboot_public_url }}/<cluster>-add-node/node.<arch>-vmlinuz
#      {{ netboot_public_url }}/<cluster>-add-node/node.<arch>-initrd.img
#      {{ netboot_public_url }}/<cluster>-add-node/node.<arch>-rootfs.img

# 3. Set on the cluster host (igou-inventory/host_vars/<cluster>.yml):
#    openshift_add_node_arch: x86_64
#    openshift_add_node_boot_artifacts_base_url: "{{ netboot_public_url }}/<cluster>-add-node/"

# 4. Deploy the per-host pin (one time, for this MAC):
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml --tags render,push,verify

# 5. Generate the boot artifacts:
export KUBECONFIG=~/.kube/<cluster>-config
ansible-playbook playbooks/openshift/add_node_iso.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=<cluster>

# 6. PXE-boot the worker (BMC, IPMI, manual reboot — whatever you do today).

# 7. Optionally watch the cluster see it:
ansible-playbook playbooks/openshift/add_node_iso.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=<cluster> --tags monitor

# 8. Approve any pending CSRs:
oc get csr
oc adm certificate approve <name>
```

### Subsequent runs (token refresh, cluster reinstall, etc.)

Just re-run step 5. The pin file on rb5009 doesn't need updating — the URLs it references stay the same; only the artifacts behind those URLs rotate.

```bash
ansible-playbook playbooks/openshift/add_node_iso.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml -e target_cluster=<cluster>
```

`add_node_iso.yml` is intentionally not idempotent: `oc adm node-image create --pxe` always re-bakes the artifacts, so `changed=2` (or so) on every run is normal.

---

## rb5009: refresh iPXE binaries

`netboot.xyz.kpxe` (BIOS), `netboot.xyz.efi` (UEFI x64), and `netboot.xyz-arm64.efi` (UEFI ARM64) are built from upstream netboot.xyz with the internal HTTPS fallback URL embedded:
- `boot_domain = public.igou.systems/boot-files`
- `bootloader_default = https`

The binaries' embedded autoexec ALSO has a hardcoded `:tftpmenu` per-host MAC/HOSTNAME chain against `${tftp-server}` (= rb5009) — this is what makes pinned-host routing work without a custom menu.ipxe.

```bash
# Build, upload, wire DHCP, verify — full pipeline:
ansible-playbook playbooks/routeros/deploy_netboot_binaries.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml

# Just rebuild and re-upload (skip DHCP wiring):
ansible-playbook playbooks/routeros/deploy_netboot_binaries.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml \
  --tags build,upload,verify
```

Rebuild when:
- `netboot_chainload_host` or `netboot_chainload_proto` changes (rare).
- Upstream netboot.xyz publishes a security fix to the iPXE bundle.
- A new arch is added.

To flip HTTPS↔HTTP without a rebuild, set `netboot_public_scheme: http` in inventory and re-run `deploy_assets.yml --tags push`. The iPXE binaries try HTTPS first then HTTP automatically; flipping `netboot_public_scheme` only changes what the rendered `.ipxe` scripts (menu.ipxe + pin fragments) emit.

---

## Smoke test the deployment

After any change, run the headless smoke test to confirm:
- public.igou.systems is reachable over HTTPS.
- Smoke pin files are present on rb5009 with expected substring (preflight readback).
- Pinned MACs see their `/ip tftp` row's hit counter increment after a real boot.
- Random MACs see no `/ip tftp` row exists for their auto-generated MAC.

```bash
# Serial mode (~15 min; one VM at a time):
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml

# Parallel mode (~6 min; all 4 VMs at once):
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml \
  -e 'pxe_test_parallel=true'
```

Expect `failed=0`. The 4 default cases exercise BIOS + UEFI × pinned + random. VMs schedule on `ocp.igou.systems` via nodeSelector.

---

## Verify live state matches inventory

```bash
# 1. Full verify pass (HTTPS probes + rb5009 /file + /ip tftp counts)
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml \
  --tags verify

# 2. Inspect pin files on rb5009
SSH_AUTH_SOCK= ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes igou@rb5009.igou.systems -p 3480 \
  "/file print where name~\"^netboot/per-host/\""
SSH_AUTH_SOCK= ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes igou@rb5009.igou.systems -p 3480 \
  "/ip tftp print where req-filename~\"^(MAC|HOSTNAME)-\""

# 3. Inspect HTTPS asset reachability (from a network position that can reach 10.10.45.241)
curl -sI https://public.igou.systems/boot-files/menu.ipxe
curl -sI https://public.igou.systems/boot-files/ocp/agent.x86_64-vmlinuz
```

---

## Troubleshooting

### "My VM didn't get its custom pin"

1. Is the pin file on rb5009?
   ```
   SSH_AUTH_SOCK= ssh -i ~/.ssh/id_ed25519 igou@rb5009.igou.systems -p 3480 \
     "/file print where name~\"netboot/per-host/MAC-<hex>.ipxe\""
   ```
   - Empty result → re-run `--tags push` and check the push_pins_rb5009 task didn't fail silently.
2. Is the `/ip tftp` row mapping the bare filename to the flash path?
   ```
   /ip tftp print where req-filename="MAC-<hex>.ipxe"
   ```
   - Empty → re-run push.
3. Is the binary actually requesting the file? Check the hit counter:
   ```
   /ip tftp print detail without-paging where req-filename="MAC-<hex>.ipxe"
   ```
   The `hits=N` field should increment after a real PXE boot. If it doesn't, iPXE isn't reaching `:tftpmenu` — check the binary's chainload path (boot.cfg vs autoexec) and DHCP wiring.
4. Confirm the file body:
   ```
   /file print value-list where name="netboot/per-host/MAC-<hex>.ipxe"
   ```
5. Check the VM's MAC matches what's in inventory: `${mac:hexraw}` is lowercase no-separator (e.g. `f8:b4:6a:ab:55:c7` → `f8b46aab55c7`).

### "Unpinned host doesn't see the fallback menu"

The HTTPS fallback chain inside the bootstrap iPXE binary is `chain https://public.igou.systems/boot-files/menu.ipxe`. If this fails:
1. Probe the URL from a network position that can reach the public nginx:
   ```
   curl -sI https://public.igou.systems/boot-files/menu.ipxe
   ```
   - 404 → run `--tags push` to re-write menu.ipxe.
   - SSL error → cert renewal or chain issue (Let's Encrypt managed; check truenas TLS config).
   - No route → public nginx container down or macvlan misconfigured.
2. Check the file ownership: nginx serves files only if readable by its uid. `chmod -R 0755/0644` (dir/file) usually fixes 403s caused by ACL inheritance from the parent dataset.

### "iPXE chain fails on a worker"

1. Rb5009 binary actually delivered? Check `--tags verify` output of `playbooks/routeros/deploy_netboot_binaries.yml` (TFTP hit-counter delta).
2. iPXE binary chainloads to the right URL? `strings .cache/netboot-build/output/ipxe/netboot.xyz.kpxe | grep -E 'public|boot-files'`. If wrong, rebuild (`deploy_netboot_binaries.yml --tags build,upload`).
3. Is the DHCP `next-server` / `boot-file-name` matcher table targeting the right binary for the client's option-93? `/ip dhcp-server matcher print` on rb5009 (or check via `--tags verify`).

### "Asset URL returns 404 even though the file is at the path"

nginx serves `/mnt/ssd/public/` at HTTPS root. URLs look like `https://public.igou.systems/boot-files/<path>` — don't prefix with `/mnt/ssd/public/`. If still 404, check file permissions (see above).

### "I changed an entry but the deploy says no changes"

`--tags render` writes to `.cache/netboot-menus/` on the controller; `--tags push` syncs that cache. If you only ran `--tags push`, no re-render happened. Default invocation runs all stages; explicit tag-driven runs need `render,push` (and `verify` for sanity).

### "I want to roll back"

The previous netbootxyz container architecture is gone — there's nothing to roll back to. To temporarily disable per-host pins for a specific MAC:

```bash
# 1. Remove the MAC from netboot_host_pins (or comment it out)
# 2. Re-run --tags render,push,verify -- this prunes the file and /ip tftp row
```

To temporarily disable the unpinned-host menu without `deploy_assets`, remove `menu.ipxe` from rb5009's `/ip tftp` redirects (there isn't one in the current architecture — but you can add one that points at a static `chain exit 1` to force localboot).

---

## Path reference

### Public nginx layout

Filesystem on truenas:
```
/mnt/ssd/public/boot-files/             # netboot_public_root
├── menu.ipxe                            # rendered fallback menu
├── entries/<id>.ipxe                    # one per netboot_entries
├── fragments/<file>.ipxe                # auto-included custom .ipxe
├── kickstart/<distro>.cfg               # synced from playbooks/netboot/files/kickstart/
├── cloud-init/<role>.yaml               # synced from playbooks/netboot/files/cloud-init/
├── iso/<id>.iso                         # one per kind: iso entry (sha256-checked)
├── local/<id>/{vmlinuz,initrd}          # one dir per kind: local entry
├── cache/<id>/{vmlinuz,initrd}          # opt-in cache for kind: kernel
├── ocp/                                 # OpenShift agent-install (separate playbook)
├── ocp-add-node/                        # OpenShift add-node (separate playbook)
├── images/                              # Armbian images (separate playbook)
└── <cluster>-add-node/                  # per-cluster add-node (if you have >1)
```

URL paths (all at `https://public.igou.systems/boot-files`):

| URL | Filesystem |
|---|---|
| `/menu.ipxe` | `/mnt/ssd/public/boot-files/menu.ipxe` |
| `/entries/<id>.ipxe` | `/mnt/ssd/public/boot-files/entries/<id>.ipxe` |
| `/kickstart/<f>` | `/mnt/ssd/public/boot-files/kickstart/<f>` |
| `/cloud-init/<f>` | `/mnt/ssd/public/boot-files/cloud-init/<f>` |
| `/iso/<id>.iso` | `/mnt/ssd/public/boot-files/iso/<id>.iso` |
| `/ocp-add-node/<f>` | `/mnt/ssd/public/boot-files/ocp-add-node/<f>` |
| `/ocp/<f>` | `/mnt/ssd/public/boot-files/ocp/<f>` |

### rb5009 TFTP layout

Flash:
```
flash:/netboot/
├── netboot.xyz.kpxe                     # BIOS bootstrap iPXE binary
├── netboot.xyz.efi                      # UEFI x64 bootstrap iPXE binary
├── netboot.xyz-arm64.efi                # UEFI ARM64 bootstrap iPXE binary
└── per-host/
    ├── MAC-<hexraw>.ipxe                # one per netboot_host_pins (lowercase, no colons)
    └── HOSTNAME-<hostname>.ipxe         # alias chains to MAC-...ipxe via /ip tftp
```

`/ip tftp` rows (one per file):

| req-filename | real-filename |
|---|---|
| `netboot.xyz.kpxe` | `netboot/netboot.xyz.kpxe` |
| `netboot.xyz.efi` | `netboot/netboot.xyz.efi` |
| `netboot.xyz-arm64.efi` | `netboot/netboot.xyz-arm64.efi` |
| `MAC-<hex>.ipxe` | `netboot/per-host/MAC-<hex>.ipxe` |
| `HOSTNAME-<host>.ipxe` | `netboot/per-host/HOSTNAME-<host>.ipxe` |
