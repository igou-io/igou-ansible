---
title: RouterOS smoke test playbook
date: 2026-05-07
status: approved (auto-approved by operator)
---

# RouterOS smoke test playbook

## Goal

Provide a single Ansible playbook that connects to every device in the
`routeros` inventory group and exercises only the read commands the
existing production playbooks (`backup.yml`, `manage_users.yml`,
`baseline.yml`, `upgrade_download.yml`, `upgrade_apply.yml`) depend on.
Catch read-path drift between inventory variables and live device state
with no potential to be destructive.

## Non-goals

- No CHR VM, no GitHub Actions integration, no AAP job template.
- No production playbook changes — no tagging, no dry-run variable, no
  refactor.
- No verification of write paths (backup save, package download,
  reboots, ssh-key import).
- No restore playbook (none exists).
- No molecule scenario for routeros (RouterOS does not run in
  containers).
- No new Makefile target — operator runs `ansible-playbook` directly.

## File layout

- New file: `playbooks/routeros/smoketest.yml` (~80 lines).
- Existing `playbooks/routeros/test_connection.yaml` is untouched.
- No new inventory variables, no new tasks files, no new roles.

## Tasks performed by the playbook

The playbook targets `hosts: "{{ host | default('routeros') }}"` and
runs the following tasks in order. Every `community.routeros.command`
task uses `changed_when: false`. Assertions are positive checks against
`routeros_*` inventory variables set in
`igou-inventory/group_vars/routeros.yml`.

1. **Connectivity probe** — `/system identity print` +
   `/system resource print`. Output registered for debug.
2. **NTP + clock readback** (covers `baseline.yml` reads) —
   `/system ntp client print without-paging` and
   `/system clock print without-paging`. Assert `enabled: yes` is
   present in the NTP output and
   `time-zone-name: {{ routeros_timezone }}` is present in the clock
   output.
3. **Service readback** (covers `baseline.yml` reads) —
   `/ip service print without-paging`. For each service in
   `routeros_disabled_services`, run
   `/ip service print count-only where name=<svc> and disabled=no` and
   assert the count is `0`.
4. **User + SSH key readback** (covers `manage_users.yml` reads) —
   `/user print detail without-paging` +
   `/user ssh-keys print detail without-paging`. For each user in
   `routeros_users`, assert the user appears in the user-print output.
   For each managed key, log informationally whether the key's comment
   string is already present in the ssh-keys output (not a failure).
5. **Package update readback** (covers `upgrade_download.yml` and
   `upgrade_apply.yml` reads) — `/system package update print
   without-paging`. Assert `channel: {{ routeros_upgrade_channel }}` is
   present.
6. **Routerboard readback** (covers `upgrade_apply.yml` reads) —
   `/system routerboard print without-paging`. No assertion (some
   devices may report no separate firmware to upgrade).
7. **Backup precheck** (covers `backup.yml`) — `/export show-sensitive`
   (no `file=`, returns inline so no flash write). Register output but
   do NOT debug-print it (export contains secrets). Failure indicates
   the device denies export to the connecting user.

## What the playbook intentionally does NOT touch

- `/system backup save` (writes to flash)
- `/export ... file=...` (writes to flash; the inline form in step 7
  returns to stdout instead)
- `/system package update check-for-updates` (mutates cached
  `latest-version` field)
- `/system package update download` (writes to flash)
- `/system package update set channel=...` (config change)
- `/ip service set ... disabled=...` (config change)
- `/system clock set time-zone-name=...` (config change)
- `/system ntp client set ...` (config change)
- `/user ssh-keys import ...` (config change)
- `/system reboot`, `/system routerboard upgrade`
- `net_put`, `net_get`, `meta: reset_connection`, `wait_for`

## How to run

```
ansible-playbook playbooks/routeros/smoketest.yml \
  -i ../igou-inventory/inventory.yaml

# Scope to a single device:
ansible-playbook playbooks/routeros/smoketest.yml \
  -i ../igou-inventory/inventory.yaml \
  -e host=rb5009.igou.systems
```

The playbook is idempotent. Back-to-back runs yield identical output.

## Acceptance criteria

- `ansible-lint --profile=production` passes for the new file.
- `ansible-playbook --syntax-check playbooks/routeros/smoketest.yml`
  passes.
- `yamllint .` passes.
- Running against the `routeros` group when device state matches
  inventory variables exits 0 with `changed=0` for every host in the
  recap.
- Simulated drift (e.g., NTP disabled on one device) causes the
  relevant assertion to fail with a message naming the failing host
  and check.

## Trade-off

This is a smoke test, not a behavioral test. It validates that read
paths and inventory variables line up with reality. It cannot catch
bugs in write logic — for that, the write paths must be exercised
against a CHR VM or a real device during a maintenance window. That is
out of scope for this spec.
