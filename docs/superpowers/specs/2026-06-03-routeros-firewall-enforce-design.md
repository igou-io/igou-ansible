# RouterOS firewall enforcement (Phase 2) — design

**Date:** 2026-06-03
**Phase:** 2 of 2 (enforcement; Phase 1 = audit-only, landed in PR 207)
**Target audience:** future-me reading this before writing the implementation plan, or a third phase if one ever exists.

This is a follow-on to `2026-06-02-routeros-declarative-firewall-design.md`. Read that first. This spec only covers what changes between Phase 1 and Phase 2; it does not re-derive the data model, role contract, or component layout.

## 1. Goal

Flip the existing `routeros_firewall` role from audit-only to enforcing: when invoked in enforce mode, the role brings the live device to match the desired YAML, including additions, modifications, removals, and re-ordering — bracketed by a pre-write backup snapshot so a botched apply is always recoverable.

## 2. Non-goals

- Changing the data model. Same `host_vars/<host>/firewall.yml` shape Phase 1 produces.
- Changing the audit entrypoint. Phase 1's audit playbook keeps working unchanged; "enforce" is an additional mode, not a replacement.
- Scheduled / on-merge / GitOps-style automatic enforcement. Phase 2 ships on-demand only; if scheduled enforce is wanted later, it's a Phase 3 decision (with its own change-window / alerting concerns).
- A CHR-based molecule scenario. Worth doing eventually but not a blocker — Phase 2 is validated against the real rb5009.
- Partial-path management ("manage only `ansible:`-prefixed rules"). Same as Phase 1: the role owns each declared sub-path completely.

## 3. Approach decisions

### 3.1 Trigger: on-demand only

**Decision:** Enforce runs only when an operator invokes the new AAP `routeros_firewall_enforce` job template. Audit keeps running on its existing cadence.

**Why:** The trust escalator is audit-on-schedule → operator reviews drift → operator decides to apply. Auto-enforce-on-merge couples "the YAML now reflects what we want" to "the router now matches the YAML" too tightly — a regression in CI gates would push bad firewall state directly to prod. Scheduled nightly enforce reverts emergency hand-edits made during an incident with no operator in the loop. On-demand defers those risks until the operator has explicit input.

**Rejected:** GitHub-Action-on-merge (too tightly coupled; requires audit-as-CI-gate to be bulletproof first), scheduled enforce (reverts emergency changes).

### 3.2 Mode toggle: single boolean variable

**Decision:** Add `routeros_firewall_enforce: false` to `defaults/main.yml`. The role's existing `api_modify` tasks derive `check_mode` from `not routeros_firewall_enforce`. The AAP `_enforce` template passes `-e routeros_firewall_enforce=true`. No new `tasks_from` file, no parallel code paths.

**Why:** Minimum surface area. The audit and enforce flows are otherwise identical — same path traversal, same `api_modify` calls, same diff semantics. The only difference is whether `api_modify` simulates or commits. A boolean toggle expresses that without a code fork. Default `false` means a misconfigured playbook fails safe (audit, not enforce).

**Rejected:** Separate `tasks/enforce.yml` entrypoint (more code paths to keep in sync; encourages drift between audit and enforce); "always enforce + check_mode at playbook layer" (too easy to forget the check_mode in a new playbook and accidentally write to prod).

### 3.3 Pre-write safety: backup snapshot

**Decision:** When `routeros_firewall_enforce=true`, the role's first task (before any `api_modify`) is to drop a backup to the device's flash via `community.routeros.command` running `/system backup save name=...` and `/export compact file=...`. Filenames include the playbook run timestamp.

**Why:** Cheap insurance against a botched apply. RouterOS doesn't expose a transactional firewall edit — `api_modify`'s per-row updates can succeed, fail, or partially apply if the connection drops mid-run. With a backup on flash, recovery is `/system backup load name=<file>` from console or netinstall; without one, the operator is reconstructing the prior state by hand. The existing scheduled `routeros_backup_dir` workflow grabs backups off-device on a cadence (`routeros_backup_retain: 30` per host per file type), which is great for off-box archival but the most recent scheduled backup may be hours stale. The pre-enforce backup guarantees the snapshot was taken seconds before the changes that broke things.

The exported `.rsc` file is captured alongside the `.backup` because RouterOS backups are version-pinned (you can't restore a 7.x backup onto an 8.x device, etc.) while `.rsc` is portable plaintext — belt and suspenders.

**Rejected:** Relying on the existing scheduled backups (staleness risk); fetching backups off-device as part of enforce (slow; not necessary — on-device is enough for rollback).

## 4. What changes

### Role (`roles/routeros_firewall/`)

- `defaults/main.yml`: add `routeros_firewall_enforce: false`.
- `tasks/main.yml`: change `check_mode: true` (hardcoded) → `check_mode: "{{ not routeros_firewall_enforce | bool }}"` on both the IPv4 and IPv6 `api_modify` loops.
- New file `tasks/_backup_before_enforce.yml`: gated on `routeros_firewall_enforce | bool`, runs the backup + export commands and logs the filenames. Imported from `main.yml` after input assertions, before any `api_modify`.
- The existing `tasks/_assert_inputs.yml` gets one additional check when `routeros_firewall_enforce=true`: confirm the API user has the `write` policy. Cheaper to fail fast on a permission error in a pre-task than to fail mid-enforce after the backup has already been taken.

No changes to `tasks/export.yml`, `templates/firewall.yml.j2`, or `defaults/main.yml`'s other variables.

### Audit playbook (`playbooks/routeros/firewall-audit.yaml`)

No code change. The same playbook serves both audit and enforce — what differs is the variable passed in. Doc string in the file gets a one-line note saying so.

### AAP (lives in `igou-inventory`, not this repo)

Add one job template:

- `routeros_firewall_enforce` — manual trigger only (no schedule). Uses the same `firewall-audit.yaml` playbook and the `igou-aap-ee-rhel9` EE. Passes `routeros_firewall_enforce=true` as an extra-var (configured as a surveyed prompt, so the operator confirms-by-typing at trigger time). Optionally: gated behind an AAP approval node so a second operator has to click before the job actually runs.

### Inventory (`igou-inventory/group_vars/routeros.yml`)

No change. The API user already has write (done as part of the Phase 1 onboarding); the comment was updated in `d0200dd`.

## 5. Failure modes

### Connection drops mid-enforce

`api_modify` processes each path independently and commits changes incrementally inside that path. If the API connection drops between paths, earlier paths are committed, later paths aren't. If it drops mid-path, the per-row updates that already landed stay applied. There is no rollback automation in this design — the operator restores from the pre-enforce backup. The audit job re-run after recovery surfaces what was applied.

### Permission error mid-enforce

The pre-enforce permission check should catch this. If it doesn't (e.g. the user has write on `/ip firewall` but not `/ip firewall connection tracking`), the partial-apply story applies — restore from backup, fix permissions, re-run.

### Operator pushes a bad rule

Audit was supposed to catch this before enforce ran. If it didn't (operator skipped the audit step, or the rule is syntactically valid but semantically wrong like "drop all input"), the backup is the recovery mechanism. This is operator error, not role error.

### Backup itself fails

Enforce aborts. Hard fail before any `api_modify`. The role refuses to write without a successful backup.

## 6. Operator workflow

1. Operator updates `host_vars/<host>/firewall.yml` in `igou-inventory`, opens a PR.
2. CI lints (existing). PR merges to `main`.
3. Nightly scheduled `routeros_firewall_audit` runs — drift surfaces as job failure with the diff in the AAP run log.
4. Operator reads the diff, decides "yes, apply this," triggers `routeros_firewall_enforce` in AAP.
5. Role takes the backup, applies the changes, runs a final audit pass (or operator triggers the audit template manually right after) to confirm zero drift.
6. If anything goes sideways, operator restores from the on-device backup via console or `/system backup load`.

## 7. Out of scope / deferred

- **CHR molecule scenario.** Mentioned in Phase 1 section 9 as "deferred to Phase 2." Still deferred. The Phase 2 PR validates against real rb5009; a CHR-based integration suite is its own ticket and doesn't gate enforcement.
- **Automatic post-enforce audit pass.** The role could re-call audit after enforce to confirm zero drift, but that's a quality-of-life add — out of scope here; operator manually re-runs the audit job.
- **Rollback automation.** No `--rollback` flag. Operator restores from the backup file the role produced. The backup filename is logged so it's easy to find.
- **Per-rule-level apply ("just this one rule").** Whole-path semantics only. If you want surgical edits, do them by hand and re-export.
- **Switches (`crs310`, `crs317`, `crs328`).** Same as Phase 1 — not in scope until each gets a `host_vars/<host>/firewall.yml`.
- **Phase 3.** If scheduled enforce becomes desirable later, it would need: a change-window concept, drift-categorization (e.g. only auto-apply additions, never deletions), better alerting. Not designed here.
