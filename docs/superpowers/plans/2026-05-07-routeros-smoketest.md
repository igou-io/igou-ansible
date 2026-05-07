---
title: Implementation plan — RouterOS smoke test playbook
date: 2026-05-07
status: approved (auto-approved by operator)
spec: ../specs/2026-05-07-routeros-smoketest-design.md
---

# Implementation plan — RouterOS smoke test playbook

Single task, single file. Playbook is ~80 lines and self-contained;
no decomposition needed.

## Task 1 — Write `playbooks/routeros/smoketest.yml`

### File

`playbooks/routeros/smoketest.yml`

### Content

Implement the 7 tasks listed in the spec. Use:

- `community.routeros.command` for every device command.
- `ansible.builtin.assert` for positive state checks.
- `changed_when: false` on every command task (these are reads).
- `loop:` over `routeros_disabled_services` for per-service enabled
  count (one `count-only` command per service).
- `loop:` over `routeros_users` for the user-existence assertion.
- `loop:` over `routeros_users | subelements('ssh_keys')` for the
  per-key informational debug.

Inventory variables consumed (already defined in
`igou-inventory/group_vars/routeros.yml`):

- `routeros_timezone`
- `routeros_ntp_servers` (not asserted; only used by `baseline.yml`'s
  write path — out of scope for this smoke test)
- `routeros_disabled_services`
- `routeros_upgrade_channel`
- `routeros_users` (list of dicts with `name` and `ssh_keys`)

Skeleton:

```yaml
---
# Smoke test for the routeros playbook suite. Connects to every host
# and runs only the read commands that backup.yml, manage_users.yml,
# baseline.yml, upgrade_download.yml, and upgrade_apply.yml depend on.
# Asserts that inventory variables match live device state. Never
# writes, never reboots, never touches device flash.
- name: RouterOS smoke test
  hosts: "{{ host | default('routeros') }}"
  gather_facts: false

  tasks:
    - name: Connectivity probe
      community.routeros.command:
        commands:
          - "/system identity print"
          - "/system resource print"
      register: probe
      changed_when: false

    - name: Show probe output
      ansible.builtin.debug:
        var: probe.stdout_lines

    - name: Read NTP client and clock
      community.routeros.command:
        commands:
          - "/system ntp client print without-paging"
          - "/system clock print without-paging"
      register: ntp_clock
      changed_when: false

    - name: Assert NTP enabled and timezone matches inventory
      ansible.builtin.assert:
        that:
          - "'enabled: yes' in ntp_clock.stdout[0]"
          - "('time-zone-name: ' ~ routeros_timezone) in ntp_clock.stdout[1]"
        fail_msg: >-
          NTP client disabled or timezone mismatch on
          {{ inventory_hostname }}.

    - name: Read full IP service table
      community.routeros.command:
        commands:
          - "/ip service print without-paging"
      register: ip_service
      changed_when: false

    - name: Count enabled instances of each policy-disabled service
      community.routeros.command:
        commands:
          - "/ip service print count-only where name={{ item }} and disabled=no"
      register: svc_check
      changed_when: false
      loop: "{{ routeros_disabled_services }}"
      loop_control:
        label: "{{ item }}"

    - name: Assert no policy-disabled service is enabled
      ansible.builtin.assert:
        that:
          - (item.stdout[0] | trim | int) == 0
        fail_msg: >-
          Service '{{ item.item }}' is enabled on
          {{ inventory_hostname }} (policy says disabled).
      loop: "{{ svc_check.results }}"
      loop_control:
        label: "{{ item.item }}"

    - name: Read user list and SSH keys
      community.routeros.command:
        commands:
          - "/user print detail without-paging"
          - "/user ssh-keys print detail without-paging"
      register: users_keys
      changed_when: false

    - name: Assert each managed user exists on device
      ansible.builtin.assert:
        that:
          - ('name=\"' ~ item.name ~ '\"') in users_keys.stdout[0]
        fail_msg: >-
          Managed user '{{ item.name }}' missing on
          {{ inventory_hostname }}.
      loop: "{{ routeros_users }}"
      loop_control:
        label: "{{ item.name }}"

    - name: Report SSH key presence informationally
      ansible.builtin.debug:
        msg: >-
          {{ inventory_hostname }} {{ item.0.name }} key
          '{{ item.1.split(' ')[2] }}':
          {{ 'PRESENT' if (item.1.split(' ')[2] in users_keys.stdout[1]) else 'MISSING' }}
      loop: "{{ routeros_users | subelements('ssh_keys') }}"
      loop_control:
        label: "{{ item.0.name }}: {{ item.1.split(' ')[2] }}"

    - name: Read package update status
      community.routeros.command:
        commands:
          - "/system package update print without-paging"
      register: pkg
      changed_when: false

    - name: Assert package update channel matches inventory
      ansible.builtin.assert:
        that:
          - ('channel: ' ~ routeros_upgrade_channel) in pkg.stdout[0]
        fail_msg: >-
          Package update channel on {{ inventory_hostname }} does not
          match routeros_upgrade_channel ({{ routeros_upgrade_channel }}).

    - name: Read routerboard status
      community.routeros.command:
        commands:
          - "/system routerboard print without-paging"
      register: rb
      changed_when: false

    - name: Show routerboard status
      ansible.builtin.debug:
        var: rb.stdout_lines

    - name: Backup precheck -- inline export must succeed
      community.routeros.command:
        commands:
          - "/export show-sensitive"
      register: export_probe
      changed_when: false
      no_log: true
```

`no_log: true` on the export probe prevents the secret-bearing output
from landing in logs.

### Validation

Run all three from `/workspace/igou-ansible`:

```
ansible-lint --profile=production playbooks/routeros/smoketest.yml
ansible-playbook --syntax-check playbooks/routeros/smoketest.yml
yamllint playbooks/routeros/smoketest.yml
```

All three must exit 0 before commit.

### Lint pitfalls to avoid (learned in prior routeros playbook work)

- **`name[template]`**: Jinja templates must come at the END of name
  strings, not the start. The skeleton above already complies — keep
  it that way.
- **`jinja[spacing]`**: slice expressions need spaces around the colon
  (`[expr | int :]`). Not used in this file but worth remembering.
- **`yaml[colons]`**: any string containing `: ` (colon followed by a
  space) inside a `when:` value must be wrapped in double quotes — the
  skeleton above already does this for the timezone and channel
  asserts.
- The `\"` inside `('name=\"' ~ item.name ~ '\"')` is correct in
  single-quoted YAML strings — backslashes are literal there.

### Commit

After validation passes, commit the playbook in its own commit:

```
Add smoketest.yml for RouterOS read-path verification

Single playbook that connects to every routeros host and runs only
the read commands the production playbooks depend on. Asserts
inventory variables match live device state. No writes, no reboots,
no flash usage.
```

The spec and plan docs are committed separately.

## Acceptance

Task 1 is the only task. Done when the file exists, all three lint
checks pass, and the commit is in place.
