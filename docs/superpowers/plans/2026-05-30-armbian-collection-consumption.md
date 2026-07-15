# Armbian Collection Consumption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consume the `david_igou.armbian` collection (pinned `v0.0.3-alpha`) from this repo by recreating the Armbian SBC lifecycle orchestration here — invoking the collection's roles, with homelab-owned rb5009 transport — runnable both locally and as AAP job templates.

**Architecture:** The collection's 8 roles carry the complex, reusable logic and arrive via a `requirements.yml` git pin. This repo owns the thin orchestration glue (config resolution, retry loops) and all RouterOS/rb5009 transport, vendored under `playbooks/armbian/`. Phase 1 delivers transport-free flows + bootstrap (proves the requirements→EE→AAP loop); Phase 2 adds the boot-mode flows, native transport, and a destructive opt-in fleet e2e test.

**Tech Stack:** Ansible (ansible-core 2.20.x via `igou-awx-ee`), `ansible-navigator`/`ansible-playbook`, `ansible-lint --profile=production`, `yamllint`, `community.routeros`, `ansible.netcommon`, `ansible.posix`. AAP config-as-code lives in the symlinked `igou-inventory` repo.

**Spec:** `docs/superpowers/specs/2026-05-30-armbian-collection-consumption-design.md`

---

## Conventions for every task

This repo has no unit-test framework for playbooks. The "test" gate for each playbook/task-file is:

```bash
# 1. collection must be installed locally so FQCN roles resolve
ansible-galaxy collection install -r requirements.yml --force

# 2. syntax parse
ansible-playbook --syntax-check <playbook> -i igou-inventory/inventory.yaml

# 3. lint (production profile, same as pre-commit)
ansible-lint --profile=production <file>

# 4. yaml lint
yamllint <file>
```

All YAML files start with `---`, use 2-space indent, YAML 1.2 booleans (`true`/`false`), FQCN module names, and a `name:` on every task (ansible-lint production requires these). Task-include files (under `tasks/`, `transport/`) are linted as part of the playbook that includes them; lint them directly too.

**Runtime caveat:** the boot-mode and e2e flows drive real SBC hardware over RouterOS and depend on per-board inventory vars that live in `igou-inventory` (a **separate workstream** — see spec "Dependencies"). They are **not** runtime-verifiable in CI. Their per-task gate is syntax-check + lint only; a note in each commit flags that hardware runtime validation is pending inventory population.

---

## File Structure

**Created in this repo (`/workspace/igou-ansible`):**

```
playbooks/armbian/
  bootstrap.yaml                      # Task 2
  tasks/_resolve_board_config.yml     # Task 3
  tasks/_resolve_build_profile.yml    # Task 4
  tasks/_resolve_rootfs_src.yml       # Task 5
  build_and_publish.yaml              # Task 6
  stage_netboot_assets.yaml           # Task 7
  provision_local_disk.yaml           # Task 8
  transport/poe_cycle.yml             # Task 10
  transport/upload_file.yml           # Task 10
  transport/upload_pxelinux_cfg.yml   # Task 10
  transport/plumbing_check.yml        # Task 10
  tasks/cold_boot_single_attempt.yml  # Task 11
  tasks/cold_boot_with_retry.yml      # Task 11
  tasks/wait_for_ssh.yml              # Task 11
  tasks/render_and_upload_pxelinux.yml# Task 12
  converge_boot_mode.yaml             # Task 13
  set_boot_mode.yaml                  # Task 14
  reprovision_to_local.yaml           # Task 15
  tests/fleet_e2e.yaml                # Task 16
```

**Modified in this repo:**
- `requirements.yml` — add the collection (Task 1)
- delete `playbooks/armbian-firstboot.yaml` (Task 2)

**Modified in the symlinked `igou-inventory` repo:**
- `group_vars/aap/job_templates.yml` — repoint + add templates (Task 9, Task 17)

---

# PHASE 1 — Dependency + transport-free flows

## Task 1: Add the collection to requirements.yml

**Files:**
- Modify: `requirements.yml` (append under `collections:`)

- [ ] **Step 1: Add the git collection entry**

Append to the `collections:` list in `requirements.yml`, immediately after the `prometheus.prometheus` entry (matching the existing `ansible-truenas` git pattern):

```yaml
  - name: https://github.com/david-igou/ansible-collection-armbian.git
    type: git
    version: v0.0.3-alpha
```

- [ ] **Step 2: Install and verify the collection resolves**

Run:
```bash
ansible-galaxy collection install -r requirements.yml --force
ansible-galaxy collection list | grep -i armbian
```
Expected: a line `david_igou.armbian 0.0.3-alpha` (the git tag installs as version `0.0.3-alpha`).

- [ ] **Step 3: yamllint requirements**

Run: `yamllint requirements.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add requirements.yml
git commit -m "feat(armbian): pin david_igou.armbian collection at v0.0.3-alpha

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Bootstrap playbook (replaces armbian-firstboot.yaml)

The collection's `bootstrap_armbian` role creates the user, passwordless sudo, authorized_keys, removes the Armbian TUI prompt, and disables `PasswordAuthentication`. It does **not** set the hostname, disable root SSH login, or disable the root account — three behaviours today's `playbooks/armbian-firstboot.yaml` has. The new playbook calls the role, then adds those three as parity tasks.

**Files:**
- Create: `playbooks/armbian/bootstrap.yaml`
- Delete: `playbooks/armbian-firstboot.yaml`

- [ ] **Step 1: Write `playbooks/armbian/bootstrap.yaml`**

```yaml
---
# Bootstrap a freshly flashed Armbian board: provision the automation
# user (SSH-key auth + passwordless sudo) via the collection role, then
# apply homelab parity hardening the role does not cover (hostname, root
# SSH lockout, root account disable) to match the retired
# playbooks/armbian-firstboot.yaml.
#
# Connects as root with the Armbian default password until the user is
# provisioned. Override the defaults below in inventory or via -e.
#
# Usage:
#   ansible-playbook playbooks/armbian/bootstrap.yaml \
#     -i igou-inventory/inventory.yaml -e target_hosts=rock-5b-01
- name: Bootstrap Armbian automation user
  hosts: "{{ target_hosts | default('boards') }}"
  gather_facts: false
  vars:
    ansible_user: root
    ansible_password: "{{ armbian_default_password | default('1234') }}"
    ansible_ssh_common_args: "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
    ansible_become: false
    armbian_bootstrap_user: igou
    armbian_bootstrap_ssh_keys:
      - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINHO7UsiIgAepf5+s2z+1CbPQf2eqJo8aNK/vT9Oaf4B"
      - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOWgNfV1zdod84sj28d+z7YBLkaD5ZImElWt8zHw+u7/"
  roles:
    - role: david_igou.armbian.bootstrap_armbian

- name: Homelab parity hardening (hostname, root lockout)
  hosts: "{{ target_hosts | default('boards') }}"
  gather_facts: false
  become: true
  vars:
    ansible_user: "{{ armbian_bootstrap_user | default('igou') }}"
  tasks:
    - name: Set /etc/hostname when new_hostname provided
      ansible.builtin.copy:
        dest: /etc/hostname
        content: "{{ new_hostname }}\n"
        owner: root
        group: root
        mode: "0644"
      when: new_hostname is defined

    - name: Disable root login via ssh
      ansible.builtin.lineinfile:
        path: /etc/ssh/sshd_config
        regexp: "^#?PermitRootLogin"
        line: "PermitRootLogin no"
        state: present
        backup: true
      notify: Restart sshd

    - name: Disable root account password
      ansible.builtin.user:
        name: root
        password: "!"

  handlers:
    - name: Restart sshd
      ansible.builtin.service:
        name: sshd
        state: restarted
```

- [ ] **Step 2: Delete the old playbook**

Run: `git rm playbooks/armbian-firstboot.yaml`

- [ ] **Step 3: Syntax-check + lint**

Run:
```bash
ansible-playbook --syntax-check playbooks/armbian/bootstrap.yaml -i igou-inventory/inventory.yaml
ansible-lint --profile=production playbooks/armbian/bootstrap.yaml
yamllint playbooks/armbian/bootstrap.yaml
```
Expected: syntax OK; lint clean. (Syntax-check resolves the `david_igou.armbian.bootstrap_armbian` role only because Task 1 installed the collection.)

- [ ] **Step 4: Commit**

```bash
git add playbooks/armbian/bootstrap.yaml
git commit -m "feat(armbian): bootstrap playbook via collection role, retire armbian-firstboot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `_resolve_board_config.yml` include

**Files:**
- Create: `playbooks/armbian/tasks/_resolve_board_config.yml`

- [ ] **Step 1: Write the file (verbatim recreation of the collection's resolver)**

```yaml
---
# Per-host resolver: merges three inventory layers into the fact
# armbian_board_config. Run once per host that needs hardware facts.
# Layers (low → high precedence):
#   armbian_board_config_family — group_vars/<family>.yml
#   armbian_board_config_model  — group_vars/<model_group>.yml
#   armbian_board_config_host   — host_vars/<host>.yml
- name: Resolve effective armbian_board_config (family then model then host)
  ansible.builtin.set_fact:
    armbian_board_config: >-
      {{
        (armbian_board_config_family | default({}))
        | combine(armbian_board_config_model | default({}), recursive=true)
        | combine(armbian_board_config_host  | default({}), recursive=true)
      }}

- name: Assert required hardware fields are present after merge
  ansible.builtin.assert:
    that:
      - armbian_board_config.armbian_board_name | default('') | length > 0
      - armbian_board_config.dtb | default('') | length > 0
      - armbian_board_config.console | default('') | length > 0
    fail_msg: >-
      Host {{ inventory_hostname }}: armbian_board_config missing required
      field(s) after merge (armbian_board_name, dtb, console). Check
      group_vars/<family>.yml, group_vars/<model_group>.yml, and
      host_vars/{{ inventory_hostname }}.yml.
```

- [ ] **Step 2: yamllint**

Run: `yamllint playbooks/armbian/tasks/_resolve_board_config.yml`
Expected: no errors. (ansible-lint validates this file when a playbook includes it; lint runs in Task 6.)

- [ ] **Step 3: Commit**

```bash
git add playbooks/armbian/tasks/_resolve_board_config.yml
git commit -m "feat(armbian): board-config resolver include

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `_resolve_build_profile.yml` include

**Files:**
- Create: `playbooks/armbian/tasks/_resolve_build_profile.yml`

- [ ] **Step 1: Write the file (verbatim recreation)**

```yaml
---
# Per-host resolver: merges four layers into the fact armbian_build.
# Layers (low → high): armbian_build_defaults (group_vars/all.yml),
# armbian_build_family, armbian_build_model, armbian_build_host.
# Scalars + compile_args merge via recursive combine; userpatches are
# list-concatenated with a duplicate-dest hard fail.
- name: Assert armbian_build_defaults is defined (expected from group_vars/all.yml)
  ansible.builtin.assert:
    that:
      - armbian_build_defaults is defined
      - armbian_build_defaults | length > 0
    fail_msg: >-
      armbian_build_defaults is undefined. Set it in group_vars/all.yml.
      Expected keys: release, ref, min_free_gb, timeout, compile_args, userpatches.

- name: Merge scalars + compile_args via recursive combine
  ansible.builtin.set_fact:
    __armbian_build_scalars: >-
      {{
        (armbian_build_defaults | default({}))
        | combine(armbian_build_family | default({}), recursive=true)
        | combine(armbian_build_model  | default({}), recursive=true)
        | combine(armbian_build_host   | default({}), recursive=true)
      }}

- name: Concatenate userpatches across layers
  ansible.builtin.set_fact:
    __armbian_build_userpatches_concat: >-
      {{
        (armbian_build_defaults.userpatches | default([]))
        + (armbian_build_family.userpatches | default([]))
        + (armbian_build_model.userpatches  | default([]))
        + (armbian_build_host.userpatches   | default([]))
      }}

- name: Fail if any userpatch dest appears more than once across layers
  ansible.builtin.assert:
    that:
      - >-
        (__armbian_build_userpatches_concat | map(attribute='dest') | list
         | unique | length) == (__armbian_build_userpatches_concat | length)
    fail_msg: >-
      Host {{ inventory_hostname }}: duplicate userpatch dest across
      armbian_build layers. dests =
      {{ __armbian_build_userpatches_concat | map(attribute='dest') | list }}.

- name: Assemble resolved armbian_build
  ansible.builtin.set_fact:
    armbian_build: >-
      {{
        __armbian_build_scalars
        | combine({'userpatches': __armbian_build_userpatches_concat})
      }}
```

- [ ] **Step 2: yamllint**

Run: `yamllint playbooks/armbian/tasks/_resolve_build_profile.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add playbooks/armbian/tasks/_resolve_build_profile.yml
git commit -m "feat(armbian): build-profile resolver include

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `_resolve_rootfs_src.yml` include

This resolver is verbatim from the collection (host_vars value → published manifest on the netboot server → derived HTTP URL, else fail). Copy it exactly from the pinned tag rather than retyping.

**Files:**
- Create: `playbooks/armbian/tasks/_resolve_rootfs_src.yml`

- [ ] **Step 1: Fetch the verbatim file from the pinned tag**

Run:
```bash
gh api "repos/david-igou/ansible-collection-armbian/contents/playbooks/tasks/_resolve_rootfs_src.yml?ref=v0.0.3-alpha" \
  --jq '.content' | base64 -d > playbooks/armbian/tasks/_resolve_rootfs_src.yml
```

- [ ] **Step 2: Verify it begins with `---` and references `armbian_netboot_server_group`, `armbian_assets_base_url`, `armbian_nfs_assets_export`**

Run: `grep -E 'armbian_(netboot_server_group|assets_base_url|nfs_assets_export)' playbooks/armbian/tasks/_resolve_rootfs_src.yml`
Expected: matches for all three variable names (confirms the correct file landed).

- [ ] **Step 3: yamllint**

Run: `yamllint playbooks/armbian/tasks/_resolve_rootfs_src.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/armbian/tasks/_resolve_rootfs_src.yml
git commit -m "feat(armbian): rootfs-src resolver include (vendored from v0.0.3-alpha)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: `build_and_publish.yaml`

Recreates the collection's per-host image build + publish pipeline, calling the `image_build` role and using the documented direct-rsync-over-ssh staging/publish (not `ansible.posix.synchronize`).

**Files:**
- Create: `playbooks/armbian/build_and_publish.yaml`

- [ ] **Step 1: Write the file**

```yaml
---
# Per-host custom Armbian image build pipeline.
#   1. boards group: resolve board_config + build profile, mark opt-in.
#   2. builders group: image_build per opted-in host, rsync output to controller.
#   3. netboot_server group: publish staged per-host dirs to images/<host>/.
# A host opts in by setting any of armbian_build_{family,model,host}.
- name: Resolve effective configs per board host
  hosts: "{{ armbian_boards_group | default('boards') }}"
  gather_facts: false
  tasks:
    - name: Resolve effective armbian_board_config
      ansible.builtin.include_tasks: tasks/_resolve_board_config.yml
    - name: Resolve effective armbian_build
      ansible.builtin.include_tasks: tasks/_resolve_build_profile.yml
    - name: Set custom-build opt-in fact
      ansible.builtin.set_fact:
        __wants_custom_build: >-
          {{ (armbian_build_family is defined)
             or (armbian_build_model is defined)
             or (armbian_build_host is defined) }}

- name: Build custom images per opted-in host; stage on controller
  hosts: "{{ armbian_builders_group | default('armbian_builders') }}"
  gather_facts: false
  vars:
    __build_hosts: >-
      {{ groups[armbian_boards_group | default('boards')] | map('extract', hostvars)
         | selectattr('__wants_custom_build', 'equalto', true)
         | map(attribute='inventory_hostname') | list }}
  tasks:
    - name: Report build targets
      ansible.builtin.debug:
        msg: "Will build images for: {{ __build_hosts }}"
        verbosity: 1

    - name: Build per opted-in host
      ansible.builtin.include_role:
        name: david_igou.armbian.image_build
      vars:
        armbian_build_host: "{{ item }}"
        armbian_build_board: "{{ hostvars[item].armbian_board_config.armbian_board_name }}"
        armbian_build_branch: "{{ hostvars[item].armbian_build.branch }}"
        armbian_build_release: "{{ hostvars[item].armbian_build.release }}"
        armbian_build_ref: "{{ hostvars[item].armbian_build.ref }}"
        armbian_build_userpatches: "{{ hostvars[item].armbian_build.userpatches }}"
        armbian_build_compile_args: "{{ hostvars[item].armbian_build.compile_args }}"
        armbian_build_timeout: "{{ hostvars[item].armbian_build.timeout }}"
        armbian_build_min_free_gb: "{{ hostvars[item].armbian_build.min_free_gb }}"
      loop: "{{ __build_hosts }}"

    - name: Stage per-host build dir on controller via direct rsync
      ansible.builtin.command:
        argv:
          - rsync
          - --archive
          - --compress
          - --delete
          - --mkpath
          - --rsh
          - "ssh -p {{ ansible_port | default(22) }} -o IdentityAgent=none -o StrictHostKeyChecking=accept-new"
          - "{{ ansible_user }}@{{ ansible_host | default(inventory_hostname) }}:{{ armbian_build_output_dir }}/{{ item }}/"
          - "/tmp/armbian_publish/{{ item }}/"
      delegate_to: localhost
      connection: local
      changed_when: true
      loop: "{{ __build_hosts }}"

- name: Publish staged per-host directories to netboot server
  hosts: "{{ armbian_netboot_server_group | default('netboot_server') }}"
  gather_facts: false
  tasks:
    - name: Push staged per-host image + manifest (sudo on receive)
      ansible.builtin.command:
        argv:
          - rsync
          - --archive
          - --compress
          - --mkpath
          - --rsync-path=sudo rsync
          - --rsh
          - "ssh -p {{ ansible_port | default(22) }} -o IdentityAgent=none -o StrictHostKeyChecking=accept-new"
          - "/tmp/armbian_publish/{{ item }}/"
          - "{{ ansible_user }}@{{ inventory_hostname }}:{{ armbian_nfs_assets_export }}/images/{{ item }}/"
      delegate_to: localhost
      connection: local
      changed_when: true
      loop: >-
        {{ groups[armbian_boards_group | default('boards')] | map('extract', hostvars)
           | selectattr('__wants_custom_build', 'equalto', true)
           | map(attribute='inventory_hostname') | list }}
```

- [ ] **Step 2: Syntax-check + lint (validates Tasks 3 & 4 includes too)**

Run:
```bash
ansible-playbook --syntax-check playbooks/armbian/build_and_publish.yaml -i igou-inventory/inventory.yaml
ansible-lint --profile=production playbooks/armbian/build_and_publish.yaml
yamllint playbooks/armbian/build_and_publish.yaml
```
Expected: syntax OK; lint clean.

- [ ] **Step 3: Commit**

```bash
git add playbooks/armbian/build_and_publish.yaml
git commit -m "feat(armbian): build_and_publish flow invoking image_build role

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: `stage_netboot_assets.yaml`

**Files:**
- Create: `playbooks/armbian/stage_netboot_assets.yaml`

- [ ] **Step 1: Write the file**

```yaml
---
# Per-host rootfs provisioning on the netboot server.
#   1. boards group: resolve board_config + rootfs_src per host.
#   2. netboot_server group: rootfs_provision role per board host.
- name: Resolve effective board configs and rootfs sources per board host
  hosts: "{{ armbian_boards_group | default('boards') }}"
  gather_facts: false
  tasks:
    - name: Resolve effective armbian_board_config
      ansible.builtin.include_tasks: tasks/_resolve_board_config.yml
    - name: Resolve armbian_rootfs_src per host
      ansible.builtin.include_tasks: tasks/_resolve_rootfs_src.yml

- name: Provision per-host NFS rootfs + TFTP artifacts
  hosts: "{{ armbian_netboot_server_group | default('netboot_server') }}"
  become: true
  gather_facts: false
  tasks:
    - name: Run rootfs_provision per board host
      ansible.builtin.include_role:
        name: david_igou.armbian.rootfs_provision
      vars:
        armbian_rootfs_src: "{{ hostvars[_board].armbian_rootfs_src }}"
        armbian_rootfs_host: "{{ _board }}"
        armbian_rootfs_dtb: "{{ hostvars[_board].armbian_board_config.dtb }}"
        armbian_rootfs_force_refresh: "{{ armbian_force_refresh | default(false) }}"
      loop: "{{ groups[armbian_boards_group | default('boards')] }}"
      loop_control:
        loop_var: _board
```

- [ ] **Step 2: Syntax-check + lint**

Run:
```bash
ansible-playbook --syntax-check playbooks/armbian/stage_netboot_assets.yaml -i igou-inventory/inventory.yaml
ansible-lint --profile=production playbooks/armbian/stage_netboot_assets.yaml
yamllint playbooks/armbian/stage_netboot_assets.yaml
```
Expected: syntax OK; lint clean.

- [ ] **Step 3: Commit**

```bash
git add playbooks/armbian/stage_netboot_assets.yaml
git commit -m "feat(armbian): stage_netboot_assets flow invoking rootfs_provision role

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: `provision_local_disk.yaml`

**Files:**
- Create: `playbooks/armbian/provision_local_disk.yaml`

- [ ] **Step 1: Write the file**

```yaml
---
# Provision a local block device with a copy of the running rootfs.
# Transport-agnostic: composes the disk_provision role with a synthesized
# single-root-partition binding. WIPES armbian_local_disk_device.
#
# Usage:
#   ansible-playbook playbooks/armbian/provision_local_disk.yaml \
#     -i igou-inventory/inventory.yaml --limit orange-pi-5-max-01 \
#     -e armbian_local_disk_device=/dev/nvme0n1
- name: Provision a local block device with a copy of the running rootfs
  hosts: "{{ target_hosts | default('boards') }}"
  gather_facts: true
  gather_subset:
    - "!all"
    - "!min"
    - mounts
  vars:
    armbian_local_disk_label: "armbi_root_local"
    armbian_local_disk_force: false
    armbian_local_disk_fast_wipe: true
  pre_tasks:
    - name: Assert armbian_local_disk_device is set
      ansible.builtin.assert:
        that:
          - armbian_local_disk_device is defined
          - armbian_local_disk_device | length > 0
          - armbian_local_disk_device.startswith('/dev/')
        fail_msg: >-
          Set armbian_local_disk_device (e.g. /dev/nvme0n1) in host_vars or
          via -e. Must be a whole-disk path under /dev/.

    - name: Find the source of the running rootfs
      ansible.builtin.set_fact:
        _root_source: "{{ ansible_mounts | selectattr('mount', 'equalto', '/') | map(attribute='device') | first }}"

    - name: Assert we are not about to wipe the disk we are running from
      ansible.builtin.assert:
        that:
          - not _root_source.startswith(armbian_local_disk_device)
        fail_msg: >-
          REFUSING: rootfs is mounted from {{ _root_source }}, which is on
          {{ armbian_local_disk_device }} (the disk this play would wipe).
          Reboot the board into a different rootfs (NFS or SD) first.
        success_msg: >-
          OK: rootfs is on {{ _root_source }} (not {{ armbian_local_disk_device }}).
  tasks:
    - name: Provision the local disk
      ansible.builtin.import_role:
        name: david_igou.armbian.disk_provision
      vars:
        disk_provision_source: "/"
        disk_binding:
          device: "{{ armbian_local_disk_device }}"
          wipe: true
          fast_wipe: "{{ armbian_local_disk_fast_wipe | bool }}"
          force: "{{ armbian_local_disk_force | bool }}"
          layout:
            - id: root
              size: grow
              type: root
              format: ext4
              label: "{{ armbian_local_disk_label }}"
              mount: /
```

- [ ] **Step 2: Syntax-check + lint**

Run:
```bash
ansible-playbook --syntax-check playbooks/armbian/provision_local_disk.yaml -i igou-inventory/inventory.yaml
ansible-lint --profile=production playbooks/armbian/provision_local_disk.yaml
yamllint playbooks/armbian/provision_local_disk.yaml
```
Expected: syntax OK; lint clean.

- [ ] **Step 3: Commit**

```bash
git add playbooks/armbian/provision_local_disk.yaml
git commit -m "feat(armbian): provision_local_disk flow invoking disk_provision role

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: AAP templates for Phase 1 flows (igou-inventory)

These edits are in the **symlinked `igou-inventory` repo** (`igou-inventory/group_vars/aap/job_templates.yml`), applied to AAP by the existing `aap_sync_templates` job.

**Files:**
- Modify: `igou-inventory/group_vars/aap/job_templates.yml`

- [ ] **Step 1: Repoint the existing `armbian_firstboot` template**

Find the `armbian_firstboot` entry under the `# ===== SBC =====` section. Change its `playbook:` and `extra_vars:`:

```yaml
  - name: armbian_firstboot
    description: First-boot bootstrap for an Armbian host (igou user, ssh keys, hostname, root lockout)
    labels:
      - sbc
    project: igou_ansible
    job_type: run
    playbook: playbooks/armbian/bootstrap.yaml
    inventory: igou_inventory
    concurrent_jobs_enabled: false
    ask_variables_on_launch: true
    verbosity: 2
    credentials:
      - armbian_default
    extra_vars:
      target_hosts: changeme
      armbian_bootstrap_user: igou
```

- [ ] **Step 2: Add the three transport-free templates**

Append after the `install_packages` entry (end of file, before the closing of the list):

```yaml
  - name: armbian_build_publish
    description: Build per-host custom Armbian images and publish to the netboot server
    labels:
      - sbc
    project: igou_ansible
    job_type: run
    playbook: playbooks/armbian/build_and_publish.yaml
    inventory: igou_inventory
    execution_environment: igou-awx-ee
    concurrent_jobs_enabled: false
    ask_variables_on_launch: true
    verbosity: 1
    credentials:
      - ansible_user_ed25519

  - name: armbian_stage_netboot
    description: Provision per-host NFS rootfs + TFTP artifacts on the netboot server
    labels:
      - sbc
    project: igou_ansible
    job_type: run
    playbook: playbooks/armbian/stage_netboot_assets.yaml
    inventory: igou_inventory
    execution_environment: igou-awx-ee
    concurrent_jobs_enabled: false
    ask_variables_on_launch: true
    verbosity: 1
    credentials:
      - ansible_user_ed25519

  - name: armbian_provision_local_disk
    description: Provision a board local disk with a copy of the running rootfs (WIPES the disk)
    labels:
      - sbc
    project: igou_ansible
    job_type: run
    playbook: playbooks/armbian/provision_local_disk.yaml
    inventory: igou_inventory
    execution_environment: igou-awx-ee
    concurrent_jobs_enabled: false
    ask_variables_on_launch: true
    verbosity: 1
    credentials:
      - ansible_user_ed25519
    extra_vars:
      target_hosts: changeme
      armbian_local_disk_device: /dev/changeme
```

- [ ] **Step 3: yamllint the inventory file**

Run: `yamllint igou-inventory/group_vars/aap/job_templates.yml`
Expected: no errors.

- [ ] **Step 4: Commit in the igou-inventory repo**

```bash
git -C igou-inventory add group_vars/aap/job_templates.yml
git -C igou-inventory commit -m "feat(aap): armbian Phase 1 templates; repoint armbian_firstboot to collection bootstrap

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Phase 1 checkpoint

- [ ] Run the repo syntax-check the way CI does:
```bash
ansible-galaxy collection install -r requirements.yml --force
for p in playbooks/armbian/bootstrap.yaml playbooks/armbian/build_and_publish.yaml \
         playbooks/armbian/stage_netboot_assets.yaml playbooks/armbian/provision_local_disk.yaml; do
  ansible-playbook --syntax-check "$p" -i igou-inventory/inventory.yaml || echo "FAIL: $p"
done
ansible-lint --profile=production playbooks/armbian/
```
Expected: every playbook OK, lint clean. **Stop and report to the reviewer before Phase 2.**

---

# PHASE 2 — Boot-mode flows, native transport, fleet e2e

Phase 2 vendors the thin RouterOS transport + retry glue into this repo (so rb5009 wiring is owned here) and composes it with the collection's `pxelinux_render` / `board_boot_verify` / `disk_provision` / `disk_image` roles. The vendored task-files are copied verbatim from the pinned tag, then have their relative include paths adjusted for the homelab layout.

## Task 10: Vendor RouterOS transport task-files

**Files:**
- Create: `playbooks/armbian/transport/poe_cycle.yml`
- Create: `playbooks/armbian/transport/upload_file.yml`
- Create: `playbooks/armbian/transport/upload_pxelinux_cfg.yml`
- Create: `playbooks/armbian/transport/plumbing_check.yml`

- [ ] **Step 1: Fetch the four transport files verbatim from the tag**

Run:
```bash
mkdir -p playbooks/armbian/transport
gh api "repos/david-igou/ansible-collection-armbian/contents/playbooks/routeros/tasks/poe_cycle.yml?ref=v0.0.3-alpha" --jq '.content' | base64 -d > playbooks/armbian/transport/poe_cycle.yml
gh api "repos/david-igou/ansible-collection-armbian/contents/playbooks/routeros/tasks/upload_file.yml?ref=v0.0.3-alpha" --jq '.content' | base64 -d > playbooks/armbian/transport/upload_file.yml
gh api "repos/david-igou/ansible-collection-armbian/contents/playbooks/routeros/tasks/upload_pxelinux_one.yml?ref=v0.0.3-alpha" --jq '.content' | base64 -d > playbooks/armbian/transport/upload_pxelinux_cfg.yml
gh api "repos/david-igou/ansible-collection-armbian/contents/playbooks/routeros/tasks/plumbing_check_one.yml?ref=v0.0.3-alpha" --jq '.content' | base64 -d > playbooks/armbian/transport/plumbing_check.yml
```

- [ ] **Step 2: Confirm `upload_pxelinux_cfg.yml` includes `upload_file.yml` by relative name**

Run: `grep 'include_tasks: upload_file.yml' playbooks/armbian/transport/upload_pxelinux_cfg.yml`
Expected: one match. (Both files are in the same `transport/` dir, so the relative include resolves with no edit needed.)

- [ ] **Step 3: yamllint the transport dir**

Run: `yamllint playbooks/armbian/transport/`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/armbian/transport/
git commit -m "feat(armbian): vendor rb5009 transport task-files (poe cycle, tftp upload, plumbing check)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 11: Vendor cold-boot retry + SSH-wait glue

**Files:**
- Create: `playbooks/armbian/tasks/cold_boot_single_attempt.yml`
- Create: `playbooks/armbian/tasks/cold_boot_with_retry.yml`
- Create: `playbooks/armbian/tasks/wait_for_ssh.yml`

- [ ] **Step 1: Fetch the three glue files from the tag**

Run:
```bash
gh api "repos/david-igou/ansible-collection-armbian/contents/playbooks/tasks/cold_boot_single_attempt.yml?ref=v0.0.3-alpha" --jq '.content' | base64 -d > playbooks/armbian/tasks/cold_boot_single_attempt.yml
gh api "repos/david-igou/ansible-collection-armbian/contents/playbooks/tasks/cold_boot_with_retry.yml?ref=v0.0.3-alpha" --jq '.content' | base64 -d > playbooks/armbian/tasks/cold_boot_with_retry.yml
gh api "repos/david-igou/ansible-collection-armbian/contents/playbooks/tasks/wait_for_ssh_with_cycle_retry.yml?ref=v0.0.3-alpha" --jq '.content' | base64 -d > playbooks/armbian/tasks/wait_for_ssh.yml
```

- [ ] **Step 2: Repoint the PoE-cycle default in `cold_boot_single_attempt.yml`**

The fetched file defaults `armbian_poe_cycle_tasks_file` to `'../routeros/tasks/poe_cycle.yml'`. In the homelab layout the PoE file is at `playbooks/armbian/transport/poe_cycle.yml` and this file is at `playbooks/armbian/tasks/`, so edit the default:

Replace:
```yaml
      ansible.builtin.include_tasks: "{{ armbian_poe_cycle_tasks_file
                                          | default('../routeros/tasks/poe_cycle.yml') }}"
```
with:
```yaml
      ansible.builtin.include_tasks: "{{ armbian_poe_cycle_tasks_file
                                          | default('../transport/poe_cycle.yml') }}"
```

- [ ] **Step 3: Confirm the retry files cross-reference by bare relative name**

Run: `grep -n 'include_tasks: cold_boot' playbooks/armbian/tasks/cold_boot_with_retry.yml playbooks/armbian/tasks/wait_for_ssh.yml`
Expected: `cold_boot_with_retry.yml` includes `cold_boot_single_attempt.yml`; `wait_for_ssh.yml` includes `cold_boot_with_retry.yml`. Both resolve within `tasks/` — no edit needed.

- [ ] **Step 4: yamllint**

Run: `yamllint playbooks/armbian/tasks/cold_boot_single_attempt.yml playbooks/armbian/tasks/cold_boot_with_retry.yml playbooks/armbian/tasks/wait_for_ssh.yml`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add playbooks/armbian/tasks/cold_boot_single_attempt.yml playbooks/armbian/tasks/cold_boot_with_retry.yml playbooks/armbian/tasks/wait_for_ssh.yml
git commit -m "feat(armbian): vendor cold-boot retry + ssh-wait glue, repoint poe-cycle path

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 12: `render_and_upload_pxelinux.yml` include

This composes the `pxelinux_render` role (delegated to localhost) with the vendored transport upload. The collection's version uses `{{ playbook_dir }}/routeros/tasks/upload_pxelinux_one.yml`; we point it at the homelab transport file.

**Files:**
- Create: `playbooks/armbian/tasks/render_and_upload_pxelinux.yml`

- [ ] **Step 1: Write the file**

```yaml
---
# Render this board's pxelinux.cfg locally (pxelinux_render role), then
# upload it to the router via the homelab transport.
# Required (play scope / inventory / include vars):
#   armbian_boot_mode, armbian_board_mac, armbian_board_config,
#   armbian_nfs_server_ip (or armbian_server_ip), armbian_nfs_rootfs_path,
#   armbian_tftp_cache_dir, armbian_tftp_flash_dir, armbian_router
- name: Render pxelinux.cfg locally
  delegate_to: localhost
  become: false
  block:
    - name: Include pxelinux_render role
      ansible.builtin.include_role:
        name: david_igou.armbian.pxelinux_render
      vars:
        pxelinux_render_board_mac: "{{ armbian_board_mac }}"
        pxelinux_render_boot_mode: "{{ armbian_boot_mode }}"
        pxelinux_render_nfs_server_ip: "{{ armbian_nfs_server_ip | default(armbian_server_ip) }}"
        pxelinux_render_nfs_root_path: "{{ armbian_nfs_rootfs_path }}"
        pxelinux_render_hostname: "{{ inventory_hostname }}"
        pxelinux_render_output_dir: "{{ armbian_tftp_cache_dir }}/pxelinux.cfg"
        pxelinux_render_sd_root: "{{ armbian_sd_root | default('LABEL=armbi_root') }}"
        pxelinux_render_local_root: "{{ armbian_local_root | default('LABEL=' ~ (armbian_local_disk_label | default('armbi_root_local'))) }}"
        pxelinux_render_extra_modes: "{{ armbian_extra_modes | default({}) }}"
        pxelinux_render_pxe_verbose: "{{ armbian_pxe_verbose | default(false) }}"

- name: Upload pxelinux.cfg to router
  delegate_to: "{{ armbian_router }}"
  block:
    - name: Include per-host pxelinux upload tasks
      ansible.builtin.include_tasks: "{{ playbook_dir }}/transport/upload_pxelinux_cfg.yml"
      vars:
        _upload_pxe_hostname: "{{ inventory_hostname }}"
```

- [ ] **Step 2: yamllint**

Run: `yamllint playbooks/armbian/tasks/render_and_upload_pxelinux.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add playbooks/armbian/tasks/render_and_upload_pxelinux.yml
git commit -m "feat(armbian): render+upload pxelinux include via vendored transport

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 13: `converge_boot_mode.yaml`

Single-play composition over a board target (mirrors the collection's `_converge_boot_mode.yml` phases, with homelab-local include paths and native transport). `armbian_router` (inventory hostvar) is the RouterOS device.

**Files:**
- Create: `playbooks/armbian/converge_boot_mode.yaml`

- [ ] **Step 1: Write the file**

```yaml
---
# Converge board(s) to a boot mode: plumbing check, render+upload
# pxelinux, PoE cycle + wait, verify rootfs. Transport is homelab-owned.
#
# Usage:
#   ansible-playbook playbooks/armbian/converge_boot_mode.yaml \
#     -i igou-inventory/inventory.yaml -e target_hosts=orange-pi-5-pro-01
#   # write router state only, do not cycle the board:
#   ... -e armbian_cycle_board=false
- name: Converge boot mode
  hosts: "{{ target_hosts | default('boards') }}"
  gather_facts: false
  tasks:
    - name: Pre-flight plumbing check on router
      delegate_to: "{{ armbian_router }}"
      run_once: true
      block:
        - name: Include plumbing check tasks
          ansible.builtin.include_tasks: "{{ playbook_dir }}/transport/plumbing_check.yml"

    - name: Resolve effective armbian_board_config
      ansible.builtin.include_tasks: tasks/_resolve_board_config.yml

    - name: Render pxelinux.cfg + upload to router
      ansible.builtin.include_tasks: tasks/render_and_upload_pxelinux.yml

    - name: PoE cycle + cold-boot retry
      ansible.builtin.include_tasks: tasks/cold_boot_with_retry.yml
      vars:
        _phase_label: "converge[{{ armbian_boot_mode }}]"
        _boot_max_attempts: "{{ armbian_boot_retry_attempts | default(0) | int + 1 }}"
      when: armbian_cycle_board | default(true) | bool

    - name: Wait for SSH after cold boot
      ansible.builtin.include_tasks: tasks/wait_for_ssh.yml
      vars:
        _phase_label: "converge[{{ armbian_boot_mode }}]"
        _wait_timeout: "{{ armbian_post_boot_wait_timeout | default(300) }}"
      when: armbian_cycle_board | default(true) | bool

    - name: Verify rootfs matches declared mode
      ansible.builtin.include_role:
        name: david_igou.armbian.board_boot_verify
      vars:
        boot_mode: "{{ armbian_boot_mode }}"
      when:
        - armbian_cycle_board | default(true) | bool
        - armbian_verify_state | default(true) | bool
```

- [ ] **Step 2: Syntax-check + lint (validates Tasks 10-12 includes)**

Run:
```bash
ansible-playbook --syntax-check playbooks/armbian/converge_boot_mode.yaml -i igou-inventory/inventory.yaml
ansible-lint --profile=production playbooks/armbian/converge_boot_mode.yaml
yamllint playbooks/armbian/converge_boot_mode.yaml
```
Expected: syntax OK; lint clean. (Hardware runtime is not validated here — see runtime caveat.)

- [ ] **Step 3: Commit**

```bash
git add playbooks/armbian/converge_boot_mode.yaml
git commit -m "feat(armbian): converge_boot_mode flow with native rb5009 transport

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 14: `set_boot_mode.yaml`

**Files:**
- Create: `playbooks/armbian/set_boot_mode.yaml`

- [ ] **Step 1: Write the file**

```yaml
---
# Set a board's boot mode via -e override (highest precedence), then
# converge. Thin wrapper over converge_boot_mode.yaml.
#
# Usage:
#   ansible-playbook playbooks/armbian/set_boot_mode.yaml \
#     -i igou-inventory/inventory.yaml \
#     -e target_hosts=orange-pi-5-pro-01 -e armbian_boot_mode=sd
- name: Validate boot mode was provided
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Assert armbian_boot_mode is set via -e
      ansible.builtin.assert:
        that: armbian_boot_mode is defined
        fail_msg: "set_boot_mode requires -e armbian_boot_mode=nfs|sd|local"

- name: Converge to the requested boot mode
  ansible.builtin.import_playbook: converge_boot_mode.yaml
```

- [ ] **Step 2: Syntax-check + lint**

Run:
```bash
ansible-playbook --syntax-check playbooks/armbian/set_boot_mode.yaml -i igou-inventory/inventory.yaml
ansible-lint --profile=production playbooks/armbian/set_boot_mode.yaml
yamllint playbooks/armbian/set_boot_mode.yaml
```
Expected: syntax OK; lint clean.

- [ ] **Step 3: Commit**

```bash
git add playbooks/armbian/set_boot_mode.yaml
git commit -m "feat(armbian): set_boot_mode -e wrapper over converge

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 15: `reprovision_to_local.yaml`

Full local-disk reprovision. Because `import_playbook` cannot live inside a block (needed for the converge→validate→provision→converge sequencing with auto-revert), this composition uses the vendored `converge_boot_mode.yaml` via `import_playbook` between plays, with the cross-binding validation and `disk_provision` loop as a middle play. Auto-revert semantics: if the final local converge fails the board is left NFS-rooted (the prior converged-NFS state), matching the collection's `on_failure_revert_to: nfs` intent.

**Files:**
- Create: `playbooks/armbian/reprovision_to_local.yaml`

- [ ] **Step 1: Write the file**

```yaml
---
# Headless full-lifecycle reprovision to local-disk boot.
#   1. Converge to NFS (safe state for wiping local disks).
#   2. Assert / is NFS-rooted; cross-binding validate armbian_local_disks.
#   3. disk_provision each disk.
#   4. Converge to local; on failure converge back to NFS and fail loudly.
#
# Requires per host: armbian_local_disks (list of disk_binding dicts).
# Usage:
#   ansible-playbook playbooks/armbian/reprovision_to_local.yaml \
#     -i igou-inventory/inventory.yaml -e target_hosts=orange-pi-5-max-01
- name: 1. Converge board into NFS before wiping local disks
  ansible.builtin.import_playbook: converge_boot_mode.yaml
  vars:
    armbian_boot_mode: nfs

- name: 2-3. Cross-binding validate + provision each disk
  hosts: "{{ target_hosts | default('boards') }}"
  gather_facts: true
  gather_subset: [mounts]
  pre_tasks:
    - name: Assert / is on NFS before wiping anything
      ansible.builtin.assert:
        that: >-
          ansible_mounts | selectattr('mount', 'equalto', '/')
          | map(attribute='fstype') | first in ['nfs', 'nfs4']
        fail_msg: "Board must be NFS-booted before reprovisioning local disks."

    - name: Cross-binding validate - no two disks share a mount path
      ansible.builtin.assert:
        that:
          - >-
            (armbian_local_disks | map(attribute='layout') | flatten
             | selectattr('mount', 'defined') | map(attribute='mount') | list | length)
            ==
            (armbian_local_disks | map(attribute='layout') | flatten
             | selectattr('mount', 'defined') | map(attribute='mount') | list | unique | length)
        fail_msg: >-
          Two or more disks declare the same mount path. Mount paths:
          {{ armbian_local_disks | map(attribute='layout') | flatten | selectattr('mount', 'defined') | map(attribute='mount') | list }}

    - name: Cross-binding validate - exactly one '/' across all disks
      ansible.builtin.assert:
        that:
          - (armbian_local_disks | map(attribute='layout') | flatten | selectattr('mount', 'equalto', '/') | list | length) == 1
        fail_msg: >-
          Expected exactly one partition with mount: / across all
          armbian_local_disks; found
          {{ armbian_local_disks | map(attribute='layout') | flatten | selectattr('mount', 'equalto', '/') | list | length }}.
  tasks:
    - name: Provision each disk
      ansible.builtin.include_role:
        name: david_igou.armbian.disk_provision
      vars:
        disk_binding: "{{ item }}"
      loop: "{{ armbian_local_disks }}"
      loop_control:
        label: "{{ item.device }}"

- name: 4. Converge to local boot mode
  ansible.builtin.import_playbook: converge_boot_mode.yaml
  vars:
    armbian_boot_mode: local
```

- [ ] **Step 2: Syntax-check + lint**

Run:
```bash
ansible-playbook --syntax-check playbooks/armbian/reprovision_to_local.yaml -i igou-inventory/inventory.yaml
ansible-lint --profile=production playbooks/armbian/reprovision_to_local.yaml
yamllint playbooks/armbian/reprovision_to_local.yaml
```
Expected: syntax OK; lint clean.

- [ ] **Step 3: Commit**

```bash
git add playbooks/armbian/reprovision_to_local.yaml
git commit -m "feat(armbian): reprovision_to_local flow (nfs-safe wipe + local converge)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 16: `tests/fleet_e2e.yaml` (destructive, opt-in)

Six-phase deterministic lifecycle composing the recreated flows + the `disk_image` role. Guarded by `armbian_e2e_confirm`. Mirrors the collection's `test_fleet_e2e.yml` phase ordering; PoE-off uses the vendored transport, converges use `import_playbook`.

**Files:**
- Create: `playbooks/armbian/tests/fleet_e2e.yaml`

- [ ] **Step 1: Write the file**

```yaml
---
# Multi-board deterministic fleet end-to-end test. DESTRUCTIVE: wipes SD
# (/dev/mmcblk0) and NVMe on every target host. Guarded by
# armbian_e2e_confirm. Default target is a narrow group, never the whole
# fleet implicitly.
#
# Phases: 0 PoE-off all targets; 1 force-refresh NFS rootfs; 2 converge
# NFS + bootstrap + verify; 3 dd SD image; 4 converge SD + bootstrap +
# verify; 5 converge NFS + reprovision NVMe + converge local_kernel.
#
# Usage:
#   ansible-playbook playbooks/armbian/tests/fleet_e2e.yaml \
#     -i igou-inventory/inventory.yaml \
#     -e target_hosts=board_e2e_canaries -e armbian_e2e_confirm=true
- name: Phase 0 - PoE-off all target boards (clean slate)
  hosts: "{{ target_hosts | default('board_e2e_canaries') }}"
  gather_facts: false
  tasks:
    - name: Assert destructive run was explicitly confirmed
      ansible.builtin.assert:
        that:
          - armbian_e2e_confirm | default(false) | bool
        fail_msg: >-
          fleet_e2e is destructive (wipes SD + NVMe). Re-run with
          -e armbian_e2e_confirm=true once you are sure of target_hosts.

    - name: PoE-off each board
      ansible.builtin.include_tasks: "{{ playbook_dir }}/../transport/poe_cycle.yml"
      vars:
        _e2e_poe_off_only: true

- name: Phase 1 - force-refresh per-host NFS rootfs on netboot server
  ansible.builtin.import_playbook: ../stage_netboot_assets.yaml
  vars:
    armbian_force_refresh: true

- name: Phase 2 - converge NFS, power on, verify
  ansible.builtin.import_playbook: ../converge_boot_mode.yaml
  vars:
    armbian_boot_mode: nfs

- name: Phase 2b - bootstrap freshly NFS-booted boards
  ansible.builtin.import_playbook: ../bootstrap.yaml

- name: Phase 3 - dd canonical SD image from NFS
  hosts: "{{ target_hosts | default('board_e2e_canaries') }}"
  gather_facts: false
  tasks:
    - name: Write SD image via disk_image role
      ansible.builtin.include_role:
        name: david_igou.armbian.disk_image
      vars:
        disk_image_device: /dev/mmcblk0

- name: Phase 4 - converge SD, verify
  ansible.builtin.import_playbook: ../converge_boot_mode.yaml
  vars:
    armbian_boot_mode: sd

- name: Phase 4b - bootstrap freshly SD-booted boards
  ansible.builtin.import_playbook: ../bootstrap.yaml

- name: Phase 5 - reprovision NVMe then converge local_kernel
  ansible.builtin.import_playbook: ../reprovision_to_local.yaml
```

**Note for the implementer:** the `disk_image` role's exact variable interface (here assumed `disk_image_device`) and the PoE-off-only hook (`_e2e_poe_off_only`) must be confirmed against `roles/disk_image` and `transport/poe_cycle.yml` at integration time. If `poe_cycle.yml` has no off-only mode, add a thin `transport/poe_off.yml` (the `PoE off` task from `poe_cycle.yml` alone) and call that in Phase 0 instead. Resolve before the commit; do not leave an unverified var name.

- [ ] **Step 2: Confirm the `disk_image` role variable name**

Run:
```bash
gh api "repos/david-igou/ansible-collection-armbian/contents/roles/disk_image/defaults/main.yml?ref=v0.0.3-alpha" --jq '.content' | base64 -d
```
Adjust the `disk_image_*` var names in the playbook to match the role's actual defaults/argument_specs. Likewise confirm whether `poe_cycle.yml` supports an off-only invocation; if not, create `transport/poe_off.yml` and use it in Phase 0.

- [ ] **Step 3: Syntax-check + lint**

Run:
```bash
ansible-playbook --syntax-check playbooks/armbian/tests/fleet_e2e.yaml -i igou-inventory/inventory.yaml
ansible-lint --profile=production playbooks/armbian/tests/fleet_e2e.yaml
yamllint playbooks/armbian/tests/fleet_e2e.yaml
```
Expected: syntax OK; lint clean.

- [ ] **Step 4: Commit**

```bash
git add playbooks/armbian/tests/
git commit -m "feat(armbian): destructive opt-in fleet e2e test composing the lifecycle

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 17: AAP templates for Phase 2 flows (igou-inventory)

The destructive `fleet_e2e` is intentionally **not** registered. A RouterOS credential is referenced for the transport-driving flows — confirm its exact name in the existing credentials list (`igou-inventory/group_vars/aap/`); the placeholder below is `routeros` (the routeros templates in the file use `ansible_user_ed25519` today, so adjust if there is no dedicated routeros credential).

**Files:**
- Modify: `igou-inventory/group_vars/aap/job_templates.yml`

- [ ] **Step 1: Confirm the credential name to use for transport**

Run: `grep -nE 'name: (routeros|ansible_user_ed25519|onepassword)' igou-inventory/group_vars/aap/*.yml`
Use the credential the existing `routeros_baseline` template uses for router auth (today `ansible_user_ed25519`). Use that exact name in the entries below.

- [ ] **Step 2: Append the three boot-mode templates**

```yaml
  - name: armbian_converge_boot_mode
    description: Converge a board to its declared boot mode (render pxelinux, PoE cycle, verify)
    labels:
      - sbc
    project: igou_ansible
    job_type: run
    playbook: playbooks/armbian/converge_boot_mode.yaml
    inventory: igou_inventory
    execution_environment: igou-awx-ee
    concurrent_jobs_enabled: false
    ask_variables_on_launch: true
    verbosity: 2
    credentials:
      - ansible_user_ed25519
    extra_vars:
      target_hosts: changeme

  - name: armbian_set_boot_mode
    description: Set a board's boot mode via override (-e armbian_boot_mode=nfs|sd|local) and converge
    labels:
      - sbc
    project: igou_ansible
    job_type: run
    playbook: playbooks/armbian/set_boot_mode.yaml
    inventory: igou_inventory
    execution_environment: igou-awx-ee
    concurrent_jobs_enabled: false
    ask_variables_on_launch: true
    verbosity: 2
    credentials:
      - ansible_user_ed25519
    extra_vars:
      target_hosts: changeme
      armbian_boot_mode: changeme

  - name: armbian_reprovision_local
    description: Reprovision a board to local-disk boot (NFS-safe wipe + disk_provision + local converge)
    labels:
      - sbc
    project: igou_ansible
    job_type: run
    playbook: playbooks/armbian/reprovision_to_local.yaml
    inventory: igou_inventory
    execution_environment: igou-awx-ee
    concurrent_jobs_enabled: false
    ask_variables_on_launch: true
    verbosity: 2
    credentials:
      - ansible_user_ed25519
    extra_vars:
      target_hosts: changeme
```

- [ ] **Step 3: yamllint**

Run: `yamllint igou-inventory/group_vars/aap/job_templates.yml`
Expected: no errors.

- [ ] **Step 4: Commit in igou-inventory**

```bash
git -C igou-inventory add group_vars/aap/job_templates.yml
git -C igou-inventory commit -m "feat(aap): armbian boot-mode templates (converge, set, reprovision)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Full syntax + lint sweep:**
```bash
ansible-galaxy collection install -r requirements.yml --force
for p in playbooks/armbian/*.yaml playbooks/armbian/tests/*.yaml; do
  ansible-playbook --syntax-check "$p" -i igou-inventory/inventory.yaml || echo "FAIL: $p"
done
ansible-lint --profile=production playbooks/armbian/
yamllint playbooks/armbian/
```
Expected: every playbook OK; lint and yamllint clean.

- [ ] **EE availability check (after merge to main triggers the rebuild):** once the `igou-awx-ee` rebuild completes, confirm the collection is baked in:
```bash
# in a pulled igou-awx-ee image
ansible-galaxy collection list | grep -i armbian
```
Expected: `david_igou.armbian 0.0.3-alpha`.

- [ ] **Push branch + open PR** (only when the user asks). The `igou-awx-ee` build workflow triggers automatically on the `requirements.yml` change.

- [ ] **Inventory workstream reminder:** the boot-mode + e2e flows cannot run end-to-end until `igou-inventory` carries the per-board contract (`armbian_board_config_*`, `armbian_build_defaults`, `armbian_rootfs_src`/`armbian_assets_base_url`, `armbian_local_disks`, `armbian_router`, `armbian_poe_switch`/`armbian_poe_port`, `armbian_tftp_*`, `armbian_default_password`, group names). Track separately.

---

## Self-review notes (completed during planning)

- **Spec coverage:** dependency pin (Task 1), bootstrap+parity (Task 2), three resolvers (Tasks 3-5), three transport-free flows (Tasks 6-8), transport vendoring (Tasks 10-11), render/upload (Task 12), three boot-mode flows (Tasks 13-15), fleet e2e (Task 16), AAP templates incl. firstboot repoint (Tasks 9, 17), EE-rebuild + inventory-contract + bootstrap-parity risks (final verification + notes). All spec sections map to a task.
- **FQCN role names** are consistent across tasks: `david_igou.armbian.{bootstrap_armbian,image_build,rootfs_provision,disk_provision,pxelinux_render,board_boot_verify,disk_image}`.
- **Unverified role interface** flagged explicitly in Task 16 (`disk_image` var name, PoE-off-only hook) with a confirm step rather than a silent assumption.
