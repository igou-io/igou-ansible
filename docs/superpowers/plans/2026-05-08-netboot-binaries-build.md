# Custom netboot.xyz iPXE Binaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build custom `netboot.xyz.kpxe`, `netboot.xyz.efi`, and `netboot.xyz-arm64.efi` binaries on the control node, ship them to the MikroTik rb5009 for TFTP serving, and wire DHCP `next-server` + `boot-file-name` (with UEFI matchers) so PXE clients on `10.10.9.0/24` and `10.10.45.0/24` chainload to the existing TrueNAS netbootxyz container at `tftp://10.10.45.242/menu.ipxe`.

**Architecture:** Single playbook `playbooks/routeros/deploy_netboot_binaries.yml` with four tagged stages backed by per-stage task files (`build`, `upload`, `dhcp`, `verify`). The build stage runs `delegate_to: localhost` and invokes `podman run ghcr.io/netbootxyz/builder:latest ansible-playbook -i localhost, /ansible/site.yml` against a templated `user_overrides.yml`; the other three stages run against the rb5009 via `community.routeros.command`. No molecule scenarios — consistent with the rest of `playbooks/routeros/`. Idempotency is enforced via build-output hashing, file size compare for uploads, and `print count-only` checks before each RouterOS mutation.

**Tech Stack:** Ansible (`community.routeros.command`, `ansible.netcommon.net_put`, `ansible.builtin.git`, `ansible.builtin.template`, `ansible.builtin.command`), Podman (for the netboot.xyz builder container), Jinja2 templates, MikroTik RouterOS RouterOS connection over SSH (`ansible_port=3480`), `curl` (for the TFTP-fetch verification probes).

---

## Reference material

- Spec: `docs/superpowers/specs/2026-05-08-netboot-binaries-build-design.md`. Re-read this — it has the full architectural rationale and decisions log.
- Prior implementation in the same playbook directory: `playbooks/routeros/deploy_netbootxyz.yml` and the seven `playbooks/routeros/tasks/netbootxyz_*.yml` files. Their style (tagged stages, `community.routeros.command` everywhere, `register: _foo` + `count-only` idempotency) is the model to follow.
- Repo conventions: `CLAUDE.md` at the repo root. Note especially: YAML must start with `---`, two-space indent, YAML 1.2 booleans (`true`/`false`), ansible-lint production profile.
- Pre-existing inventory: `igou-inventory/group_vars/routeros.yml` already has `routeros_netboot` group reference and the netbootxyz vars from the prior design. Extend it; don't overwrite.

## Files Created/Modified

```
.gitignore                                                MODIFY  (add .cache/netboot-build/)
igou-inventory/group_vars/routeros.yml                    MODIFY  (append netboot_* block)

playbooks/routeros/deploy_netboot_binaries.yml            CREATE  (orchestrator)
playbooks/routeros/tasks/netboot_build.yml                CREATE  (stage 1)
playbooks/routeros/tasks/netboot_upload.yml               CREATE  (stage 2)
playbooks/routeros/tasks/netboot_dhcp.yml                 CREATE  (stage 3)
playbooks/routeros/tasks/netboot_verify.yml               CREATE  (stage 4)
playbooks/routeros/templates/netboot/user_overrides.yml.j2  CREATE
playbooks/routeros/files/netboot/cleanup-old-container.md CREATE  (manual teardown doc)
```

## Pre-flight assumptions (verify before Task 1)

These are *assumptions baked into this plan*. If any is wrong, stop and clarify with the user before proceeding.

1. **Control node has podman.** This codebase's CLAUDE.md confirms it. `podman --version` should work.
2. **Control node has internet to ghcr.io and github.com.** Per CLAUDE.md, github.com is whitelisted; ghcr.io is also allowed because the EE build CI relies on it.
3. **rb5009 reachable on `ansible_port=3480` from the control node.** Recent commits in this branch have already deployed playbooks against it.
4. **The rb5009 nbxyz container deployed by `2026-05-07-netbootxyz-rb5009-design.md` is currently running.** This plan does NOT remove it; the cleanup README does. The new TFTP files don't conflict with anything that container did, because the binaries land in `flash:/netboot/` (new dir) and the existing container's data is in `flash:/containers/netbootxyz/`.
5. **The DHCP server names on rb5009 for the two target subnets are not yet known.** Task 1 has the operator confirm them with `/ip dhcp-server print` before populating the inventory placeholders.
6. **Existing `/ip dhcp-server network` rows for `10.10.9.0/24` and `10.10.45.0/24` exist.** Task 6 modifies them with `set [find address=...]` — if the rows don't exist, the `set` is a no-op and Task 7 verify will fail loudly.

---

## Task 1: Inventory variables and gitignore

**Files:**
- Modify: `igou-inventory/group_vars/routeros.yml` (append the netboot_* block at the end)
- Modify: `.gitignore` (add `.cache/netboot-build/` if not present)

This task gives every later task a stable variable surface. We do this first so the orchestrator and task files can `Read` real values via inventory, not placeholders.

- [ ] **Step 1: Confirm rb5009 DHCP server names**

  Run, on the control node:

  ```bash
  ansible-navigator exec --mode stdout -- \
      ansible -i igou-inventory/inventory.yaml routeros_netboot \
      -m community.routeros.command \
      -a 'commands=["/ip dhcp-server print without-paging"]'
  ```

  Expected: a table listing one or more DHCP servers. Note the `NAME` column entries that match the two subnets `10.10.9.0/24` and `10.10.45.0/24`. Record both names — these go into the inventory in step 2.

  If the operator can't produce these names (e.g., no rb5009 access), STOP and ask the user.

- [ ] **Step 2: Append netboot variables to `igou-inventory/group_vars/routeros.yml`**

  Open the file. The bottom of the file currently ends after the `netbootxyz_force_restart` block (around line 104). Append:

  ```yaml

  # --- Variables consumed by playbooks/routeros/deploy_netboot_binaries.yml ---

  # Source pin: the netboot.xyz repo and ref to clone for the build.
  netboot_xyz_repo: https://github.com/netbootxyz/netboot.xyz.git
  netboot_xyz_ref: "2.0.84"

  # netboot.xyz builder image. Pinned to digest in inventory; latest used
  # here for clarity until the first successful build records one.
  netboot_builder_image: ghcr.io/netbootxyz/builder:latest

  # Local working directory for the source clone and build outputs.
  # Resolved relative to the playbook directory; lands at
  # <repo-root>/.cache/netboot-build/.
  netboot_local_build_dir: "{{ playbook_dir }}/../../.cache/netboot-build"

  # Chainload target embedded into the iPXE binaries (boot_domain in
  # the rendered user_overrides.yml). netboot_chainload_proto is currently
  # constrained to "tftp" -- see the design's Decisions log.
  netboot_chainload_host: "10.10.45.242"
  netboot_chainload_proto: "tftp"

  # Subnets that get next-server + boot-file-name + UEFI matchers. Each
  # entry pairs a subnet with its rb5009 IP and the local DHCP server's name.
  # The dhcp_server values come from `/ip dhcp-server print` on the router.
  netboot_subnets:
    - subnet: 10.10.9.0/24
      rb5009_ip: 10.10.9.1
      dhcp_server: REPLACE_WITH_LAN_DHCP_NAME
    - subnet: 10.10.45.0/24
      rb5009_ip: 10.10.45.1
      dhcp_server: REPLACE_WITH_VLAN45_DHCP_NAME

  # iPXE architectures to build, upload, register, and add matchers for.
  # Drop entries to skip an arch entirely (the playbook gates each stage).
  netboot_arches:
    - bios
    - uefi-x64
    - uefi-arm64

  # UEFI option-93 matcher table. Each entry is checked against the request's
  # vendor-class option to route UEFI clients to the right .efi file.
  # 0x0007 (EFI BC) is what most real-world x86_64 firmware actually sends;
  # 0x0009 (EFI x86-64) is the IANA-registered code; 0x000b is EFI ARM 64-bit.
  netboot_uefi_matchers:
    - { value: "0x0007", set: pxe-uefi-x64,   arch: uefi-x64 }
    - { value: "0x0009", set: pxe-uefi-x64,   arch: uefi-x64 }
    - { value: "0x000b", set: pxe-uefi-arm64, arch: uefi-arm64 }

  # Flash directory the binaries land in. Created by the upload stage.
  netboot_flash_dir: netboot
  ```

  Replace `REPLACE_WITH_LAN_DHCP_NAME` and `REPLACE_WITH_VLAN45_DHCP_NAME` with the names from step 1.

- [ ] **Step 3: Ensure `.gitignore` ignores the cache directory**

  In `/workspace/igou-ansible/.gitignore`, add (if not present):

  ```
  /.cache/
  ```

  Already-present entries that match (e.g., a broader pattern) are fine — confirm with `git check-ignore -v .cache/netboot-build/` (after creating an empty file there in a later task) that the path is ignored.

- [ ] **Step 4: Lint and commit**

  Run:

  ```bash
  yamllint igou-inventory/group_vars/routeros.yml
  ```

  Expected: clean (no errors).

  Run:

  ```bash
  ansible-lint --profile=production igou-inventory/group_vars/routeros.yml
  ```

  Expected: clean.

  Commit:

  ```bash
  git add igou-inventory/group_vars/routeros.yml .gitignore
  git commit -m "$(cat <<'EOF'
  Inventory: add netboot.xyz custom-binary build variables

  Variables consumed by the upcoming
  playbooks/routeros/deploy_netboot_binaries.yml. Source ref pinned to
  2.0.84; chainload target is the existing TrueNAS netbootxyz container
  at 10.10.45.242 over TFTP.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 2: Cleanup README + user_overrides template

**Files:**
- Create: `playbooks/routeros/files/netboot/cleanup-old-container.md`
- Create: `playbooks/routeros/templates/netboot/user_overrides.yml.j2`

The cleanup README is doc-only; it never gets templated or shipped anywhere by Ansible. It lives under `files/` because that's the conventional Ansible location for shipped-with-playbook static content, and the README is what the deploy playbook's header comment links to. The template is the actual contract between our inventory variables and the netboot.xyz upstream Ansible build.

- [ ] **Step 1: Create the cleanup README**

  Create directory `playbooks/routeros/files/netboot/` (mkdir is fine; keep the `.md` adjacent to where the playbook expects it).

  File: `playbooks/routeros/files/netboot/cleanup-old-container.md`

  ```markdown
  # Decommissioning the rb5009 netbootxyz container

  After `deploy_netboot_binaries.yml` has run successfully and you've
  smoke-tested PXE boot end-to-end (boot a real or virtual client on
  10.10.9.0/24 or 10.10.45.0/24, confirm it pulls the binary from rb5009
  and lands on the TrueNAS menu), tear down the rb5009 netbootxyz
  container manually.

  Each step is idempotent — run them all even on a partial cleanup.

  1. Stop and remove the container:

         /container stop [find root-dir=containers/netbootxyz]
         # wait until status=stopped, then:
         /container remove [find root-dir=containers/netbootxyz]

     This wipes containers/netbootxyz/ on flash (the writable layer
     holding /config).

  2. Remove the env list (only present if netbootxyz_env_extra was
     non-empty when the container was last deployed):

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

- [ ] **Step 2: Create the user_overrides template**

  Create directory `playbooks/routeros/templates/netboot/`.

  File: `playbooks/routeros/templates/netboot/user_overrides.yml.j2`

  ```yaml
  ---
  # Rendered by playbooks/routeros/tasks/netboot_build.yml from
  # group_vars/routeros.yml at build time. This file is mounted into the
  # netboot.xyz builder container at /ansible/user_overrides.yml and
  # consumed by the upstream site.yml -> netbootxyz role.
  #
  # Documentation of every key:
  #   https://github.com/netbootxyz/netboot.xyz/blob/master/user_overrides.yml

  boot_domain: "{{ netboot_chainload_host }}"
  bootloader_default: "{{ netboot_chainload_proto }}"
  site_name: "igou homelab"
  generate_menus: false
  generate_disks: true
  generate_checksums: false
  make_num_jobs: 4
  ```

- [ ] **Step 3: Lint**

  ```bash
  yamllint playbooks/routeros/templates/netboot/user_overrides.yml.j2
  ```

  Expected: clean. Note that yamllint understands the file as YAML even with the `.j2` extension because the Jinja directives here are bare scalars; if yamllint complains about them, that's a real issue.

  Render the template by hand to spot-check:

  ```bash
  python3 -c "
  from jinja2 import Template
  print(Template(open('playbooks/routeros/templates/netboot/user_overrides.yml.j2').read()).render(
      netboot_chainload_host='10.10.45.242',
      netboot_chainload_proto='tftp',
  ))
  "
  ```

  Expected output (verbatim):

  ```yaml
  ---
  # Rendered by playbooks/routeros/tasks/netboot_build.yml from
  # group_vars/routeros.yml at build time. This file is mounted into the
  # netboot.xyz builder container at /ansible/user_overrides.yml and
  # consumed by the upstream site.yml -> netbootxyz role.
  #
  # Documentation of every key:
  #   https://github.com/netbootxyz/netboot.xyz/blob/master/user_overrides.yml

  boot_domain: "10.10.45.242"
  bootloader_default: "tftp"
  site_name: "igou homelab"
  generate_menus: false
  generate_disks: true
  generate_checksums: false
  make_num_jobs: 4
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add playbooks/routeros/files/netboot/cleanup-old-container.md \
          playbooks/routeros/templates/netboot/user_overrides.yml.j2
  git commit -m "$(cat <<'EOF'
  Add netboot binaries: cleanup README and user_overrides template

  Cleanup README documents the manual rb5009 nbxyz container teardown
  steps for after the binary-based deploy is verified. user_overrides
  template is mounted into the netboot.xyz builder container at build
  time to embed the chainload URL.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 3: Orchestrator playbook + build stage

**Files:**
- Create: `playbooks/routeros/deploy_netboot_binaries.yml`
- Create: `playbooks/routeros/tasks/netboot_build.yml`

The orchestrator only includes the build stage in this task — that lets us syntax-check and smoke-build before ever touching the router. Subsequent tasks (4–6) append their stage to the orchestrator.

- [ ] **Step 1: Create the orchestrator with the build stage only**

  File: `playbooks/routeros/deploy_netboot_binaries.yml`

  ```yaml
  ---
  # Build custom netboot.xyz iPXE binaries on the control node, ship them
  # to the rb5009 for TFTP serving, and wire RouterOS DHCP so PXE clients
  # land on the binaries (which then chainload to the existing TrueNAS
  # netbootxyz container at tftp://10.10.45.242/menu.ipxe).
  #
  # Stages (each backed by a tag):
  #   build   -- podman run nbxyz/builder against our user_overrides.yml
  #   upload  -- net_put binaries to flash:/netboot/, register /ip tftp
  #   dhcp    -- /ip dhcp-server network/option/option-set/matcher
  #   verify  -- TFTP fetch checks + DHCP state assertion
  #
  # See docs/superpowers/specs/2026-05-08-netboot-binaries-build-design.md
  # for the full design and decisions log.
  #
  # The playbook does NOT decommission the rb5009 netbootxyz container
  # deployed by deploy_netbootxyz.yml. After verifying PXE boot end-to-end,
  # follow the manual cleanup steps in:
  #   playbooks/routeros/files/netboot/cleanup-old-container.md

  - name: Build and deploy custom netboot.xyz iPXE binaries
    hosts: "{{ host | default('routeros_netboot') }}"
    gather_facts: false
    serial: 1
    tasks:

      - name: Build stage
        ansible.builtin.import_tasks: tasks/netboot_build.yml
        tags: [build]
  ```

- [ ] **Step 2: Create the build stage task file**

  File: `playbooks/routeros/tasks/netboot_build.yml`

  ```yaml
  ---
  # Build the custom netboot.xyz iPXE binaries on the control node by
  # invoking the upstream Ansible build inside the official netbootxyz
  # builder container.
  #
  # Sets fact netboot_arches_resolved -- used by upload, dhcp, and verify
  # stages.
  #
  # Idempotency: a SHA256 of the rendered user_overrides plus the resolved
  # git SHA of the cloned netboot.xyz repo is recorded in MANIFEST.lastbuild
  # after each successful run. On re-run, if the hash matches AND every
  # expected output binary exists, the podman build is skipped.

  - name: Define arches map (local -> public file names)
    ansible.builtin.set_fact:
      _netboot_arches_map:
        bios:
          local_name: netboot.xyz.kpxe
          public_name: netboot.xyz.kpxe
        uefi-x64:
          local_name: netboot.xyz.efi
          public_name: netboot.xyz.efi
        uefi-arm64:
          local_name: netboot.xyz-arm64.efi
          public_name: netboot.xyz-arm64.efi

  - name: Resolve enabled arches into a structured list
    ansible.builtin.set_fact:
      netboot_arches_resolved: >-
        {{
          netboot_arches | map('extract', _netboot_arches_map)
                         | list
        }}

  - name: Ensure local build cache directory exists
    ansible.builtin.file:
      path: "{{ netboot_local_build_dir }}"
      state: directory
      mode: "0755"
    delegate_to: localhost

  - name: Ensure local build output directory exists
    ansible.builtin.file:
      path: "{{ netboot_local_build_dir }}/output"
      state: directory
      mode: "0755"
    delegate_to: localhost

  - name: Clone or update netboot.xyz source at the pinned ref
    ansible.builtin.git:
      repo: "{{ netboot_xyz_repo }}"
      dest: "{{ netboot_local_build_dir }}/src"
      version: "{{ netboot_xyz_ref }}"
      force: true
    delegate_to: localhost
    register: _netboot_git

  - name: Render user_overrides.yml into the source clone
    ansible.builtin.template:
      src: "{{ playbook_dir }}/templates/netboot/user_overrides.yml.j2"
      dest: "{{ netboot_local_build_dir }}/src/user_overrides.yml"
      mode: "0644"
    delegate_to: localhost
    register: _netboot_template

  - name: Compute a build-input hash (rendered overrides + git SHA)
    ansible.builtin.set_fact:
      _netboot_build_hash: >-
        {{
          (lookup('ansible.builtin.file',
                  netboot_local_build_dir ~ '/src/user_overrides.yml')
           ~ _netboot_git.after) | hash('sha256')
        }}
    delegate_to: localhost

  - name: Read prior build hash if present
    ansible.builtin.slurp:
      src: "{{ netboot_local_build_dir }}/MANIFEST.lastbuild"
    register: _netboot_lastbuild
    delegate_to: localhost
    failed_when: false
    changed_when: false

  - name: Stat each expected output binary
    ansible.builtin.stat:
      path: "{{ netboot_local_build_dir }}/output/ipxe/{{ item.local_name }}"
    delegate_to: localhost
    loop: "{{ netboot_arches_resolved }}"
    loop_control:
      label: "{{ item.public_name }}"
    register: _netboot_output_stat

  - name: Decide whether to invoke the builder container
    ansible.builtin.set_fact:
      _netboot_need_build: >-
        {{
          (_netboot_lastbuild.content | default('') | b64decode | trim)
            != _netboot_build_hash
          or
          (_netboot_output_stat.results
            | rejectattr('stat.exists', 'equalto', true) | list | length) > 0
        }}

  - name: Run the netboot.xyz builder container
    ansible.builtin.command:
      cmd: >-
        podman run --rm
        --pull=always
        -v {{ netboot_local_build_dir }}/src:/ansible:Z
        -v {{ netboot_local_build_dir }}/output:/var/www/html:Z
        {{ netboot_builder_image }}
        ansible-playbook -i localhost, /ansible/site.yml
    when: _netboot_need_build
    delegate_to: localhost
    changed_when: true
    register: _netboot_build_run

  - name: Re-stat each expected output binary after build
    ansible.builtin.stat:
      path: "{{ netboot_local_build_dir }}/output/ipxe/{{ item.local_name }}"
    delegate_to: localhost
    loop: "{{ netboot_arches_resolved }}"
    loop_control:
      label: "{{ item.public_name }}"
    register: _netboot_output_stat_post

  - name: Assert all expected binaries exist after build
    ansible.builtin.assert:
      that:
        - item.stat.exists
      fail_msg: >-
        Expected output binary
        {{ netboot_local_build_dir }}/output/ipxe/{{ item.item.local_name }}
        is missing after the build step. Check the builder container's
        ansible-playbook output (the prior task) for errors.
    loop: "{{ _netboot_output_stat_post.results }}"
    loop_control:
      label: "{{ item.item.public_name }}"

  - name: Persist build hash for next run
    ansible.builtin.copy:
      dest: "{{ netboot_local_build_dir }}/MANIFEST.lastbuild"
      content: "{{ _netboot_build_hash }}"
      mode: "0644"
    delegate_to: localhost
    when: _netboot_need_build

  - name: Write human-readable MANIFEST
    ansible.builtin.copy:
      dest: "{{ netboot_local_build_dir }}/MANIFEST"
      mode: "0644"
      content: |
        ref: {{ netboot_xyz_ref }}
        git_sha: {{ _netboot_git.after }}
        built_at: {{ ansible_date_time.iso8601 | default(lookup('pipe', 'date -u +%Y-%m-%dT%H:%M:%SZ')) }}
        artifacts:
        {% for item in netboot_arches_resolved %}
          - ipxe/{{ item.local_name }}
        {% endfor %}
    delegate_to: localhost
    when: _netboot_need_build
  ```

- [ ] **Step 3: Lint**

  ```bash
  yamllint playbooks/routeros/deploy_netboot_binaries.yml \
            playbooks/routeros/tasks/netboot_build.yml
  ```

  Expected: clean.

  ```bash
  ansible-lint --profile=production playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: clean. If ansible-lint flags `command-instead-of-module` for the `podman run` step, that's expected — there's no community.containers module that does what we need (read-only one-shot mounts), so leave it as `command:` and add a noqa comment if the rule is fatal under production profile:

  ```yaml
    - name: Run the netboot.xyz builder container  # noqa: command-instead-of-module
  ```

- [ ] **Step 4: Syntax-check**

  ```bash
  ansible-playbook --syntax-check \
      -i igou-inventory/inventory.yaml \
      playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: `playbook: playbooks/routeros/deploy_netboot_binaries.yml`.

- [ ] **Step 5: Smoke-build**

  This is the proof the build stage works end-to-end on the control node. Runs offline-relative to the rb5009 (no SSH to the router happens because the only task is `delegate_to: localhost`).

  ```bash
  ansible-navigator run \
      -i igou-inventory/inventory.yaml \
      --tags build \
      --pae false \
      --mode stdout \
      playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: takes 2–5 minutes on first run. On success, three files exist:

  ```bash
  ls -la .cache/netboot-build/output/ipxe/netboot.xyz.kpxe \
         .cache/netboot-build/output/ipxe/netboot.xyz.efi \
         .cache/netboot-build/output/ipxe/netboot.xyz-arm64.efi
  cat .cache/netboot-build/MANIFEST
  ```

  Each file should be on the order of 100KB–1MB. Re-run the same command immediately; expected output: the `Run the netboot.xyz builder container` task is skipped (idempotency working).

- [ ] **Step 6: Commit**

  ```bash
  git add playbooks/routeros/deploy_netboot_binaries.yml \
          playbooks/routeros/tasks/netboot_build.yml
  git commit -m "$(cat <<'EOF'
  Implement netboot binaries build stage

  Orchestrator playbook with the first stage: clone netboot.xyz at the
  pinned ref, render user_overrides.yml from inventory, run the
  netbootxyz/builder container to produce kpxe/efi/arm64.efi, and
  persist a build-input hash for idempotent re-runs.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 4: Upload stage

**Files:**
- Modify: `playbooks/routeros/deploy_netboot_binaries.yml` (append the import)
- Create: `playbooks/routeros/tasks/netboot_upload.yml`

This stage SCPs the binaries to `flash:/netboot/` on the rb5009 and registers each in `/ip tftp` so the public name (e.g., `netboot.xyz.kpxe`) maps to the on-flash path. It depends on `netboot_arches_resolved` being set by the build stage — if the operator runs `--tags upload` alone, the upload task file calls a helper to set the fact directly.

- [ ] **Step 1: Append the upload stage to the orchestrator**

  Open `playbooks/routeros/deploy_netboot_binaries.yml`. Below the existing `Build stage` task block, append:

  ```yaml
        - name: Upload stage
          ansible.builtin.import_tasks: tasks/netboot_upload.yml
          tags: [upload]
  ```

  (Same indentation as the existing `Build stage` block — under `tasks:`.)

- [ ] **Step 2: Create the upload task file**

  File: `playbooks/routeros/tasks/netboot_upload.yml`

  ```yaml
  ---
  # Ship built iPXE binaries from the control node's build cache to
  # flash:/netboot/ on the rb5009, then register each in /ip tftp so
  # PXE clients can fetch them by their public names.
  #
  # Honors netboot_arches_resolved (set by netboot_build.yml). When this
  # stage runs alone via --tags upload, the fact is recomputed from
  # netboot_arches and the static arches map.

  - name: Recompute arches map if running upload stage alone
    ansible.builtin.set_fact:
      _netboot_arches_map:
        bios:
          local_name: netboot.xyz.kpxe
          public_name: netboot.xyz.kpxe
        uefi-x64:
          local_name: netboot.xyz.efi
          public_name: netboot.xyz.efi
        uefi-arm64:
          local_name: netboot.xyz-arm64.efi
          public_name: netboot.xyz-arm64.efi
    when: netboot_arches_resolved is not defined

  - name: Recompute resolved arches list if running upload stage alone
    ansible.builtin.set_fact:
      netboot_arches_resolved: >-
        {{
          netboot_arches | map('extract', _netboot_arches_map) | list
        }}
    when: netboot_arches_resolved is not defined

  - name: Stat each local binary
    ansible.builtin.stat:
      path: "{{ netboot_local_build_dir }}/output/ipxe/{{ item.local_name }}"
    delegate_to: localhost
    loop: "{{ netboot_arches_resolved }}"
    loop_control:
      label: "{{ item.public_name }}"
    register: _netboot_local_stat

  - name: Assert each local binary exists
    ansible.builtin.assert:
      that:
        - item.stat.exists
      fail_msg: >-
        Local binary
        {{ netboot_local_build_dir }}/output/ipxe/{{ item.item.local_name }}
        is missing. Run the build stage first
        (--tags build).
    loop: "{{ _netboot_local_stat.results }}"
    loop_control:
      label: "{{ item.item.public_name }}"

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

  - name: Count router-side binaries matching local size
    community.routeros.command:
      commands:
        - >-
          /file print count-only where
          name="{{ netboot_flash_dir }}/{{ item.0.public_name }}"
          and size={{ item.1.stat.size }}
    register: _netboot_router_size_match
    changed_when: false
    loop: "{{ netboot_arches_resolved | zip(_netboot_local_stat.results) | list }}"
    loop_control:
      label: "{{ item.0.public_name }}"

  - name: Upload binary if size differs from router copy
    ansible.netcommon.net_put:
      src: "{{ netboot_local_build_dir }}/output/ipxe/{{ item.0.local_name }}"
      dest: "{{ netboot_flash_dir }}/{{ item.0.public_name }}"
    vars:
      ansible_command_timeout: 300
    when:
      - (_netboot_router_size_match.results[ansible_loop.index0].stdout[0] | trim | int) == 0
    loop: "{{ netboot_arches_resolved | zip(_netboot_local_stat.results) | list }}"
    loop_control:
      label: "{{ item.0.public_name }}"
      extended: true
    register: _netboot_uploads

  - name: Count tftp rows for each arch
    community.routeros.command:
      commands:
        - >-
          /ip tftp print count-only where
          req-filename="{{ item.public_name }}" and
          real-filename="{{ netboot_flash_dir }}/{{ item.public_name }}"
    register: _netboot_tftp_count
    changed_when: false
    loop: "{{ netboot_arches_resolved }}"
    loop_control:
      label: "{{ item.public_name }}"

  - name: Add /ip tftp row for each arch
    community.routeros.command:
      commands:
        - >-
          /ip tftp add
          req-filename="{{ item.public_name }}"
          real-filename="{{ netboot_flash_dir }}/{{ item.public_name }}"
          allow=yes read-only=yes
    when: (_netboot_tftp_count.results[ansible_loop.index0].stdout[0] | trim | int) == 0
    changed_when: true
    loop: "{{ netboot_arches_resolved }}"
    loop_control:
      label: "{{ item.public_name }}"
      extended: true
  ```

- [ ] **Step 3: Lint**

  ```bash
  yamllint playbooks/routeros/tasks/netboot_upload.yml playbooks/routeros/deploy_netboot_binaries.yml
  ansible-lint --profile=production playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: clean.

- [ ] **Step 4: Syntax-check**

  ```bash
  ansible-playbook --syntax-check \
      -i igou-inventory/inventory.yaml \
      playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: clean.

- [ ] **Step 5: Smoke-run upload stage**

  ```bash
  ansible-navigator run \
      -i igou-inventory/inventory.yaml \
      --tags upload \
      --pae false \
      --mode stdout \
      playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: three uploads on first run, all `/ip tftp add` tasks fire. Confirm on the router:

  ```bash
  ansible -i igou-inventory/inventory.yaml routeros_netboot \
      -m community.routeros.command \
      -a 'commands=["/file print where name~\"netboot/\""]'
  ansible -i igou-inventory/inventory.yaml routeros_netboot \
      -m community.routeros.command \
      -a 'commands=["/ip tftp print"]'
  ```

  The `/file print` should list `netboot/`, `netboot/netboot.xyz.kpxe`, `netboot/netboot.xyz.efi`, `netboot/netboot.xyz-arm64.efi`. The `/ip tftp print` should show three rows with matching `req-filename` and `real-filename`.

  Re-run the same playbook command. Expected: zero changed tasks (idempotency).

- [ ] **Step 6: Commit**

  ```bash
  git add playbooks/routeros/deploy_netboot_binaries.yml \
          playbooks/routeros/tasks/netboot_upload.yml
  git commit -m "$(cat <<'EOF'
  Implement netboot binaries upload stage

  net_put each built binary to flash:/netboot/ on rb5009, then register
  it in /ip tftp under its public filename. Idempotent via size compare
  for the upload and count-only for the tftp row.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 5: DHCP wiring stage

**Files:**
- Modify: `playbooks/routeros/deploy_netboot_binaries.yml` (append the import)
- Create: `playbooks/routeros/tasks/netboot_dhcp.yml`

This is the most state-modifying stage on the router. It does three things:
1. Sets `next-server` and `boot-file-name` at the network layer for each subnet in `netboot_subnets` (default = BIOS).
2. Creates `/ip dhcp-server option` and `/ip dhcp-server option/sets` rows for the UEFI overrides — global, conditional on the matching arch being in `netboot_arches`.
3. Creates `/ip dhcp-server matcher` rows per (subnet × matcher value) combination, scoped by `server`.

Each step has count-only / drift-detection gates so re-runs are no-ops.

- [ ] **Step 1: Append the dhcp stage to the orchestrator**

  Open `playbooks/routeros/deploy_netboot_binaries.yml`. Below the `Upload stage` block, append:

  ```yaml
        - name: DHCP stage
          ansible.builtin.import_tasks: tasks/netboot_dhcp.yml
          tags: [dhcp]
  ```

- [ ] **Step 2: Create the dhcp task file**

  File: `playbooks/routeros/tasks/netboot_dhcp.yml`

  ```yaml
  ---
  # Wire RouterOS DHCP so PXE clients on the configured subnets receive
  # next-server pointing at rb5009 and boot-file-name pointing at the
  # right binary for their architecture.
  #
  # Default path (BIOS): network-layer next-server + boot-file-name set
  # via /ip dhcp-server network. UEFI overrides go through option-sets
  # plus matchers because that's the only RouterOS mechanism for changing
  # boot-file-name per-client.
  #
  # All adds are gated on count-only checks for idempotency. Network-layer
  # set is gated on a print-then-compare drift check.

  # --- Network-layer fields (default = BIOS) -----------------------------------

  - name: Read existing network rows for our subnets
    community.routeros.command:
      commands:
        - >-
          /ip dhcp-server network print detail without-paging where
          address={{ item.subnet }}
    register: _netboot_network_existing
    changed_when: false
    loop: "{{ netboot_subnets }}"
    loop_control:
      label: "{{ item.subnet }}"

  - name: Set next-server + boot-file-name on each DHCP network
    community.routeros.command:
      commands:
        - >-
          /ip dhcp-server network set
          [find address={{ item.0.subnet }}]
          next-server={{ item.0.rb5009_ip }}
          boot-file-name=netboot.xyz.kpxe
    when: >-
      (item.1.stdout[0] | regex_search('(?m)^\\s*next-server:\\s*' ~ item.0.rb5009_ip ~ '\\s*$') is none)
      or
      (item.1.stdout[0] | regex_search('(?m)^\\s*boot-file-name:\\s*"?netboot\\.xyz\\.kpxe"?\\s*$') is none)
    changed_when: true
    loop: "{{ netboot_subnets | zip(_netboot_network_existing.results) | list }}"
    loop_control:
      label: "{{ item.0.subnet }}"

  # --- Option + option-set (global, conditional on UEFI arches enabled) -------

  - name: Count option pxe-bf-uefi-x64
    community.routeros.command:
      commands:
        - '/ip dhcp-server option print count-only where name=pxe-bf-uefi-x64'
    register: _netboot_opt_x64_count
    when: "'uefi-x64' in netboot_arches"
    changed_when: false

  - name: Add option pxe-bf-uefi-x64
    community.routeros.command:
      commands:
        - >-
          /ip dhcp-server option add
          code=67 name=pxe-bf-uefi-x64 value="'netboot.xyz.efi'"
    when:
      - "'uefi-x64' in netboot_arches"
      - (_netboot_opt_x64_count.stdout[0] | trim | int) == 0
    changed_when: true

  - name: Count option-set pxe-uefi-x64
    community.routeros.command:
      commands:
        - '/ip dhcp-server option/sets print count-only where name=pxe-uefi-x64'
    register: _netboot_set_x64_count
    when: "'uefi-x64' in netboot_arches"
    changed_when: false

  - name: Add option-set pxe-uefi-x64
    community.routeros.command:
      commands:
        - "/ip dhcp-server option/sets add name=pxe-uefi-x64 options=pxe-bf-uefi-x64"
    when:
      - "'uefi-x64' in netboot_arches"
      - (_netboot_set_x64_count.stdout[0] | trim | int) == 0
    changed_when: true

  - name: Count option pxe-bf-uefi-arm64
    community.routeros.command:
      commands:
        - '/ip dhcp-server option print count-only where name=pxe-bf-uefi-arm64'
    register: _netboot_opt_arm64_count
    when: "'uefi-arm64' in netboot_arches"
    changed_when: false

  - name: Add option pxe-bf-uefi-arm64
    community.routeros.command:
      commands:
        - >-
          /ip dhcp-server option add
          code=67 name=pxe-bf-uefi-arm64 value="'netboot.xyz-arm64.efi'"
    when:
      - "'uefi-arm64' in netboot_arches"
      - (_netboot_opt_arm64_count.stdout[0] | trim | int) == 0
    changed_when: true

  - name: Count option-set pxe-uefi-arm64
    community.routeros.command:
      commands:
        - '/ip dhcp-server option/sets print count-only where name=pxe-uefi-arm64'
    register: _netboot_set_arm64_count
    when: "'uefi-arm64' in netboot_arches"
    changed_when: false

  - name: Add option-set pxe-uefi-arm64
    community.routeros.command:
      commands:
        - "/ip dhcp-server option/sets add name=pxe-uefi-arm64 options=pxe-bf-uefi-arm64"
    when:
      - "'uefi-arm64' in netboot_arches"
      - (_netboot_set_arm64_count.stdout[0] | trim | int) == 0
    changed_when: true

  # --- Matchers (per DHCP server x matcher value) -----------------------------

  - name: Count matcher rows for each (server x value) pair
    community.routeros.command:
      commands:
        - >-
          /ip dhcp-server matcher print count-only where
          server={{ item.0.dhcp_server }} and
          code=93 and
          value="{{ item.1.value }}"
    register: _netboot_matcher_count
    changed_when: false
    when: item.1.arch in netboot_arches
    loop: "{{ netboot_subnets | product(netboot_uefi_matchers) | list }}"
    loop_control:
      label: "{{ item.0.dhcp_server }} {{ item.1.value }}"

  - name: Add matcher row for each (server x value) pair
    community.routeros.command:
      commands:
        - >-
          /ip dhcp-server matcher add
          name=match-{{ item.0.dhcp_server }}-{{ item.1.value | regex_replace('^0x', '') }}
          server={{ item.0.dhcp_server }}
          code=93 value="{{ item.1.value }}"
          option-set={{ item.1.set }}
    when:
      - item.1.arch in netboot_arches
      - not (_netboot_matcher_count.results[ansible_loop.index0].skipped | default(false))
      - (_netboot_matcher_count.results[ansible_loop.index0].stdout[0] | trim | int) == 0
    changed_when: true
    loop: "{{ netboot_subnets | product(netboot_uefi_matchers) | list }}"
    loop_control:
      label: "{{ item.0.dhcp_server }} {{ item.1.value }}"
      extended: true
  ```

- [ ] **Step 3: Lint**

  ```bash
  yamllint playbooks/routeros/tasks/netboot_dhcp.yml playbooks/routeros/deploy_netboot_binaries.yml
  ansible-lint --profile=production playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: clean. If ansible-lint flags `risky-shell-pipe` or similar on the regex_search, leave the regex intact (the values are inventory-driven, not user-input).

- [ ] **Step 4: Syntax-check**

  ```bash
  ansible-playbook --syntax-check \
      -i igou-inventory/inventory.yaml \
      playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: clean.

- [ ] **Step 5: Smoke-run dhcp stage**

  ```bash
  ansible-navigator run \
      -i igou-inventory/inventory.yaml \
      --tags dhcp \
      --pae false \
      --mode stdout \
      playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected on first run: 2 network-layer set tasks fire, 4 option/option-set adds fire (2 options + 2 option-sets), 6 matcher adds fire.

  Confirm on the router:

  ```bash
  ansible -i igou-inventory/inventory.yaml routeros_netboot \
      -m community.routeros.command \
      -a 'commands=["/ip dhcp-server network print detail where address=10.10.9.0/24","/ip dhcp-server network print detail where address=10.10.45.0/24"]'
  ansible -i igou-inventory/inventory.yaml routeros_netboot \
      -m community.routeros.command \
      -a 'commands=["/ip dhcp-server option print","/ip dhcp-server option/sets print","/ip dhcp-server matcher print"]'
  ```

  Each network row should report `next-server: <rb5009-ip>` and `boot-file-name: netboot.xyz.kpxe`. Two option rows, two option-set rows, six matcher rows.

  Re-run the same playbook command. Expected: zero changed tasks.

- [ ] **Step 6: Commit**

  ```bash
  git add playbooks/routeros/deploy_netboot_binaries.yml \
          playbooks/routeros/tasks/netboot_dhcp.yml
  git commit -m "$(cat <<'EOF'
  Implement netboot binaries DHCP wiring stage

  Network-layer next-server + boot-file-name (BIOS default) per subnet,
  plus option/option-set/matcher entries for UEFI x86_64 (0x0007 +
  0x0009) and UEFI arm64 (0x000b). Idempotent via drift detection on
  the network row and count-only on the option/option-set/matcher rows.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 6: Verify stage

**Files:**
- Modify: `playbooks/routeros/deploy_netboot_binaries.yml` (append the import)
- Create: `playbooks/routeros/tasks/netboot_verify.yml`

Three checks: TFTP fetch from rb5009 (size compare), TFTP fetch of `menu.ipxe` from TrueNAS (sanity-check the chainload target), DHCP-state assertion via `print` parse.

- [ ] **Step 1: Append the verify stage to the orchestrator**

  Open `playbooks/routeros/deploy_netboot_binaries.yml`. Below the `DHCP stage` block, append:

  ```yaml
        - name: Verify stage
          ansible.builtin.import_tasks: tasks/netboot_verify.yml
          tags: [verify]
  ```

- [ ] **Step 2: Create the verify task file**

  File: `playbooks/routeros/tasks/netboot_verify.yml`

  ```yaml
  ---
  # Verify the deployment end-to-end:
  #   1. Fetch each binary from rb5009 over TFTP (size compare).
  #   2. Fetch menu.ipxe from the TrueNAS netbootxyz container over TFTP
  #      and assert it begins with #!ipxe (chainload target alive).
  #   3. Read /ip dhcp-server network and matcher rows, assert that
  #      next-server, boot-file-name, and the expected matchers exist.

  - name: Recompute arches map if running verify stage alone
    ansible.builtin.set_fact:
      _netboot_arches_map:
        bios:
          local_name: netboot.xyz.kpxe
          public_name: netboot.xyz.kpxe
        uefi-x64:
          local_name: netboot.xyz.efi
          public_name: netboot.xyz.efi
        uefi-arm64:
          local_name: netboot.xyz-arm64.efi
          public_name: netboot.xyz-arm64.efi
    when: netboot_arches_resolved is not defined

  - name: Recompute resolved arches list if running verify stage alone
    ansible.builtin.set_fact:
      netboot_arches_resolved: >-
        {{
          netboot_arches | map('extract', _netboot_arches_map) | list
        }}
    when: netboot_arches_resolved is not defined

  - name: Stat each local binary (for size compare)
    ansible.builtin.stat:
      path: "{{ netboot_local_build_dir }}/output/ipxe/{{ item.local_name }}"
    delegate_to: localhost
    loop: "{{ netboot_arches_resolved }}"
    loop_control:
      label: "{{ item.public_name }}"
    register: _netboot_local_stat_verify

  - name: Ensure local TFTP fetch directory exists
    ansible.builtin.file:
      path: "{{ netboot_local_build_dir }}/verify"
      state: directory
      mode: "0755"
    delegate_to: localhost

  - name: Fetch each binary from rb5009 over TFTP
    ansible.builtin.command:
      cmd: >-
        curl -sf
        -o {{ netboot_local_build_dir }}/verify/{{ item.public_name }}
        tftp://{{ netboot_subnets[0].rb5009_ip }}/{{ item.public_name }}
    delegate_to: localhost
    changed_when: false
    loop: "{{ netboot_arches_resolved }}"
    loop_control:
      label: "{{ item.public_name }}"

  - name: Stat each fetched binary
    ansible.builtin.stat:
      path: "{{ netboot_local_build_dir }}/verify/{{ item.public_name }}"
    delegate_to: localhost
    loop: "{{ netboot_arches_resolved }}"
    loop_control:
      label: "{{ item.public_name }}"
    register: _netboot_fetched_stat

  - name: Assert each fetched binary matches the local build size
    ansible.builtin.assert:
      that:
        - item.0.stat.size == item.1.stat.size
      fail_msg: >-
        Size mismatch for {{ item.0.item.public_name }}:
        rb5009 served {{ item.0.stat.size }} bytes,
        local build is {{ item.1.stat.size }} bytes.
        Re-run --tags upload and check /ip tftp print on the router.
    loop: "{{ _netboot_fetched_stat.results | zip(_netboot_local_stat_verify.results) | list }}"
    loop_control:
      label: "{{ item.0.item.public_name }}"

  - name: Fetch menu.ipxe from TrueNAS netbootxyz over TFTP
    ansible.builtin.command:
      cmd: >-
        curl -sf
        -o {{ netboot_local_build_dir }}/verify/menu.ipxe
        tftp://{{ netboot_chainload_host }}/menu.ipxe
    delegate_to: localhost
    changed_when: false

  - name: Slurp first 32 bytes of menu.ipxe
    ansible.builtin.command:
      cmd: head -c 32 {{ netboot_local_build_dir }}/verify/menu.ipxe
    delegate_to: localhost
    changed_when: false
    register: _netboot_menu_head

  - name: Assert menu.ipxe begins with the iPXE shebang
    ansible.builtin.assert:
      that:
        - "'#!ipxe' in _netboot_menu_head.stdout"
      fail_msg: >-
        Fetched {{ netboot_chainload_host }}/menu.ipxe but it does not
        begin with #!ipxe. The TrueNAS netbootxyz container is likely
        down or its dnsmasq is not serving menu.ipxe.

  - name: Read DHCP network rows for assertion
    community.routeros.command:
      commands:
        - >-
          /ip dhcp-server network print detail without-paging where
          address={{ item.subnet }}
    register: _netboot_network_verify
    changed_when: false
    loop: "{{ netboot_subnets }}"
    loop_control:
      label: "{{ item.subnet }}"

  - name: Assert next-server + boot-file-name on each network
    ansible.builtin.assert:
      that:
        - item.1.stdout[0] | regex_search('(?m)^\\s*next-server:\\s*' ~ item.0.rb5009_ip ~ '\\s*$') is not none
        - item.1.stdout[0] | regex_search('(?m)^\\s*boot-file-name:\\s*"?netboot\\.xyz\\.kpxe"?\\s*$') is not none
      fail_msg: >-
        DHCP network row for {{ item.0.subnet }} is missing the expected
        next-server={{ item.0.rb5009_ip }} or boot-file-name=netboot.xyz.kpxe.
    loop: "{{ netboot_subnets | zip(_netboot_network_verify.results) | list }}"
    loop_control:
      label: "{{ item.0.subnet }}"

  - name: Read DHCP matcher count for each expected (server, value) pair
    community.routeros.command:
      commands:
        - >-
          /ip dhcp-server matcher print count-only where
          server={{ item.0.dhcp_server }} and
          code=93 and
          value="{{ item.1.value }}"
    register: _netboot_matcher_verify
    changed_when: false
    when: item.1.arch in netboot_arches
    loop: "{{ netboot_subnets | product(netboot_uefi_matchers) | list }}"
    loop_control:
      label: "{{ item.0.dhcp_server }} {{ item.1.value }}"

  - name: Assert each expected matcher exists
    ansible.builtin.assert:
      that:
        - (item.stdout[0] | trim | int) >= 1
      fail_msg: >-
        Missing matcher on server {{ item.item.0.dhcp_server }}
        for code=93 value={{ item.item.1.value }}.
    when:
      - not (item.skipped | default(false))
    loop: "{{ _netboot_matcher_verify.results }}"
    loop_control:
      label: "{{ item.item.0.dhcp_server }} {{ item.item.1.value }}"
  ```

- [ ] **Step 3: Lint**

  ```bash
  yamllint playbooks/routeros/tasks/netboot_verify.yml playbooks/routeros/deploy_netboot_binaries.yml
  ansible-lint --profile=production playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: clean.

- [ ] **Step 4: Syntax-check**

  ```bash
  ansible-playbook --syntax-check \
      -i igou-inventory/inventory.yaml \
      playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: clean.

- [ ] **Step 5: Smoke-run verify stage**

  ```bash
  ansible-navigator run \
      -i igou-inventory/inventory.yaml \
      --tags verify \
      --pae false \
      --mode stdout \
      playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: every assertion passes. Three TFTP fetches from rb5009 succeed, one TFTP fetch from TrueNAS succeeds, all DHCP assertions pass.

  If the TrueNAS TFTP fetch hangs: confirm `10.10.45.242:69/udp` is reachable from the control node. From the control node:

  ```bash
  curl -v -o /tmp/menu.ipxe tftp://10.10.45.242/menu.ipxe
  ```

  If THAT fails, the chainload would also fail at boot — fix the TrueNAS netbootxyz container before continuing.

- [ ] **Step 6: Commit**

  ```bash
  git add playbooks/routeros/deploy_netboot_binaries.yml \
          playbooks/routeros/tasks/netboot_verify.yml
  git commit -m "$(cat <<'EOF'
  Implement netboot binaries verify stage

  TFTP-fetch each binary from rb5009 and assert size matches the local
  build, fetch menu.ipxe from the TrueNAS chainload target and assert
  the iPXE shebang, then read /ip dhcp-server network + matcher rows
  and assert all expected entries.

  Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 7: End-to-end smoke and documentation

**Files:**
- (read-only verification — no edits in this task unless smoke run reveals an issue)

This task is the operator running the entire playbook from a clean state, then doing a manual PXE boot to confirm the chain works end-to-end.

- [ ] **Step 1: Full playbook re-run**

  ```bash
  ansible-navigator run \
      -i igou-inventory/inventory.yaml \
      --pae false \
      --mode stdout \
      playbooks/routeros/deploy_netboot_binaries.yml
  ```

  Expected: zero changed tasks (all four stages idempotent on a system that's already converged), all assertions pass.

- [ ] **Step 2: Manual PXE smoke test**

  Boot a UEFI VM (e.g., libvirt with `os.firmware=efi`) on the `10.10.45.0/24` subnet, with PXE boot enabled. Watch for:

  - DHCP offer reports `next-server: 10.10.45.1`, `filename: netboot.xyz.efi`
  - TFTP transfer of `netboot.xyz.efi` from rb5009 succeeds
  - iPXE banner appears, then chains to `tftp://10.10.45.242/menu.ipxe`
  - netboot.xyz menu renders

  If the menu renders, the deployment is functional end-to-end. If only the BIOS path matters in your immediate use case, do the same test with a BIOS VM and confirm the `.kpxe` path.

  This step is not automatable from the playbook — it's a real client booting against the production DHCP server. Document the result (which client OS/firmware was used, which subnet) in your operator notes.

- [ ] **Step 3: Reminder — manual cleanup of the previous container**

  After the smoke test passes, follow the steps in
  `playbooks/routeros/files/netboot/cleanup-old-container.md` to decommission
  the rb5009 netbootxyz container deployed by `deploy_netbootxyz.yml`.

  This is intentionally NOT automated — see the spec's Decisions log.

- [ ] **Step 4: Final verification of repo state**

  ```bash
  git log --oneline -8
  git status
  ```

  Expected: 6 new commits on this branch (one per Task 1–6), working tree clean except for the pre-existing untracked files from the prior nbxyz-container session and the `.cache/netboot-build/` directory (gitignored).

  ```bash
  ansible-lint --profile=production .
  yamllint .
  ```

  Both should be clean across the repo (or at least no NEW failures vs. before this branch).

---

## Self-review checklist (run before declaring done)

- All four stage files exist and are imported by the orchestrator under their respective tags (`build`, `upload`, `dhcp`, `verify`).
- The full playbook is idempotent — a second run reports zero changed tasks.
- The verify stage's assertions cover: each binary size matches, menu.ipxe begins with `#!ipxe`, every expected DHCP network row has the right `next-server`/`boot-file-name`, every expected matcher exists.
- `netboot_arches` removal is observed by all four stages (build skips disabled arch artifacts, upload skips them, dhcp skips its option/option-set/matcher gates, verify skips the corresponding assertions).
- The cleanup README is referenced from the orchestrator's header comment.
- `.cache/netboot-build/` is gitignored and does not show up in `git status`.
- No `community.routeros.command` task is missing `register` + `changed_when: false` if it's read-only, or `changed_when: true` if it mutates the router.
