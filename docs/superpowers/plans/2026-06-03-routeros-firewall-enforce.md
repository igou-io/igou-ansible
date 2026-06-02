# RouterOS firewall enforcement (Phase 2) — implementation plan

**Date:** 2026-06-03
**Spec:** `docs/superpowers/specs/2026-06-03-routeros-firewall-enforce-design.md`

This plan is intentionally short because Phase 2 is a small, focused change on top of Phase 1's foundation: one new role variable, one `check_mode` derivation tweak, an `import_playbook` at the playbook layer to chain S3 backup, and one AAP template addition. The spec already covers all the design rationale; this plan covers the order of operations and the verification steps.

## Pre-implementation: repo context

- **Working repo (role side):** `/workspace/igou-ansible` on branch `feat/routeros-firewall-enforce` (already created from `main` after PR 207 merged).
- **Inventory repo:** `/workspace/igou-inventory` on `main`. The new AAP template lives here.
- **Phase 1 has landed:** the role's `tasks/main.yml` currently hardcodes `check_mode: true`. The role's defaults already declare `routeros_firewall_paths` / `_ipv6_paths` / `_ordered` etc.
- **`backup_s3.yaml` already exists** at `playbooks/routeros/backup_s3.yaml` — it handles the on-device backup + plaintext export + S3 upload with tier tagging. Phase 2 reuses it; it does not get modified.

## File structure (this PR)

```
roles/routeros_firewall/
├── defaults/main.yml                       # MODIFIED: add routeros_firewall_enforce
└── tasks/main.yml                          # MODIFIED: derive check_mode from the toggle
playbooks/routeros/
└── firewall-audit.yaml                     # MODIFIED: prepend conditional import_playbook of backup_s3.yaml
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
# api_modify in check_mode, no writes. When true, api_modify commits writes
# against the device. Pre-enforce backup is handled at the playbook layer
# (firewall-audit.yaml chains backup_s3.yaml when both enforce and
# routeros_firewall_pre_enforce_backup are true); the role itself is
# lab-agnostic and doesn't assume an S3 backend.
routeros_firewall_enforce: false
```

**Acceptance:** reading the file shows the new variable.

## Task 2: Derive check_mode in main.yml

**File:** `roles/routeros_firewall/tasks/main.yml`

One surgical edit per `api_modify` task (IPv4 + IPv6): `check_mode: true` → `check_mode: "{{ not (routeros_firewall_enforce | bool) }}"`. Update task names from "Audit ..." to "Audit or enforce ..." since they serve both modes. Update the summary debug to label the mode.

**Acceptance:** `routeros_firewall_enforce=false` (default) reproduces Phase 1 audit behavior exactly. `routeros_firewall_enforce=true` writes.

## Task 3: Chain backup_s3 from firewall-audit.yaml

**File:** `playbooks/routeros/firewall-audit.yaml`

Prepend an `import_playbook` of `backup_s3.yaml` with:

```yaml
- name: Pre-enforce S3 backup (opt-in via routeros_firewall_pre_enforce_backup)
  ansible.builtin.import_playbook: backup_s3.yaml
  vars:
    routeros_s3_tier: pre-enforce
  when:
    - routeros_firewall_enforce | default(false) | bool
    - routeros_firewall_pre_enforce_backup | default(false) | bool
```

Update the playbook's docstring to explain the chained-backup behavior.

`import_playbook` is a static import — the `when` is applied to every task in the imported playbook, so when the gate is false the backup play's tasks are simply skipped. No special-casing needed.

**Acceptance:** with both flags true, the backup play runs and an object lands in S3 with `tier=pre-enforce`. With either flag false, the backup play's tasks all skip.

## Task 4: AAP `_enforce` job template (inventory side)

**File:** `igou-inventory/group_vars/aap/job_templates.yml`

Add a new entry mirroring the existing `routeros_firewall_audit` template, but:

- Name: `routeros_firewall_enforce`
- `playbook` — same `playbooks/routeros/firewall-audit.yaml`
- `extra_vars`: `routeros_firewall_enforce: true` AND `routeros_firewall_pre_enforce_backup: true`
- `ask_variables_on_launch: true` — surveyed so the operator confirms at trigger time
- `diff_mode: true` (matches the audit template)
- No schedule (manual trigger only)

**Acceptance:** after `make aap-configure`, both templates appear in the UI; `_audit` is schedulable, `_enforce` is manual-only.

## Task 5: End-to-end smoke test against rb5009

1. **Audit baseline.** Run `firewall-audit.yaml` (no extra vars). PLAY RECAP should show all paths `changed=False`.
2. **Enforce with no drift, no backup flag.** Pass `routeros_firewall_enforce=true`. Backup play skips (no extra-var for the backup flag); all paths `changed=False`.
3. **Enforce with no drift, backup flag on.** Pass both flags. Backup play runs end-to-end, S3 object lands with `tier=pre-enforce`; all paths `changed=False`.
4. **Introduce a small drift.** Change one rule's comment in `firewall.yml`.
5. **Enforce.** Both flags on. Backup play runs; the one drifted rule updates on the device; final state matches model.
6. **Confirm.** Re-run `firewall-audit.yaml`. PLAY RECAP zero-drift.
7. **Revert the test drift.** Edit `firewall.yml` back, re-run enforce, re-run audit, confirm zero drift.
8. **Verify backup recoverability.** List the rustfs `routeros-backups` bucket filtered on `tier=pre-enforce` — at least three objects (one per enforce-with-backup run) should be visible.

**Acceptance:** all eight steps succeed; rb5009 ends in the same state it started.

## Self-review

### Spec coverage

- [x] Trigger (on-demand AAP) — Task 4
- [x] Mode toggle (boolean variable) — Task 1 + Task 2
- [x] Pre-write backup via chained `backup_s3.yaml` — Task 3
- [x] Partial-apply / rollback — covered in spec §5 + S3-staged backup
- [x] What stays the same (audit playbook structure, data model, role contract) — implicit

### Placeholder scan

No `TODO`s in the plan steps.

### Type / name consistency

- `routeros_firewall_enforce` (bool) — used in Tasks 1, 2, 3, 4.
- `routeros_firewall_pre_enforce_backup` (bool) — used in Tasks 3, 4.
- `routeros_s3_tier: pre-enforce` — distinct from `daily`/`weekly`/`monthly` so S3 lifecycle can handle pre-enforce snapshots differently.
