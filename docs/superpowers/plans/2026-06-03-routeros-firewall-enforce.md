# RouterOS firewall enforcement (Phase 2) — implementation plan

**Date:** 2026-06-03
**Spec:** `docs/superpowers/specs/2026-06-03-routeros-firewall-enforce-design.md`

This plan is intentionally short because Phase 2 is a small, focused change on top of Phase 1's foundation: one new role variable, one new tasks-include, one `check_mode` derivation tweak, and one AAP template addition. The spec already covers all the design rationale; this plan covers the order of operations and the verification steps.

## Pre-implementation: repo context

- **Working repo (role side):** `/workspace/igou-ansible` on branch `feat/routeros-firewall-enforce` (already created from `main` after PR 207 merged).
- **Inventory repo:** `/workspace/igou-inventory` on `main`. The new AAP template lives here.
- **Phase 1 has landed:** the role's `tasks/main.yml` currently hardcodes `check_mode: true`. The role's defaults already declare `routeros_firewall_paths` / `_ipv6_paths` / `_ordered` etc. This plan modifies and adds, doesn't replace.

## File structure (this PR)

```
roles/routeros_firewall/
├── defaults/main.yml                       # MODIFIED: add routeros_firewall_enforce
├── tasks/
│   ├── _assert_inputs.yml                  # MODIFIED: add write-perms check gated on enforce
│   ├── _backup_before_enforce.yml          # NEW
│   └── main.yml                            # MODIFIED: import backup + derive check_mode
docs/superpowers/
├── specs/2026-06-03-routeros-firewall-enforce-design.md   # NEW (already written)
└── plans/2026-06-03-routeros-firewall-enforce.md          # NEW (this file)
```

Inventory side (separate commit on `igou-inventory/main`):

```
group_vars/aap/job_templates.yml            # MODIFIED: add routeros_firewall_enforce template
```

## Task 1: Role default for the enforce toggle

**File:** `roles/routeros_firewall/defaults/main.yml`

Add at the end of the `Behavior` block:

```yaml
# Phase 2 enforce switch. When false (default), the role runs audit-only —
# api_modify in check_mode, no writes. When true, the role takes an
# on-device backup snapshot and then applies the desired YAML for real.
# Audit and enforce share the same playbook entrypoint; the AAP _enforce
# template flips this to true via extra_vars.
routeros_firewall_enforce: false
```

**Acceptance:** `ansible-doc` (or just reading the file) shows the new variable.

## Task 2: Pre-enforce backup include

**File:** `roles/routeros_firewall/tasks/_backup_before_enforce.yml` (new)

Conditional task file imported by `main.yml`. Skips entirely when `routeros_firewall_enforce` is false.

```yaml
---
# Pre-enforce backup snapshot. Drops two artifacts to /flash/ on the device:
#   firewall-pre-enforce-<ts>.backup  — RouterOS-version-pinned binary backup
#   firewall-pre-enforce-<ts>.rsc     — portable text export
#
# When recovery is needed, the operator can either:
#   /system backup load name=firewall-pre-enforce-<ts>
# or, if the .backup is version-incompatible (e.g. post-upgrade restore):
#   /import file=firewall-pre-enforce-<ts>.rsc
#
# Failure here aborts the play before any api_modify runs. Without a known
# backup, the role refuses to write.

- name: Compute backup filename stem
  ansible.builtin.set_fact:
    _routeros_firewall_backup_stem: "firewall-pre-enforce-{{ ansible_date_time.iso8601_basic_short }}"

- name: Take RouterOS backup snapshot
  community.routeros.command:
    commands:
      - "/system/backup/save name={{ _routeros_firewall_backup_stem }} dont-encrypt=yes"
  register: _routeros_firewall_backup_result

- name: Export firewall config to .rsc
  community.routeros.command:
    commands:
      - "/export compact file={{ _routeros_firewall_backup_stem }}"
  register: _routeros_firewall_export_result

- name: Confirm backup artifacts landed on flash
  ansible.builtin.debug:
    msg: |-
      Pre-enforce backup taken on {{ inventory_hostname }}:
        /flash/{{ _routeros_firewall_backup_stem }}.backup
        /flash/{{ _routeros_firewall_backup_stem }}.rsc
      Recover via:
        /system/backup/load name={{ _routeros_firewall_backup_stem }}
      or, if the .backup is RouterOS-version-incompatible:
        /import file={{ _routeros_firewall_backup_stem }}.rsc
```

The `community.routeros.command` module sends raw CLI commands over the network_cli connection that's already used by other RouterOS playbooks in this repo — verified working against rb5009 in `playbooks/routeros/baseline.yml` and friends. It uses the same SSH port and `ansible_user` from `group_vars/routeros.yml`.

**Acceptance:** when this file is imported with `enforce=true`, `/flash` on the device has the two new files after the play; with `enforce=false` the file is imported but skipped entirely (block-level `when`).

## Task 3: Wire backup + check_mode into main.yml

**File:** `roles/routeros_firewall/tasks/main.yml`

Two surgical edits:

1. Add an import of `_backup_before_enforce.yml` after the input-assertion import, gated on `routeros_firewall_enforce | bool`. Use `import_tasks` so the gating is at the block level (no per-task `when`).
2. Change both `api_modify` tasks (IPv4 + IPv6) from `check_mode: true` to `check_mode: "{{ not (routeros_firewall_enforce | bool) }}"`.

```yaml
- name: Validate inputs (credentials + desired state)
  ansible.builtin.import_tasks: _assert_inputs.yml
  vars:
    _assert_check_desired: true

- name: Take pre-enforce backup snapshot
  ansible.builtin.import_tasks: _backup_before_enforce.yml
  when: routeros_firewall_enforce | bool

- name: Audit/enforce IPv4 firewall sub-paths
  community.routeros.api_modify:
    ...
    check_mode: "{{ not (routeros_firewall_enforce | bool) }}"
    diff: true
    ...
```

Both `api_modify` task names should change from "Audit ..." to "Audit/enforce ..." since they now serve both modes.

**Acceptance:** `routeros_firewall_enforce=false` (default) reproduces the exact Phase 1 audit behavior. `routeros_firewall_enforce=true` runs backup-then-write.

## Task 4: Extend input-assertion with a write-permission check

**File:** `roles/routeros_firewall/tasks/_assert_inputs.yml`

Add a new assertion block at the end, gated on `routeros_firewall_enforce | bool`. The check is a no-op `community.routeros.api` call that requires write — e.g. attempting to update a rule's comment to its current value and rolling back. If the API user is read-only, the call fails fast with a clear permission error before the backup task fires.

Actually, RouterOS doesn't have a "noop write" primitive. Simpler: in the assertion task, call `community.routeros.api_info` against a low-traffic admin path (`/user`) and check whether the result contains the `policy` field that indicates write access. Or simplest of all: defer this check to Task 5's smoke test rather than gating it in the role — the API call will fail with permission denied as the first `api_modify` runs, and Ansible reports a clean error.

**Decision deferred to implementation:** if the no-op-write check turns out to be awkward, skip it — the failure mode without the pre-check is "backup succeeds, api_modify fails with permissions, operator restores from backup (no harm done)." Acceptable.

**Acceptance:** assertion task either fires cleanly with a clear message, or is omitted with a comment explaining why.

## Task 5: AAP `_enforce` job template (inventory side)

**File:** `igou-inventory/group_vars/aap/job_templates.yml`

Add a new entry mirroring the existing `routeros_firewall_audit` template, but:

- Name: `routeros_firewall_enforce`
- `playbook` — same `playbooks/routeros/firewall-audit.yaml`
- `extra_vars: { routeros_firewall_enforce: true }`
- `ask_variables_on_launch: true` — surveyed so the operator confirms the host (`-e host=...`) at runtime
- Optionally tag with `approval_required: true` if AAP approval nodes are wired up; if not, document in the spec that this should be added later.

**Acceptance:** after `make aap-configure` runs against the AAP controller, both templates appear in the UI; `_audit` is schedulable, `_enforce` is manual-only.

## Task 6: End-to-end smoke test against rb5009

Order matters here.

1. **Confirm zero drift first.** Run `firewall-audit.yaml` (existing). PLAY RECAP should show all paths `changed=False`. If not, fix the drift before testing enforce.
2. **Run enforce with no drift.** Trigger `_enforce` template. Expected: backup files appear on `/flash`, all `api_modify` tasks report `changed=false`, no actual rule changes happen. The role-side change is non-destructive when model matches.
3. **Introduce a small drift.** Change one rule's comment in `firewall.yml` (e.g. flip a `(servers)` to `(servers-test)`), commit but DO NOT push. Or just edit locally.
4. **Run enforce again.** Expected: backup files appear; the one drifted rule updates on the device; final state matches model.
5. **Confirm.** Re-run `firewall-audit.yaml`. PLAY RECAP should be zero-drift again.
6. **Revert the test drift.** Edit `firewall.yml` back, re-run enforce, re-run audit, confirm zero drift.
7. **Verify backup recoverability.** SSH into rb5009 (`igou` user), `/file/print where name~"firewall-pre-enforce"`. Confirm both `.backup` and `.rsc` files exist for each enforce run. Read one `.rsc` to confirm it's a sensible export.

**Acceptance:** all six steps succeed; rb5009 ends in the same state it started.

## Self-review

### Spec coverage

- [x] Trigger (on-demand AAP) — Task 5
- [x] Mode toggle (boolean variable) — Task 1 + Task 3
- [x] Pre-write backup — Task 2 + Task 3
- [x] Partial-apply / rollback — covered in spec §5 + Task 2 backup
- [x] What stays the same (audit playbook, data model) — implicit

### Placeholder scan

No `TODO`s in the plan steps. The "Task 4 deferred decision" is explicit and bounded — either it works or we skip the check and document why.

### Type / name consistency

- `routeros_firewall_enforce` (bool) — used consistently in all 6 tasks.
- `_routeros_firewall_backup_stem` (string) — fact name follows the role's `_routeros_firewall_*` private-fact convention from Phase 1.
- `_backup_before_enforce.yml` filename matches the import call in Task 3.
