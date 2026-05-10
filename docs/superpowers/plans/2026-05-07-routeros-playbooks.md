# RouterOS Playbooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build five flat playbooks under `playbooks/routeros/` (backup, manage_users, baseline, upgrade_download, upgrade_apply) plus two shared task files, to manage the homelab MikroTik fleet.

**Architecture:** Flat playbooks (no roles, no collections), all operating over `community.routeros.command` via `ansible.netcommon.network_cli`. Per-playbook idempotency where the operation is naturally idempotent (users, baseline, upgrade_download); explicit non-idempotency for backup (timestamped artifacts). Backup artifacts land on the control node under `./backups/routeros/<host>/` (gitignored, mode 0700, files mode 0600).

**Tech Stack:** Ansible (community.routeros 3.20.0, ansible.netcommon 8.5.0), ansible-lint (production profile), yamllint, pre-commit. Inventory lives in the symlinked `igou-inventory` separate repo.

**Spec:** `docs/superpowers/specs/2026-05-07-routeros-playbooks-design.md`

---

## File Structure

**Created in `igou-ansible`:**
- `playbooks/routeros/tasks/fetch_artifact.yml` — shared task: pull a file from device flash to control node, chmod 0600, remove from device
- `playbooks/routeros/tasks/wait_for_routeros.yml` — shared task: wait for SSH to drop and return after a reboot, then smoke-test
- `playbooks/routeros/backup.yml` — produces `<host>-<ts>.backup` and `<host>-<ts>.rsc` per device
- `playbooks/routeros/manage_users.yml` — keep `igou` user's SSH keys in sync
- `playbooks/routeros/baseline.yml` — NTP/timezone/disabled services
- `playbooks/routeros/upgrade_download.yml` — Phase A: stage RouterOS package update
- `playbooks/routeros/upgrade_apply.yml` — Phase B: backup → reboot → verify → firmware → reboot → verify

**Modified in `igou-ansible`:**
- `.gitignore` — append `/backups/`

**Modified in `igou-inventory` (separate repo, symlinked at `/workspace/igou-inventory`):**
- `group_vars/routeros.yml` — change `ansible_user`, prune obsolete bootstrap comment, append `routeros_*` variables

---

## Testing Approach

This repo lints with `pre-commit` (yamllint + ansible-lint --profile=production). The spec explicitly opts out of molecule, since RouterOS doesn't run in a container and the fleet is 4 devices. The "test" gate at each commit is therefore:

1. `pre-commit run --all-files` (yamllint + ansible-lint, both must be clean)
2. `ansible-navigator run <playbook> --syntax-check` (or `ansible-playbook --syntax-check`)
3. *Optional, operator-driven:* run against one device with `-e host=<inventory_hostname>` and confirm expected output.

Each task below ends with steps 1 and 2 as required gates and step 3 as a documented but non-blocking manual check.

---

## Task 1: Inventory + .gitignore prep

**Files:**
- Modify: `/workspace/igou-ansible/.gitignore` (append `/backups/`)
- Modify: `/workspace/igou-inventory/group_vars/routeros.yml` (separate repo)

**Why first:** every playbook depends on the new variables in `group_vars/routeros.yml`, and the connection user must be `igou+cet1024w` before any playbook can connect.

- [ ] **Step 1: Append `/backups/` to `.gitignore`**

Edit `/workspace/igou-ansible/.gitignore` and add a single line at the end:

```
/backups/
```

- [ ] **Step 2: Update `igou-inventory/group_vars/routeros.yml`**

Open `/workspace/igou-inventory/group_vars/routeros.yml`. Replace the entire file with:

```yaml
---
# MikroTik RouterOS devices.
#
# These hosts listen for SSH on a non-default port. The local user's
# ~/.ssh/config sets that port for these hostnames; we mirror it here
# (in the live inventory) so the connection works from inside an
# execution environment too, where the user's ssh config is not
# available.
#
# The `+cet1024w` terminal hint prevents network_cli timeouts on long
# output lines.
ansible_connection: ansible.netcommon.network_cli
ansible_network_os: community.routeros.routeros
ansible_port: <inventory-managed; non-default SSH port>
ansible_user: igou+cet1024w

# --- Variables consumed by playbooks/routeros/*.yml ---

# Where backup.yml stores artifacts on the control node.
# Resolved relative to the playbook directory; lands at <repo-root>/backups/routeros/.
routeros_backup_dir: "{{ playbook_dir }}/../../backups/routeros"

# Number of newest backup artifacts to retain per host (per file type).
routeros_backup_retain: 30

# Timezone applied by baseline.yml (RouterOS time-zone-name format).
routeros_timezone: "America/New_York"

# NTP servers configured by baseline.yml.
routeros_ntp_servers:
  - 0.pool.ntp.org
  - 1.pool.ntp.org

# Services baseline.yml will disable. ssh and winbox are deliberately excluded.
routeros_disabled_services:
  - telnet
  - ftp
  - www
  - www-ssl
  - api
  - api-ssl

# Channel that upgrade_download.yml stages (and upgrade_apply.yml applies).
routeros_upgrade_channel: stable

# Users whose SSH keys are managed by manage_users.yml. Each public key
# MUST include a comment field (third whitespace field, e.g. user@host) —
# manage_users.yml uses that string to detect already-imported keys.
routeros_users:
  - name: igou
    group: full
    ssh_keys:
      - "ssh-ed25519 AAAAREPLACE_WITH_REAL_PUBLIC_KEY igou@control-node"

# When true, manage_users.yml will remove SSH keys present on the device
# for managed users that aren't in routeros_users[].ssh_keys. Off by
# default; flip to true once the playbook has been validated.
routeros_prune_ssh_keys: false
```

> **Note for implementer:** the `ssh-ed25519 AAAAREPLACE_WITH_REAL_PUBLIC_KEY igou@control-node` line is a placeholder. Before running `manage_users.yml`, replace with the real public key(s) the operator wants on every RouterOS device. The operator can paste from `~/.ssh/id_ed25519.pub` on their control node, or from `op` (1Password). Multiple keys are supported as additional list entries.

- [ ] **Step 3: yamllint the modified files**

Run from `/workspace/igou-ansible`:

```bash
yamllint /workspace/igou-inventory/group_vars/routeros.yml
```

Expected: clean (exit 0, no output).

- [ ] **Step 4: Commit in `igou-ansible`**

```bash
cd /workspace/igou-ansible
git add .gitignore
git commit -m "$(cat <<'EOF'
Gitignore /backups/ for RouterOS backup artifacts

backup.yml writes timestamped binary backups and plaintext exports
(with sensitive values) under <repo-root>/backups/routeros/<host>/.
Both contain secrets and must never be committed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Commit in `igou-inventory`**

```bash
cd /workspace/igou-inventory
git add group_vars/routeros.yml
git commit -m "$(cat <<'EOF'
Wire RouterOS playbook variables and switch ansible_user to igou

Drops the aspirational ansible-netboot+cet1024w connection user (the
bootstrap playbook that would have created that account was never
written). The igou user already exists on every device, so connections
work immediately.

Adds the routeros_* variable block consumed by the new playbooks under
playbooks/routeros/ in igou-ansible.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

> **Note:** the `ssh-ed25519 AAAAREPLACE_WITH_REAL_PUBLIC_KEY` placeholder will be visible in the git history of `igou-inventory`. That's fine — public keys aren't secret, and the operator will replace it with their real key in a follow-up commit.

---

## Task 2: Shared task — `tasks/fetch_artifact.yml`

**Files:**
- Create: `/workspace/igou-ansible/playbooks/routeros/tasks/fetch_artifact.yml`

This is the building block `backup.yml` uses. Build it first so backup.yml can include it.

- [ ] **Step 1: Create the tasks directory**

```bash
mkdir -p /workspace/igou-ansible/playbooks/routeros/tasks
```

- [ ] **Step 2: Write `tasks/fetch_artifact.yml`**

```yaml
---
# Fetch a file from RouterOS device flash to the control node, then
# remove it from the device.
#
# Required vars:
#   remote_filename: filename on device flash (no leading slash)
#   local_dir:       absolute path on the control node (must already
#                    exist with mode 0700)
#
# Caller is responsible for ensuring local_dir exists. The fetched file
# is chmod 0600 because backup artifacts contain secrets.
- name: "Fetch {{ remote_filename }} from {{ inventory_hostname }}"
  ansible.netcommon.net_get:
    src: "{{ remote_filename }}"
    dest: "{{ local_dir }}/{{ remote_filename }}"

- name: "Set mode 0600 on local copy of {{ remote_filename }}"
  ansible.builtin.file:
    path: "{{ local_dir }}/{{ remote_filename }}"
    mode: '0600'
  delegate_to: localhost

- name: "Remove {{ remote_filename }} from {{ inventory_hostname }} flash"
  community.routeros.command:
    commands:
      - "/file remove {{ remote_filename }}"
  failed_when: false
  changed_when: false
```

> **Note on `ansible.netcommon.net_get`:** the `community.routeros` collection does not ship a dedicated file-transfer module. `net_get` is the generic fallback and is expected to work over the existing `network_cli` session via SCP. If the implementer finds it doesn't (e.g. RouterOS doesn't accept the SCP subsystem), the fallback is to invoke `scp` directly via `ansible.builtin.command` with `delegate_to: localhost`, using the same port/user as the network_cli connection. Verify this works on a single host before completing this task.

- [ ] **Step 3: yamllint the file**

```bash
cd /workspace/igou-ansible
yamllint playbooks/routeros/tasks/fetch_artifact.yml
```

Expected: clean.

- [ ] **Step 4: ansible-lint clean**

`tasks/fetch_artifact.yml` is included by playbooks; ansible-lint runs against the whole tree. Defer the lint check to Task 3 (after `backup.yml` includes this file), since linting an include file in isolation can produce false positives (no top-level play context).

- [ ] **Step 5: Commit**

```bash
cd /workspace/igou-ansible
git add playbooks/routeros/tasks/fetch_artifact.yml
git commit -m "$(cat <<'EOF'
Add fetch_artifact shared task for RouterOS playbooks

Wraps net_get + chmod 0600 + /file remove so backup.yml can pull binary
backups and config exports off device flash without duplicating logic.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `backup.yml`

**Files:**
- Create: `/workspace/igou-ansible/playbooks/routeros/backup.yml`

- [ ] **Step 1: Write `backup.yml`**

```yaml
---
# Pull a fresh binary backup + plaintext config (with sensitive values)
# off every RouterOS device. Files land in
# {{ routeros_backup_dir }}/<inventory_hostname>/ at mode 0600
# (directory mode 0700). Both files contain secrets — do not commit
# them. /backups/ is in .gitignore.
#
# Default scope: the routeros group (1 router + 3 switches).
# Override target with: -e host=<inventory_hostname>
- name: Back up RouterOS devices
  hosts: "{{ host | default('routeros') }}"
  gather_facts: false

  tasks:
    - name: Compute per-host backup timestamp
      ansible.builtin.set_fact:
        backup_ts: "{{ lookup('pipe', 'date -u +%Y%m%dT%H%M%SZ') }}"

    - name: "Ensure local backup directory for {{ inventory_hostname }} exists"
      ansible.builtin.file:
        path: "{{ routeros_backup_dir }}/{{ inventory_hostname }}"
        state: directory
        mode: '0700'
      delegate_to: localhost

    - name: "Save binary backup as {{ inventory_hostname }}-{{ backup_ts }}.backup"
      community.routeros.command:
        commands:
          - "/system backup save name={{ inventory_hostname }}-{{ backup_ts }} dont-encrypt=yes"

    - name: "Export config (with sensitive values) as {{ inventory_hostname }}-{{ backup_ts }}.rsc"
      community.routeros.command:
        commands:
          - "/export show-sensitive file={{ inventory_hostname }}-{{ backup_ts }}"

    - name: Fetch binary backup
      ansible.builtin.include_tasks: tasks/fetch_artifact.yml
      vars:
        remote_filename: "{{ inventory_hostname }}-{{ backup_ts }}.backup"
        local_dir: "{{ routeros_backup_dir }}/{{ inventory_hostname }}"

    - name: Fetch plaintext export
      ansible.builtin.include_tasks: tasks/fetch_artifact.yml
      vars:
        remote_filename: "{{ inventory_hostname }}-{{ backup_ts }}.rsc"
        local_dir: "{{ routeros_backup_dir }}/{{ inventory_hostname }}"

    - name: List local .backup files for pruning
      ansible.builtin.find:
        paths: "{{ routeros_backup_dir }}/{{ inventory_hostname }}"
        patterns: "{{ inventory_hostname }}-*.backup"
        file_type: file
      register: existing_binary_backups
      delegate_to: localhost
      changed_when: false

    - name: Prune .backup files beyond retention
      ansible.builtin.file:
        path: "{{ item.path }}"
        state: absent
      delegate_to: localhost
      loop: "{{ (existing_binary_backups.files | sort(attribute='mtime', reverse=true))[routeros_backup_retain | int:] }}"
      loop_control:
        label: "{{ item.path }}"

    - name: List local .rsc files for pruning
      ansible.builtin.find:
        paths: "{{ routeros_backup_dir }}/{{ inventory_hostname }}"
        patterns: "{{ inventory_hostname }}-*.rsc"
        file_type: file
      register: existing_rsc_backups
      delegate_to: localhost
      changed_when: false

    - name: Prune .rsc files beyond retention
      ansible.builtin.file:
        path: "{{ item.path }}"
        state: absent
      delegate_to: localhost
      loop: "{{ (existing_rsc_backups.files | sort(attribute='mtime', reverse=true))[routeros_backup_retain | int:] }}"
      loop_control:
        label: "{{ item.path }}"
```

- [ ] **Step 2: yamllint and pre-commit**

```bash
cd /workspace/igou-ansible
yamllint playbooks/routeros/backup.yml playbooks/routeros/tasks/fetch_artifact.yml
pre-commit run --all-files
```

Expected: clean (yamllint exits 0, pre-commit passes).

- [ ] **Step 3: ansible-playbook --syntax-check**

```bash
cd /workspace/igou-ansible
ansible-playbook --syntax-check -i igou-inventory/inventory.yaml playbooks/routeros/backup.yml
```

Expected: `playbook: playbooks/routeros/backup.yml` with no errors.

- [ ] **Step 4 (optional, manual): Run against one device**

```bash
cd /workspace/igou-ansible
ansible-navigator run playbooks/routeros/backup.yml \
  -i igou-inventory/inventory.yaml \
  -e host=crs310.igou.systems
```

Expected outcome:
- Two new files appear at `backups/routeros/crs310.igou.systems/crs310.igou.systems-<ts>.{backup,rsc}`.
- Local file mode is `-rw-------` (0600); directory mode is `drwx------` (0700).
- The `.rsc` file is plaintext and contains `password=` lines (from `show-sensitive`).
- No leftover files under `/file print` on the device for that timestamp.

If `net_get` fails: see the note in Task 2 Step 2 about the `scp` fallback. Revise `tasks/fetch_artifact.yml`, re-run, then continue.

- [ ] **Step 5: Commit**

```bash
cd /workspace/igou-ansible
git add playbooks/routeros/backup.yml
git commit -m "$(cat <<'EOF'
Add backup.yml for RouterOS devices

Saves a binary /system backup and a /export show-sensitive plaintext
config per device, fetches both to <repo-root>/backups/routeros/<host>/
at mode 0600, and prunes to routeros_backup_retain newest of each type.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `manage_users.yml`

**Files:**
- Create: `/workspace/igou-ansible/playbooks/routeros/manage_users.yml`

- [ ] **Step 1: Write `manage_users.yml`**

```yaml
---
# Keep SSH authorized keys for managed RouterOS users in sync.
# Idempotent: only imports keys whose comment field is not already
# present in /user ssh-keys for that user.
#
# Each public key in routeros_users[].ssh_keys MUST include a comment
# (the third whitespace-separated field — e.g. "user@host"). The
# playbook uses that comment string as the dedup key, since RouterOS
# does not surface a stable fingerprint via /user ssh-keys print.
#
# Set routeros_prune_ssh_keys=true to remove keys from the device that
# aren't in the configured list (off by default).
- name: Manage RouterOS user SSH keys
  hosts: "{{ host | default('routeros') }}"
  gather_facts: false

  tasks:
    - name: Read user list
      community.routeros.command:
        commands:
          - "/user print detail without-paging"
      register: user_print
      changed_when: false

    - name: Read existing SSH keys
      community.routeros.command:
        commands:
          - "/user ssh-keys print detail without-paging"
      register: sshkey_print
      changed_when: false

    - name: "Confirm each managed user exists on {{ inventory_hostname }}"
      ansible.builtin.assert:
        that:
          - ('name=\"' ~ item.name ~ '\"') in user_print.stdout[0]
        fail_msg: "User '{{ item.name }}' not present on {{ inventory_hostname }}"
      loop: "{{ routeros_users }}"
      loop_control:
        label: "{{ item.name }}"

    - name: "Import any missing SSH keys"
      vars:
        key_comment: "{{ (item.1.split(' ') | length >= 3) | ternary(item.1.split(' ')[2], '') }}"
        key_user: "{{ item.0.name }}"
        local_tmp: "/tmp/routeros_{{ inventory_hostname }}_{{ key_user }}_{{ ansible_loop.index }}.pub"
        remote_tmp: "tmp_{{ key_user }}_{{ ansible_loop.index }}.pub"
      block:
        - name: "Stage public key locally for {{ key_user }} ({{ key_comment }})"
          ansible.builtin.copy:
            content: "{{ item.1 }}\n"
            dest: "{{ local_tmp }}"
            mode: '0600'
          delegate_to: localhost

        - name: "Push public key for {{ key_user }} ({{ key_comment }}) to device"
          ansible.netcommon.net_put:
            src: "{{ local_tmp }}"
            dest: "{{ remote_tmp }}"

        - name: "Import public key for {{ key_user }} ({{ key_comment }})"
          community.routeros.command:
            commands:
              - "/user ssh-keys import public-key-file={{ remote_tmp }} user={{ key_user }}"

        - name: "Remove staged key file from {{ inventory_hostname }} flash"
          community.routeros.command:
            commands:
              - "/file remove {{ remote_tmp }}"
          changed_when: false
          failed_when: false

        - name: "Remove local staging file"
          ansible.builtin.file:
            path: "{{ local_tmp }}"
            state: absent
          delegate_to: localhost
      loop: "{{ routeros_users | subelements('ssh_keys') }}"
      loop_control:
        extended: true
        label: "{{ item.0.name }}: {{ item.1.split(' ')[2] | default('(no comment)') }}"
      when:
        - (item.1.split(' ') | length) >= 3
        - ('key-owner=' ~ '\"' ~ item.1.split(' ')[2] ~ '\"') not in sshkey_print.stdout[0]
        - ('key-owner=' ~ item.1.split(' ')[2]) not in sshkey_print.stdout[0]

    - name: "Fail loudly for any keys missing a comment field"
      ansible.builtin.assert:
        that:
          - (item.1.split(' ') | length) >= 3
        fail_msg: "Key for {{ item.0.name }} has no comment field; manage_users.yml requires `ssh-<type> <blob> <comment>` format."
      loop: "{{ routeros_users | subelements('ssh_keys') }}"
      loop_control:
        label: "{{ item.0.name }}"

    - name: "Optional: prune device-side keys not in routeros_users"
      ansible.builtin.debug:
        msg: >-
          routeros_prune_ssh_keys is true. Manual review needed — the
          current implementation does not yet auto-prune. To prune,
          inspect /user ssh-keys print detail output above, then run
          /user ssh-keys remove <id> manually. Auto-prune is left for a
          follow-up change once the import path has been validated in
          production.
      when: routeros_prune_ssh_keys | default(false) | bool
```

> **Note on the prune branch:** the spec lists prune as optional and off by default. Auto-prune logic requires correlating each on-device `key-owner` against the configured comment list and issuing a remove for the orphans — non-trivial parsing. This task ships the no-op debug as a placeholder; if the operator turns the flag on later, they can add real logic in a follow-up. This is an intentional, documented limitation, **not** a "TODO" — the alternative would be writing untested parse-and-remove logic that's never been exercised against a real device.

- [ ] **Step 2: yamllint and pre-commit**

```bash
cd /workspace/igou-ansible
yamllint playbooks/routeros/manage_users.yml
pre-commit run --all-files
```

Expected: clean.

- [ ] **Step 3: ansible-playbook --syntax-check**

```bash
cd /workspace/igou-ansible
ansible-playbook --syntax-check -i igou-inventory/inventory.yaml playbooks/routeros/manage_users.yml
```

Expected: no errors.

- [ ] **Step 4 (optional, manual): Run against one device, twice**

```bash
cd /workspace/igou-ansible
# First run — should import the configured key(s)
ansible-navigator run playbooks/routeros/manage_users.yml \
  -i igou-inventory/inventory.yaml \
  -e host=crs310.igou.systems
# Second run — should report 0 changed for the import block
ansible-navigator run playbooks/routeros/manage_users.yml \
  -i igou-inventory/inventory.yaml \
  -e host=crs310.igou.systems
```

Expected:
- First run: `changed=N` where N matches number of new keys.
- Second run: `changed=0` (idempotent).

- [ ] **Step 5: Commit**

```bash
cd /workspace/igou-ansible
git add playbooks/routeros/manage_users.yml
git commit -m "$(cat <<'EOF'
Add manage_users.yml for RouterOS SSH key sync

Imports any SSH keys from routeros_users[].ssh_keys that aren't already
on the device. Uses the public key comment field as the dedup key.
Auto-prune is currently a no-op debug; a follow-up will land real prune
logic once the import path is validated.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `baseline.yml`

**Files:**
- Create: `/workspace/igou-ansible/playbooks/routeros/baseline.yml`

- [ ] **Step 1: Write `baseline.yml`**

```yaml
---
# Apply a small idempotent RouterOS baseline:
#   - NTP client enabled with configured server list
#   - System timezone set
#   - Specified IP services disabled (telnet, ftp, www, www-ssl, api, api-ssl by default)
#
# ssh and winbox are explicitly excluded from the disable list. The SSH
# port (set via inventory's ansible_port) is not managed here — changing
# it mid-run would lock out the connection.
#
# Run idempotently; second run reports changed=0.
- name: Apply RouterOS baseline configuration
  hosts: "{{ host | default('routeros') }}"
  gather_facts: false

  tasks:
    # ---------- NTP ----------
    - name: Read current NTP client config
      community.routeros.command:
        commands:
          - "/system ntp client print without-paging"
      register: ntp_print
      changed_when: false

    - name: Configure NTP client
      community.routeros.command:
        commands:
          - "/system ntp client set enabled=yes servers={{ routeros_ntp_servers | join(',') }}"
      when: >-
        ('enabled: yes' not in ntp_print.stdout[0])
        or ((routeros_ntp_servers | join(',')) not in ntp_print.stdout[0])

    # ---------- Timezone ----------
    - name: Read current clock config
      community.routeros.command:
        commands:
          - "/system clock print without-paging"
      register: clock_print
      changed_when: false

    - name: Set timezone
      community.routeros.command:
        commands:
          - "/system clock set time-zone-name={{ routeros_timezone }}"
      when: ('time-zone-name: ' ~ routeros_timezone) not in clock_print.stdout[0]

    # ---------- IP services ----------
    - name: "Count enabled instances of each service we want disabled"
      community.routeros.command:
        commands:
          - "/ip service print count-only where name={{ item }} and disabled=no"
      register: service_check
      changed_when: false
      loop: "{{ routeros_disabled_services }}"
      loop_control:
        label: "{{ item }}"

    - name: Disable services that are currently enabled
      community.routeros.command:
        commands:
          - "/ip service set [find name={{ item.item }}] disabled=yes"
      when: (item.stdout[0] | trim | int) > 0
      loop: "{{ service_check.results }}"
      loop_control:
        label: "{{ item.item }}"

    # ---------- Recap ----------
    - name: Show post-state for verification
      community.routeros.command:
        commands:
          - "/system clock print"
          - "/system ntp client print"
          - "/ip service print"
      register: post_state
      changed_when: false

    - name: Display recap
      ansible.builtin.debug:
        var: post_state.stdout_lines
      when: ansible_verbosity > 0
```

- [ ] **Step 2: yamllint and pre-commit**

```bash
cd /workspace/igou-ansible
yamllint playbooks/routeros/baseline.yml
pre-commit run --all-files
```

Expected: clean.

- [ ] **Step 3: ansible-playbook --syntax-check**

```bash
cd /workspace/igou-ansible
ansible-playbook --syntax-check -i igou-inventory/inventory.yaml playbooks/routeros/baseline.yml
```

Expected: no errors.

- [ ] **Step 4 (optional, manual): Run against one device, twice**

```bash
cd /workspace/igou-ansible
ansible-navigator run playbooks/routeros/baseline.yml \
  -i igou-inventory/inventory.yaml \
  -e host=crs310.igou.systems
ansible-navigator run playbooks/routeros/baseline.yml \
  -i igou-inventory/inventory.yaml \
  -e host=crs310.igou.systems
```

Expected: second run reports `changed=0`.

If the `count-only where ...` query rejects the syntax on the implementer's RouterOS version, fall back to per-service `print where name=<x> and disabled=no` and check whether the registered `stdout[0]` contains a numeric ID column — adjust the `when:` accordingly.

- [ ] **Step 5: Commit**

```bash
cd /workspace/igou-ansible
git add playbooks/routeros/baseline.yml
git commit -m "$(cat <<'EOF'
Add baseline.yml for RouterOS NTP/timezone/services hardening

Configures NTP servers, sets timezone, and disables legacy IP services
(telnet, ftp, www, www-ssl, api, api-ssl by default). SSH and winbox
are deliberately preserved. Idempotent: a second run reports 0 changed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `upgrade_download.yml` (Phase A)

**Files:**
- Create: `/workspace/igou-ansible/playbooks/routeros/upgrade_download.yml`

- [ ] **Step 1: Write `upgrade_download.yml`**

```yaml
---
# Phase A — stage a RouterOS package update without rebooting.
# Safe to run any time. Run upgrade_apply.yml later (during a maintenance
# window) to actually reboot into the new version.
- name: Stage RouterOS package upgrade
  hosts: "{{ host | default('routeros') }}"
  gather_facts: false

  tasks:
    - name: Read current update status
      community.routeros.command:
        commands:
          - "/system package update print without-paging"
      register: pkg_pre
      changed_when: false

    - name: Set update channel if it differs
      community.routeros.command:
        commands:
          - "/system package update set channel={{ routeros_upgrade_channel }}"
      when: ('channel: ' ~ routeros_upgrade_channel) not in pkg_pre.stdout[0]

    - name: Check for updates
      community.routeros.command:
        commands:
          - "/system package update check-for-updates once"
      register: check_result
      changed_when: false

    - name: Re-read update status after check
      community.routeros.command:
        commands:
          - "/system package update print without-paging"
      register: pkg_status
      changed_when: false

    - name: Show update status
      ansible.builtin.debug:
        var: pkg_status.stdout_lines

    - name: Download update if newer version is available
      community.routeros.command:
        commands:
          - "/system package update download"
      when: pkg_status.stdout[0] is search('status: New version is available')
```

- [ ] **Step 2: yamllint and pre-commit**

```bash
cd /workspace/igou-ansible
yamllint playbooks/routeros/upgrade_download.yml
pre-commit run --all-files
```

Expected: clean.

- [ ] **Step 3: ansible-playbook --syntax-check**

```bash
cd /workspace/igou-ansible
ansible-playbook --syntax-check -i igou-inventory/inventory.yaml playbooks/routeros/upgrade_download.yml
```

Expected: no errors.

- [ ] **Step 4 (optional, manual): Run against one device**

```bash
cd /workspace/igou-ansible
ansible-navigator run playbooks/routeros/upgrade_download.yml \
  -i igou-inventory/inventory.yaml \
  -e host=crs310.igou.systems
```

Expected: status output displays current/latest versions; if a new version exists, the download task fires (and the device shows the package staged in `/system package update print` — `status: New version is available, downloaded`).

- [ ] **Step 5: Commit**

```bash
cd /workspace/igou-ansible
git add playbooks/routeros/upgrade_download.yml
git commit -m "$(cat <<'EOF'
Add upgrade_download.yml (Phase A) for RouterOS

Stages a RouterOS package update without rebooting. Sets the channel
from routeros_upgrade_channel, runs check-for-updates, downloads if
newer. Safe to run any time; pair with upgrade_apply.yml during a
maintenance window.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Shared task — `tasks/wait_for_routeros.yml`

**Files:**
- Create: `/workspace/igou-ansible/playbooks/routeros/tasks/wait_for_routeros.yml`

- [ ] **Step 1: Write `tasks/wait_for_routeros.yml`**

```yaml
---
# Wait for a RouterOS device to disconnect (after a /system reboot) and
# come back, then smoke-test the connection. Used by upgrade_apply.yml.
#
# Operates on `inventory_hostname` and the inventory's ansible_port
# (must be set; no fallback). Uses control-node-side wait_for; no agent
# on device.
- name: "Wait for {{ inventory_hostname }} SSH to drop"
  ansible.builtin.wait_for:
    host: "{{ inventory_hostname }}"
    port: "{{ ansible_port }}"
    state: stopped
    delay: 5
    timeout: 60
  delegate_to: localhost

- name: "Wait for {{ inventory_hostname }} SSH to return"
  ansible.builtin.wait_for:
    host: "{{ inventory_hostname }}"
    port: "{{ ansible_port }}"
    state: started
    delay: 30
    timeout: 600
  delegate_to: localhost

- name: Reset network_cli connection
  ansible.builtin.meta: reset_connection

- name: Smoke-test post-reboot connectivity
  community.routeros.command:
    commands:
      - "/system identity print"
  register: smoke
  changed_when: false

- name: Show smoke-test output
  ansible.builtin.debug:
    var: smoke.stdout_lines
```

- [ ] **Step 2: yamllint**

```bash
cd /workspace/igou-ansible
yamllint playbooks/routeros/tasks/wait_for_routeros.yml
```

Expected: clean. (ansible-lint check is deferred to Task 8 alongside `upgrade_apply.yml` for the same reason as Task 2.)

- [ ] **Step 3: Commit**

```bash
cd /workspace/igou-ansible
git add playbooks/routeros/tasks/wait_for_routeros.yml
git commit -m "$(cat <<'EOF'
Add wait_for_routeros shared task

Drops the network_cli connection, waits for SSH to close and return,
re-establishes, and runs /system identity print as a smoke test.
Consumed by upgrade_apply.yml between the package reboot and the
optional firmware reboot.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `upgrade_apply.yml` (Phase B)

**Files:**
- Create: `/workspace/igou-ansible/playbooks/routeros/upgrade_apply.yml`

- [ ] **Step 1: Write `upgrade_apply.yml`**

```yaml
---
# Phase B — apply a previously-staged RouterOS package update, verify it
# took effect, and (if applicable) upgrade the routerboard firmware
# with a second reboot.
#
# Imports backup.yml first so a fresh backup pair always exists before
# any reboot, then proceeds serially (one device at a time).
- name: Take pre-upgrade backups
  import_playbook: backup.yml

- name: Apply RouterOS package + firmware upgrade
  hosts: "{{ host | default('routeros') }}"
  gather_facts: false
  serial: 1

  tasks:
    - name: Read pre-upgrade package status
      community.routeros.command:
        commands:
          - "/system package update print without-paging"
      register: pkg_pre
      changed_when: false

    - name: "Pre-flight — confirm package status is ready"
      ansible.builtin.assert:
        that:
          - >-
            (pkg_pre.stdout[0] is search('status: New version is available'))
            or (pkg_pre.stdout[0] is search('status: System is already up to date'))
        fail_msg: >-
          Refusing to upgrade {{ inventory_hostname }} — unexpected package
          status. Run upgrade_download.yml first, then retry.

    - name: Capture pre-upgrade installed version
      ansible.builtin.set_fact:
        pre_version: "{{ pkg_pre.stdout[0] | regex_search('installed-version: (\\S+)', '\\1') | first | default('unknown') }}"

    - name: "Warn if a newer version was published since download"
      ansible.builtin.debug:
        msg: >-
          {{ inventory_hostname }}: latest-version may be newer than the
          downloaded version. After this run, re-run upgrade_download.yml
          to pull the newer release.
      when:
        - pkg_pre.stdout[0] is search('latest-version:')
        - (pkg_pre.stdout[0] | regex_search('latest-version: (\\S+)', '\\1') | first | default('')) !=
          (pkg_pre.stdout[0] | regex_search('installed-version: (\\S+)', '\\1') | first | default(''))
        - pkg_pre.stdout[0] is search('status: New version is available')

    - name: Reboot to apply package
      community.routeros.command:
        commands:
          - "/system reboot"
      register: reboot_result
      failed_when: false
      changed_when: true
      ignore_errors: true

    - name: Wait for device to come back after package reboot
      ansible.builtin.import_tasks: tasks/wait_for_routeros.yml

    - name: Read post-upgrade resource info
      community.routeros.command:
        commands:
          - "/system resource print without-paging"
      register: resource_post
      changed_when: false

    - name: "Verify version changed (or no upgrade was needed)"
      ansible.builtin.assert:
        that:
          - >-
            ((resource_post.stdout[0] | regex_search('version: (\\S+)', '\\1') | first) != pre_version)
            or (pkg_pre.stdout[0] is search('status: System is already up to date'))
        fail_msg: >-
          Upgrade did not take effect on {{ inventory_hostname }}: still
          on {{ pre_version }}. Backup is at backups/routeros/{{ inventory_hostname }}/.

    - name: Read routerboard firmware status
      community.routeros.command:
        commands:
          - "/system routerboard print without-paging"
      register: rb_print
      changed_when: false

    - name: Determine firmware versions
      ansible.builtin.set_fact:
        rb_current: "{{ rb_print.stdout[0] | regex_search('current-firmware: (\\S+)', '\\1') | first | default('') }}"
        rb_upgrade: "{{ rb_print.stdout[0] | regex_search('upgrade-firmware: (\\S+)', '\\1') | first | default('') }}"

    - name: Routerboard firmware upgrade
      when:
        - rb_upgrade | length > 0
        - rb_current != rb_upgrade
      block:
        - name: Schedule firmware upgrade
          community.routeros.command:
            commands:
              - "/system routerboard upgrade"

        - name: Reboot for firmware upgrade
          community.routeros.command:
            commands:
              - "/system reboot"
          register: fw_reboot_result
          failed_when: false
          changed_when: true
          ignore_errors: true

        - name: Wait for device to come back after firmware reboot
          ansible.builtin.import_tasks: tasks/wait_for_routeros.yml

        - name: Re-read routerboard firmware status
          community.routeros.command:
            commands:
              - "/system routerboard print without-paging"
          register: rb_post
          changed_when: false

        - name: "Verify firmware upgrade took effect on {{ inventory_hostname }}"
          ansible.builtin.assert:
            that:
              - (rb_post.stdout[0] | regex_search('current-firmware: (\\S+)', '\\1') | first) == rb_upgrade
            fail_msg: >-
              Routerboard firmware did not upgrade to {{ rb_upgrade }} on
              {{ inventory_hostname }}.
```

- [ ] **Step 2: yamllint and pre-commit**

```bash
cd /workspace/igou-ansible
yamllint playbooks/routeros/upgrade_apply.yml playbooks/routeros/tasks/wait_for_routeros.yml
pre-commit run --all-files
```

Expected: clean. This run also covers `tasks/wait_for_routeros.yml` because it's now imported by a real playbook (so ansible-lint can resolve it in context).

- [ ] **Step 3: ansible-playbook --syntax-check**

```bash
cd /workspace/igou-ansible
ansible-playbook --syntax-check -i igou-inventory/inventory.yaml playbooks/routeros/upgrade_apply.yml
```

Expected: no errors. Note: `--syntax-check` also descends into the imported `backup.yml` and `tasks/wait_for_routeros.yml`.

- [ ] **Step 4 (optional, manual): Run against one device during a maintenance window**

```bash
cd /workspace/igou-ansible
ansible-navigator run playbooks/routeros/upgrade_apply.yml \
  -i igou-inventory/inventory.yaml \
  -e host=crs310.igou.systems
```

Expected:
- A fresh `crs310.igou.systems-<ts>.{backup,rsc}` pair appears in `backups/routeros/`.
- The device reboots, comes back within 10 minutes, runs `/system identity print` cleanly.
- `/system resource print` shows the upgraded version.
- If routerboard firmware was out of sync, a second reboot occurs and `/system routerboard print` shows `current-firmware == upgrade-firmware`.

If the device fails to come back within `wait_for_routeros.yml`'s timeout, the play halts (because of `serial: 1`), the remaining hosts are untouched, and the operator goes investigate with the backup in hand.

- [ ] **Step 5: Commit**

```bash
cd /workspace/igou-ansible
git add playbooks/routeros/upgrade_apply.yml
git commit -m "$(cat <<'EOF'
Add upgrade_apply.yml (Phase B) for RouterOS

Imports backup.yml as a hard pre-flight, then per-host (serial=1)
reboots into the staged package, verifies the version changed, and
upgrades routerboard firmware with a second reboot when needed. Uses
the shared wait_for_routeros task between reboots.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

**Spec coverage:**

| Spec section | Covered by |
|---|---|
| File layout (5 playbooks + 2 task files) | Tasks 2-8 |
| Inventory variable additions | Task 1 |
| `ansible_user` change to `igou+cet1024w` | Task 1 |
| `.gitignore /backups/` | Task 1 |
| `backup.yml` flow | Task 3 |
| `manage_users.yml` flow (with prune-as-no-op limitation) | Task 4 |
| `baseline.yml` (NTP, timezone, services) | Task 5 |
| `upgrade_download.yml` (Phase A) | Task 6 |
| `upgrade_apply.yml` (Phase B) | Task 8 |
| `tasks/wait_for_routeros.yml` | Task 7 |
| `tasks/fetch_artifact.yml` | Task 2 |
| Secrets posture (0700/0600, gitignored) | Task 3 |
| Testing strategy (lint + syntax-check + manual) | Each task's steps 2-4 |

**Known limitations documented in the plan (not "TODO" placeholders):**
- `manage_users.yml` auto-prune branch is a debug message, not real prune logic. The spec listed prune as off-by-default and optional; the design even called it out as "off by default; flip to true once you trust the playbook." The plan ships the off-state and explicitly leaves real prune logic for a follow-up. This is a deliberate scope cut, mirroring the spec.
- `fetch_artifact.yml` documents the `scp`-fallback in case `net_get` doesn't work over RouterOS network_cli. This is verification-during-Task-3, not a placeholder.
- `baseline.yml` documents a `print where ... and disabled=no` fallback if `count-only where` rejects on a given RouterOS version. Same pattern.

**Type/identifier consistency:**
- All playbooks reference variables from `group_vars/routeros.yml` defined in Task 1 (`routeros_backup_dir`, `routeros_backup_retain`, `routeros_timezone`, `routeros_ntp_servers`, `routeros_disabled_services`, `routeros_upgrade_channel`, `routeros_users`, `routeros_prune_ssh_keys`).
- `tasks/fetch_artifact.yml` interface (`remote_filename`, `local_dir`) matches `backup.yml`'s `include_tasks` invocation.
- `tasks/wait_for_routeros.yml` reads `inventory_hostname` and `ansible_port` directly from inventory — no parameters needed.
- `upgrade_apply.yml` imports `backup.yml` (defined Task 3) and `tasks/wait_for_routeros.yml` (defined Task 7).
- Pattern names (`<host>-<ts>.backup` and `<host>-<ts>.rsc`) are consistent across save, fetch, and prune steps.
