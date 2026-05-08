# Custom netboot.xyz iPXE binaries on rb5009 — design

**Date:** 2026-05-08
**Status:** approved (brainstorming complete)
**Scope:** one new playbook `playbooks/routeros/deploy_netboot_binaries.yml` (plus four task files under `playbooks/routeros/tasks/`) that builds custom `netboot.xyz.kpxe` / `.efi` / `.arm64.efi` binaries embedded with our internal chainload URL, ships them to the rb5009 TFTP server, and wires up DHCP `next-server` + `boot-file-name` so PXE clients land on the binaries. The binaries chainload to the existing TrueNAS netbootxyz container at `tftp://10.10.45.242/menu.ipxe` for the actual menu.

This replaces the rb5009 netbootxyz container deployment from `2026-05-07-netbootxyz-rb5009-design.md`. RouterOS does only DHCP + TFTP; menu serving stays on TrueNAS where it has been working.

## Goals

- Build custom iPXE binaries via netboot.xyz's upstream Ansible build (`site.yml` + `user_overrides.yml`) inside the official `ghcr.io/netbootxyz/builder` container, on the control node.
- Embed `boot_domain=10.10.45.242` and `bootloader_default=tftp` so the produced binaries chainload to the existing TrueNAS netbootxyz container without further client-side configuration.
- Ship three architectures: BIOS (`netboot.xyz.kpxe`), UEFI x86_64 (`netboot.xyz.efi`), UEFI arm64 (`netboot.xyz-arm64.efi`).
- Stage them on rb5009 flash and register them in `/ip tftp` under the same names netboot.xyz publishes upstream.
- Configure DHCP at the network layer (`next-server` + `boot-file-name`) on subnets `10.10.9.0/24` and `10.10.45.0/24`. Add UEFI matchers (option 93 = `0x0007`, `0x0009`, `0x000b`) per DHCP server to deliver the right binary per client architecture.
- Re-runnable: idempotent for every RouterOS resource and every build artifact. Tag-driven for selective re-runs (build vs. upload vs. dhcp vs. verify).
- Pin the netboot.xyz source ref via inventory variable so the build is reproducible.

## Non-goals

- Decommissioning the rb5009 netbootxyz container deployed by the previous design. The cleanup is documented as a manual procedure in a README the playbook ships with; the playbook does not stop, remove, or otherwise touch the existing `containers/netbootxyz` row.
- Decommissioning the TrueNAS netbootxyz container. It stays as the menu source.
- Customizing the chainload URL with HTTPS or per-host overrides. Only `tftp://<host>/menu.ipxe` is supported, because that's what the TrueNAS container's dnsmasq serves out of the box.
- Custom iPXE early-menu / branding / logo embedding. Out of scope; default upstream user_overrides values for those keys.
- Building the menu tree. `generate_menus: false` — TrueNAS already does this.
- Multi-host. The playbook targets the rb5009 only. The TrueNAS container is read-only context (we only need its IP).
- Molecule scenarios. RouterOS doesn't run in a container; consistent with the existing routeros playbook suite.
- AAP/AWX / GitHub Actions integration. Manual `ansible-navigator run` on the control node is the trigger.

## Inventory & connection

No connection changes. rb5009 already in `igou-inventory/inventory.yaml`; `igou-inventory/group_vars/routeros.yml` already configures network_cli + port 3480 + user `igou+cet1024w`.

The TrueNAS container's IP (`10.10.45.242`) lives in `group_vars/truenas.yml` under `truenas_docker_containers[name=netbootxyz].networks.vlan45.ipv4_address`. We don't reference truenas inventory directly — the address is captured as a string in the new variable `netboot_chainload_host`. If the TrueNAS container ever moves, both inventory entries get updated together.

## File layout

```
playbooks/routeros/
  deploy_netboot_binaries.yml             # new — orchestrator, tagged stages: build, upload, dhcp, verify
  tasks/
    netboot_build.yml                     # new — podman run nbxyz/builder, produce .kpxe/.efi/.arm64.efi  (tag: build)
    netboot_upload.yml                    # new — net_put + /ip tftp registration                          (tag: upload)
    netboot_dhcp.yml                      # new — network/option/option-set/matcher wiring                 (tag: dhcp)
    netboot_verify.yml                    # new — TFTP fetch checks + DHCP state assertion                 (tag: verify)
  templates/netboot/
    user_overrides.yml.j2                 # new — Jinja template rendered into the builder container
  files/netboot/
    cleanup-old-container.md              # new — manual teardown of the rb5009 nbxyz container (doc only, not deployed)
```

`.cache/netboot-build/` (gitignored) holds the cloned netboot.xyz source, the produced `/var/www/html/ipxe/*` artifacts, and a `MANIFEST` recording the last shipped ref + build timestamp.

Per-stage task files exist because each maps to a tag and the stages are meaningfully independent (build is local-only and slow; upload/dhcp are remote and fast). Matches the pattern set by `deploy_netbootxyz.yml` and the upgrade playbooks.

Conventions (same as the rest of `playbooks/routeros/`):

- `hosts: "{{ host | default('routeros_netboot') }}"`. Single-host group; explicit per-run override available.
- `gather_facts: false`.
- `serial: 1`.
- All RouterOS-side mutation uses `community.routeros.command`.
- The build stage runs `delegate_to: localhost`. Upload, DHCP, and verify run against the rb5009 (verify also delegates a TFTP probe to localhost).

## Architecture

```
┌──────────────────────────┐         ┌──────────────────────┐         ┌────────────────────────┐
│  Control node            │         │   rb5009             │         │   TrueNAS nbxyz        │
│                          │         │   (RouterOS)         │         │   10.10.45.242         │
│  podman run              │         │                      │         │                        │
│  ghcr.io/netbootxyz/     │  net_put│   flash:/netboot/    │ chainload│  dnsmasq tftp :69     │
│  builder:latest          │ ───────►│     netboot.xyz.kpxe │ tftp     │   → menu.ipxe         │
│  ↓                       │         │     netboot.xyz.efi  │ ───────►│   (existing)          │
│  user_overrides.yml      │         │     netboot.xyz-arm64.efi      │  webapp :3000          │
│  → /var/www/html/ipxe/   │         │                      │         │  nginx :80 (/assets)   │
│    *.kpxe / *.efi        │         │   /ip tftp           │         │                        │
│                          │         │   /ip dhcp-server    │         │                        │
└──────────────────────────┘         │     network          │         └────────────────────────┘
                                     │     option / sets    │
                                     │     matcher          │
                                     └──────────────────────┘
```

**Boot flow:**

1. PXE client DHCPs on `10.10.9.0/24` or `10.10.45.0/24`.
2. rb5009 returns `siaddr/next-server = <rb5009-gateway-ip>` and `boot-file-name = netboot.xyz.kpxe` (default), or via UEFI matcher returns `netboot.xyz.efi` / `netboot.xyz-arm64.efi`.
3. Client TFTPs the binary from rb5009.
4. Binary's embedded iPXE script runs `chain tftp://10.10.45.242/menu.ipxe`.
5. Client loads the menu from TrueNAS, normal netboot.xyz user flow continues from there.

## Build pipeline (`netboot_build.yml`)

Runs `delegate_to: localhost`. Produces `<repo>/.cache/netboot-build/ipxe/netboot.xyz.kpxe` and the two `.efi` files.

### Source clone

```yaml
- name: Ensure local build cache directory exists
  ansible.builtin.file:
    path: "{{ netboot_local_build_dir }}"
    state: directory
    mode: "0755"

- name: Clone or update netboot.xyz source at the pinned ref
  ansible.builtin.git:
    repo: "{{ netboot_xyz_repo }}"
    dest: "{{ netboot_local_build_dir }}/src"
    version: "{{ netboot_xyz_ref }}"
    force: true
```

### Render `user_overrides.yml`

Templated from inventory:

```yaml
# templates/netboot/user_overrides.yml.j2
boot_domain: "{{ netboot_chainload_host }}"
bootloader_default: "{{ netboot_chainload_proto }}"
site_name: "igou homelab"
generate_menus: false
generate_disks: true
generate_checksums: false
make_num_jobs: 4
```

Written to `{{ netboot_local_build_dir }}/src/user_overrides.yml` (overwriting the upstream file in the clone).

### Build invocation

```bash
podman run --rm \
  --pull=always \
  -v {{ netboot_local_build_dir }}/src:/ansible:Z \
  -v {{ netboot_local_build_dir }}/output:/var/www/html:Z \
  {{ netboot_builder_image }} \
  ansible-playbook -i localhost, /ansible/site.yml
```

`/var/www/html/ipxe/` ends up populated with all upstream-built binaries (kpxe, efi, arm64.efi, plus iso/usb variants we ignore).

### Idempotency

A SHA256 hash of the rendered `user_overrides.yml` is recorded to `<cache>/MANIFEST.lastbuild` after a successful run. On re-run, if the hash matches AND all the expected binaries exist in `<cache>/output/ipxe/`, the build step is skipped. Bumping `netboot_xyz_ref` invalidates the build implicitly because the rendered overrides include any embedded version metadata (in practice we hash the rendered file plus the resolved git SHA from the clone).

`MANIFEST` is the human-readable record:

```
ref: 2.0.84
git_sha: <sha>
built_at: 2026-05-08T10:30:00Z
artifacts:
  - ipxe/netboot.xyz.kpxe
  - ipxe/netboot.xyz.efi
  - ipxe/netboot.xyz-arm64.efi
```

## Upload + TFTP registration (`netboot_upload.yml`)

Runs on the rb5009.

### Flash directory

```yaml
- name: Count flash netboot directory
  community.routeros.command:
    commands:
      - '/file print count-only where name="{{ netboot_flash_dir }}" and type=directory'
  register: _netboot_flash_dir_count
  changed_when: false

- name: Create flash netboot directory
  community.routeros.command:
    commands:
      - "/file add name={{ netboot_flash_dir }} type=directory"
  when: (_netboot_flash_dir_count.stdout[0] | trim | int) == 0
  changed_when: true
```

### Upload each architecture's binary

Looped over `netboot_arches`. For each arch, the upload is gated on a size compare against the local artifact:

```yaml
- name: Stat local binary
  ansible.builtin.stat:
    path: "{{ netboot_local_build_dir }}/output/ipxe/{{ item.local_name }}"
  delegate_to: localhost
  register: _local_stat
  loop: "{{ netboot_arches_resolved }}"
  loop_control:
    label: "{{ item.public_name }}"

- name: Count router-side binaries matching local size
  community.routeros.command:
    commands:
      - >-
        /file print count-only where
        name="{{ netboot_flash_dir }}/{{ item.public_name }}" and
        size={{ _local_stat.results[loop.index0].stat.size }}
  register: _router_size_match
  changed_when: false
  loop: "{{ netboot_arches_resolved }}"

- name: Upload binary if size differs
  ansible.netcommon.net_put:
    src: "{{ netboot_local_build_dir }}/output/ipxe/{{ item.local_name }}"
    dest: "{{ netboot_flash_dir }}/{{ item.public_name }}"
  when: (_router_size_match.results[loop.index0].stdout[0] | trim | int) == 0
  loop: "{{ netboot_arches_resolved }}"
```

`netboot_arches_resolved` is a fact built from `netboot_arches`:

```yaml
arches_map:
  bios:        { local_name: netboot.xyz.kpxe,        public_name: netboot.xyz.kpxe }
  uefi-x64:    { local_name: netboot.xyz.efi,         public_name: netboot.xyz.efi }
  uefi-arm64:  { local_name: netboot.xyz-arm64.efi,   public_name: netboot.xyz-arm64.efi }
```

### TFTP registration

For each arch, register a `/ip tftp` row mapping public name → on-flash path. Idempotent via count-only check on the existing row:

```yaml
- name: Count tftp rows for each arch
  community.routeros.command:
    commands:
      - >-
        /ip tftp print count-only where
        req-filename="{{ item.public_name }}" and
        real-filename="{{ netboot_flash_dir }}/{{ item.public_name }}"
  register: _tftp_count
  loop: "{{ netboot_arches_resolved }}"
  changed_when: false

- name: Add tftp row
  community.routeros.command:
    commands:
      - >-
        /ip tftp add
        req-filename="{{ item.public_name }}"
        real-filename="{{ netboot_flash_dir }}/{{ item.public_name }}"
        allow=yes read-only=yes
  when: (_tftp_count.results[loop.index0].stdout[0] | trim | int) == 0
  loop: "{{ netboot_arches_resolved }}"
  changed_when: true
```

## DHCP wiring (`netboot_dhcp.yml`)

Runs on the rb5009. Three sections: network-layer fields, option/option-sets (global, added once), matchers (per DHCP server).

### Network-layer (default = BIOS)

For each subnet in `netboot_subnets`:

```yaml
- name: Set next-server + boot-file-name on each DHCP network
  community.routeros.command:
    commands:
      - >-
        /ip dhcp-server network set [find address={{ item.subnet }}]
        next-server={{ item.rb5009_ip }}
        boot-file-name=netboot.xyz.kpxe
  loop: "{{ netboot_subnets }}"
  changed_when: true
```

Drift detection: compare the existing `next-server` and `boot-file-name` values via `print` first; only `set` if either differs.

### Option + option-set (global, conditional on UEFI arches enabled)

```yaml
- name: Add option pxe-bf-uefi-x64
  # gated by: 'uefi-x64' in netboot_arches and count==0
  community.routeros.command:
    commands:
      - >-
        /ip dhcp-server option add
        code=67 name=pxe-bf-uefi-x64 value="'netboot.xyz.efi'"

- name: Add option-set pxe-uefi-x64
  community.routeros.command:
    commands:
      - "/ip dhcp-server option/sets add name=pxe-uefi-x64 options=pxe-bf-uefi-x64"
```

Same shape for `pxe-bf-uefi-arm64` / `pxe-uefi-arm64` when `uefi-arm64` is enabled. Each gated on a count-only check.

### Matchers (per DHCP server)

```yaml
netboot_uefi_matchers:
  - { value: "0x0007", set: pxe-uefi-x64,   arch: uefi-x64 }
  - { value: "0x0009", set: pxe-uefi-x64,   arch: uefi-x64 }
  - { value: "0x000b", set: pxe-uefi-arm64, arch: uefi-arm64 }
```

```yaml
- name: Add matcher per (subnet × matcher value)
  community.routeros.command:
    commands:
      - >-
        /ip dhcp-server matcher add
        name=match-{{ item.0.dhcp_server }}-{{ item.1.value | regex_replace('^0x', '') }}
        server={{ item.0.dhcp_server }}
        code=93 value="{{ item.1.value }}"
        option-set={{ item.1.set }}
  loop: "{{ netboot_subnets | product(netboot_uefi_matchers) | list }}"
  when: item.1.arch in netboot_arches
  # gated also by count-only check on (server, code=93, value)
```

That's 6 matcher rows total when all three arches are enabled (2 subnets × 3 matcher values).

## Verification (`netboot_verify.yml`)

Three checks. Each `delegate_to: localhost` for outbound TFTP probes; the DHCP-state assertion runs on the rb5009.

### TFTP fetch from rb5009

```yaml
- name: Fetch each binary from rb5009 over TFTP
  ansible.builtin.command:
    cmd: >-
      curl -sf -o /dev/null
      tftp://{{ netboot_subnets[0].rb5009_ip }}/{{ item.public_name }}
  delegate_to: localhost
  loop: "{{ netboot_arches_resolved }}"
  changed_when: false
```

Followed by an assertion that each fetched size matches the local build artifact (compare against the stat from the upload stage).

### TFTP fetch from TrueNAS (chainload sanity)

```yaml
- name: Fetch menu.ipxe from TrueNAS netbootxyz
  ansible.builtin.command:
    cmd: "curl -sf -o /tmp/menu.ipxe tftp://{{ netboot_chainload_host }}/menu.ipxe"
  delegate_to: localhost
  changed_when: false

- name: Assert menu.ipxe is iPXE script
  ansible.builtin.command:
    cmd: "head -c 6 /tmp/menu.ipxe"
  register: _menu_head
  delegate_to: localhost
  failed_when: '"#!ipxe" not in _menu_head.stdout'
  changed_when: false
```

This catches the common breakage mode of the TrueNAS container being down.

### DHCP-state assertion

Read `/ip dhcp-server network print detail` and matcher rows, assert via Jinja that:
- Every `netboot_subnets[*].subnet` has the correct `next-server` and `boot-file-name`.
- For each (subnet, matcher value) where the corresponding arch is enabled, a matcher row exists.

No actual DHCP request is issued — that's the manual smoke test (boot a UEFI VM on vlan45, watch it pull `.efi` from rb5009 and chainload).

## Inventory variables

Added to `igou-inventory/group_vars/routeros.yml`:

```yaml
# --- Variables consumed by playbooks/routeros/deploy_netboot_binaries.yml ---

# Source pin: the netboot.xyz repo and ref to clone for the build.
netboot_xyz_repo: https://github.com/netbootxyz/netboot.xyz.git
netboot_xyz_ref: "2.0.84"

# Builder image. Pin to digest in real config; latest used here for clarity.
netboot_builder_image: ghcr.io/netbootxyz/builder:latest

# Local working directory for clone + build outputs (gitignored).
netboot_local_build_dir: "{{ playbook_dir }}/../../.cache/netboot-build"

# Chainload target embedded into the binaries (boot_domain in user_overrides).
# netboot_chainload_proto is currently constrained to "tftp" — see Decisions log.
netboot_chainload_host: "10.10.45.242"
netboot_chainload_proto: "tftp"

# Subnets that get next-server + boot-file-name + UEFI matchers.
# Each entry pairs subnet ↔ rb5009 IP on that subnet ↔ DHCP server name.
# Confirm dhcp_server names with: /ip dhcp-server print
netboot_subnets:
  - subnet: 10.10.9.0/24
    rb5009_ip: 10.10.9.1
    dhcp_server: <fill in from /ip dhcp-server print>
  - subnet: 10.10.45.0/24
    rb5009_ip: 10.10.45.1
    dhcp_server: <fill in from /ip dhcp-server print>

# Architectures to build, upload, register, and add matchers for.
# Drop entries to skip an arch entirely (build, upload, and matcher all gated).
netboot_arches:
  - bios
  - uefi-x64
  - uefi-arm64

# UEFI option-93 matcher table. The deploy uses this verbatim.
netboot_uefi_matchers:
  - { value: "0x0007", set: pxe-uefi-x64,   arch: uefi-x64 }
  - { value: "0x0009", set: pxe-uefi-x64,   arch: uefi-x64 }
  - { value: "0x000b", set: pxe-uefi-arm64, arch: uefi-arm64 }

# Flash directory for the binaries.
netboot_flash_dir: netboot
```

`netboot_subnets[].dhcp_server` values are the only inventory entries that need to be filled in at deploy time — they depend on what `/ip dhcp-server print` reports on the live router and aren't currently captured anywhere in inventory.

## Manual cleanup of the previous deployment

Lives at `playbooks/routeros/files/netboot/cleanup-old-container.md`. Contents:

```markdown
# Decommissioning the rb5009 netbootxyz container

After `deploy_netboot_binaries.yml` has run successfully and you've smoke-tested
PXE boot end-to-end (boot a real or virtual client on 10.10.9.0/24 or 10.10.45.0/24,
confirm it pulls the binary from rb5009 and lands on the TrueNAS menu), tear down
the rb5009 netbootxyz container manually.

Each step is idempotent — run them all even on a partial cleanup.

1. Stop and remove the container:
       /container stop [find root-dir=containers/netbootxyz]
       # wait until status=stopped, then:
       /container remove [find root-dir=containers/netbootxyz]
   This wipes containers/netbootxyz/ on flash (the writable layer holding /config).

2. Remove the env list (only present if netbootxyz_env_extra was non-empty):
       /container envs remove [find name=netbootxyz-env]

3. Detach the bridge port and remove the veth:
       /interface bridge port remove [find interface=veth-netbootxyz]
       /interface veth remove [find name=veth-netbootxyz]

4. Remove the image tar:
       /file remove containers/netbootxyz.tar

5. (Optional) verify nothing remains:
       /container print
       /file print where name~"netbootxyz"
```

The deploy playbook's header comment links to this file so it's discoverable from a `git grep deploy_netboot_binaries`.

## Operations

- **Re-running:** safe. Build skips if user_overrides hash unchanged AND artifacts present. Uploads skip if size matches. DHCP/TFTP rows skip if already present.
- **Bumping netboot.xyz version:** edit `netboot_xyz_ref` in inventory → re-run. Build pulls new ref, hash mismatch triggers rebuild, size mismatch triggers re-upload.
- **Bumping the chainload target:** edit `netboot_chainload_host` → re-run. The user_overrides hash changes, build rebuilds binaries with the new embedded URL, upload replaces the flash files.
- **Skipping the build (reuse cached binaries):** `--tags upload,dhcp,verify`.
- **Disabling an arch temporarily:** remove its entry from `netboot_arches`. Existing rows on the router are NOT removed — disabling at the inventory level only stops new pushes/matchers being added. To remove old rows, do it by hand.
- **Recovering after a failed build:** delete `<cache>/MANIFEST.lastbuild` and re-run; that forces a rebuild even if the hash hadn't changed.

## Decisions log

- **Why custom binaries instead of stock netboot.xyz binaries?** So the chainload URL embeds at build time. Stock binaries chainload to `boot.netboot.xyz` over HTTPS, which depends on internet, ours don't.
- **Why TFTP for the chainload (not HTTP)?** The TrueNAS netbootxyz container's nginx default config only serves `/assets`, not `/config/menus`. dnsmasq on :69 *does* serve `menu.ipxe` out of the box. Switching to HTTP would require customizing the container's nginx, which is out of scope. TFTP works today.
- **Why keep the TrueNAS netbootxyz container?** It already serves menus and exposes a UI for managing them. Replacing it with plain nginx + manually-curated menus is more work for less ergonomics.
- **Why network-layer `next-server` + `boot-file-name`, not DHCP option-set with code=67 for the default path?** RouterOS exposes those as first-class DHCP server network properties. They don't need to go through option/option-set/matcher unless we're varying them per client (which we are, but only for UEFI overrides).
- **Why two matchers for UEFI x86_64 (`0x0007` AND `0x0009`)?** IANA registers `0x0009` for EFI x86_64 but most real-world firmware sends `0x0007` (EFI BC). Catching both costs one extra matcher row and avoids a class of "UEFI client falls through to BIOS default and fails" bugs.
- **Why is the old-container teardown a manual README and not an automation step?** User decision (2026-05-08): the playbook should not delete a container it doesn't own. Manual cleanup keeps the failure mode obvious — if the manual steps don't run, you can see the orphaned container in `/container print` next time you log in.
- **Why pin via `netboot_xyz_ref` and not git submodule?** Git ref pin keeps the spec/plan repo lean (no large vendor tree), and bumping is a one-line inventory edit. Submodule is overkill for a build dependency we don't modify.
