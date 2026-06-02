# Declarative RouterOS Firewall Management — Design (Phase 1: Audit-Only)

**Status:** approved (2026-06-02)
**Phase:** 1 of 2 (audit-only; Phase 2 = enforcement, separate spec)
**Scope:** `/ip/firewall/{filter,nat,mangle,raw,address-list}` + IPv6 equivalents on `rb5009.igou.systems`
**Target audience:** future-me reading this before writing the implementation plan, or before a Phase 2 enforcement spec.

## 1. Goal

Manage MikroTik RouterOS firewall configuration declaratively from YAML stored in `igou-inventory/`, with a clean upgrade path from drift-detection (this spec) to full enforcement (later spec) requiring no architectural change. Designed so the implementation can later be lifted into a `david_igou.routeros_configuration` Ansible collection without homelab-specific entanglements.

This phase deliberately **does not modify the device**. The deliverable is two playbooks:

1. **Drift report** — read live state, diff against the desired YAML, print a structured report.
2. **Export-to-YAML** — read live state, render it into the declarative model so the human doesn't transcribe ~dozens of hand-built rules manually.

## 2. Non-goals

- Modifying live firewall state. Phase 2 owns that.
- Modeling non-firewall RouterOS subsystems (VLANs, DHCP, interfaces, etc.). Out of scope; framework is designed so they can slot in later as additional roles.
- Solving the rb5009 self-signed cert problem. Documented as a known compromise; a separate ticket handles cert provisioning.
- Schema/lint validation independent of device state. Considered, deferred — can be added later if YAML mistakes prove a problem in practice.

## 3. Approach decisions (and what was rejected)

### 3.1 Diff/idempotence engine

**Decision:** Use `community.routeros.api_modify` with `check_mode: true` as the native drift engine.

**Why:** The module already computes adds / removes / per-field modifications against a desired list, knows about RouterOS defaults and computed fields, supports `handle_absent_entries: remove`, and emits structured `--diff` output. Phase 2 enforcement is one toggle (`check_mode: false`) — same data flow, same code path.

**Rejected alternatives:**

- **`/ip firewall filter export` + textual diff via `command` module.** Output is reordered/normalized by RouterOS, full of internal IDs; diffs are noisy. No clean write path. Dead end.
- **Custom action/filter plugin for diffing `api_info` output.** Considered initially. Rejected: `api_modify` is already the native diff engine and the module is domain-aware (knows defaults, ordering, field types). Writing our own would reinvent it less well.
- **Hybrid: `api_info` read + Jinja-rendered `.rsc` apply.** Reinvents `api_modify`. Heavier machinery, no benefit.

### 3.2 Transport

**Decision:** RouterOS API over TLS (`api-ssl`, port 8729).

**Why:** Structured I/O end-to-end. Required for `api_modify` / `api_info`. Existing `baseline.yml` already keeps `api-ssl` enabled (only plain `api` is disabled).

**Coexistence with existing playbooks:** `api_modify`/`api_info` are API modules, not connection-bound — they take `hostname`/`username`/`password`/`tls`/`port` as task parameters. Existing `network_cli` connection in `group_vars/routeros.yml` (used by `baseline.yml`, `backup.yml`, etc.) is untouched.

### 3.3 Migration strategy for the live device

**Decision:** Audit-only for Phase 1; enforcement deferred to Phase 2 (separate spec).

**Why:** rb5009 carries a mix of hand-built and previously-automated rules. Direct big-bang adoption is too risky. Phase 1 produces drift reports the operator reviews; Phase 2 flips ownership on after the YAML is trusted.

**Rejected:** Comment-prefix-scoped ownership (role only manages `ansible:`-prefixed rules, ignores others). Considered as an alternative; not chosen because audit-only achieves higher confidence and Phase 2 can still adopt the prefix-scoping technique if desired.

### 3.4 Source-of-truth location

**Decision:** Per-host directory under `igou-inventory/host_vars/<inventory_hostname>/`, split per domain.

**Why:** Inventory-adjacent (same repo as the rest of the source of truth), already-supported Ansible pattern, extends cleanly to other RouterOS hosts (switches with their own filters) and other domains (`vlans.yml`, `dhcp.yml`).

### 3.5 Role vs. playbook-native

**Decision:** Role at `roles/routeros_firewall/`.

**Why:** User intends to potentially extract a `david_igou.routeros_configuration` collection later. Designing as a role from day 1 makes that cut trivial (collection's `roles/` dir is exactly what gets extracted). The role is lab-agnostic — see Section 5.

**Trade-off vs. existing convention:** Existing RouterOS playbooks (`baseline.yml`, `backup.yml`, `manage_users.yml`) are playbook-native. The departure here is deliberate and motivated by the future-collection path; should be documented for future maintainers.

## 4. Repository layout (proposed)

```
ansible.cfg                          # committed (removed from .gitignore); pins paths
.gitignore                           # remove the `ansible.cfg` entry

roles/routeros_firewall/             # NEW — first committed role under ./roles
  defaults/main.yml                  # role-level defaults + required-input declarations
  meta/main.yml                      # min_ansible_version; no deps
  tasks/main.yml                     # audit entrypoint (default)
  tasks/export.yml                   # export-to-YAML entrypoint
  tasks/_assert_inputs.yml           # shared input-validation include

playbooks/routeros/
  firewall-audit.yaml                # import_role with default tasks_from
  firewall-export.yaml               # import_role with tasks_from: export

igou-inventory/
  group_vars/routeros.yml            # extend: API creds via 1Password lookup, validate_certs override
  host_vars/rb5009.igou.systems/     # NEW directory (multi-file host_vars)
    firewall.yml                     # generated by export, hand-curated after
  group_vars/aap/job_templates.yml   # extend: add routeros_firewall_{audit,export}
```

### `ansible.cfg` (committed)

```ini
[defaults]
remote_user = igou
roles_path = ./roles:.ansible/roles:~/.ansible/roles
collections_path = .ansible/collections:~/.ansible/collections
callbacks_enabled = profile_tasks
```

`./roles` first → committed role code wins lookups. `.ansible/roles` second → galaxy installs land there. `.ansible/` stays gitignored. CI / Makefile invocations of `ansible-galaxy {role,collection} install` should pass `-p .ansible/roles` / `-p .ansible/collections` explicitly so install target is unambiguous on fresh checkouts.

## 5. Role contract (lab-agnostic)

The role declares the variables it needs; the consumer (this lab, or any future consumer) supplies them however they like. No 1Password lookups, no homelab hostnames, no opinions about secret stores inside the role.

```yaml
# roles/routeros_firewall/defaults/main.yml
---
# --- Connection (caller supplies values; role does no secret resolution) ---
routeros_firewall_api_host: "{{ ansible_host | default(inventory_hostname) }}"
routeros_firewall_api_port: 8729
routeros_firewall_api_tls: true
routeros_firewall_api_validate_certs: true
routeros_firewall_api_username: ~      # REQUIRED — assert non-null in pre-task
routeros_firewall_api_password: ~      # REQUIRED — assert non-null in pre-task

# --- Behavior ---
routeros_firewall_paths:
  - filter
  - nat
  - mangle
  - raw
  - address-list
routeros_firewall_ipv6_paths: []

routeros_firewall_comment_prefix: "ansible:"

# --- Export target ---
routeros_firewall_export_path: "{{ inventory_dir }}/host_vars/{{ inventory_hostname }}/firewall.yml"

# --- Desired state (consumer supplies per host) ---
routeros_firewall: {}
```

`tasks/_assert_inputs.yml` fails the play with a clear message if `routeros_firewall_api_username` or `_password` is null. Audit entrypoint also asserts every rule's `comment` starts with `{{ routeros_firewall_comment_prefix }}` and that comments are unique within each path.

### Lab-specific glue (lives in `igou-inventory/`, NOT in the role)

```yaml
# igou-inventory/group_vars/routeros.yml — additive
routeros_firewall_api_username: >-
  {{ lookup('community.general.onepassword',
            'rb5009-api', field='username', vault='awx') }}
routeros_firewall_api_password: >-
  {{ lookup('community.general.onepassword',
            'rb5009-api', field='password', vault='awx') }}

# rb5009 currently has self-signed certs; flip back to true after cert work lands.
routeros_firewall_api_validate_certs: false
```

## 6. Data model

YAML structure mirrors `community.routeros.api_modify` input directly — field names match the RouterOS API (`in-interface-list`, not `in_interface_list`). Whatever `api_info` exports drops straight back into this model unchanged.

```yaml
# igou-inventory/host_vars/rb5009.igou.systems/firewall.yml
---
routeros_firewall:
  filter:
    # Order = apply order. api_modify preserves list position.
    - chain: input
      action: accept
      connection-state: established,related
      comment: "ansible: accept established/related"
    - chain: input
      action: drop
      in-interface-list: WAN
      comment: "ansible: drop WAN input"

  nat:
    - chain: srcnat
      action: masquerade
      out-interface-list: WAN
      comment: "ansible: masquerade outbound"

  address-list:
    # Unordered. Identity = (list, address) composite; api_modify handles natively.
    - list: bogons
      address: 0.0.0.0/8
      comment: "ansible: bogon 0/8"

  mangle: []
  raw: []
```

### Conventions

- **Field names verbatim** from RouterOS API. No name translation.
- **`comment` carries `ansible:` prefix** for every managed rule. Pre-task assertion enforces this. Phase 2 may use the prefix to scope ownership; Phase 1 just enforces the discipline now while it's cheap.
- **Order is meaningful** for `filter`, `nat`, `mangle`, `raw`. Author top-down in apply order.
- **Empty list `[]`** = "this sub-path is managed and should be empty".
- **Omitted key** = "this sub-path is not managed". Important distinction for Phase 2.
- **IPv6 paths included in the model** even though empty by default — costs nothing, saves a schema change when v6 rules are added.

## 7. Components & data flow

### `playbooks/routeros/firewall-export.yaml` — one-shot bootstrap

1. Load credentials (inventory provides via 1Password lookup; role doesn't know).
2. Assert required inputs are non-null.
3. For each path in `routeros_firewall_paths + routeros_firewall_ipv6_paths`: call `community.routeros.api_info` → list of dicts.
4. Strip noisy / device-only fields: `.id`, `dynamic`, `invalid`, `bytes`, `packets`. (Use `api_info`'s `hide_defaults: true` and `unfiltered: false` to suppress most of these natively.)
5. Auto-prefix comments: any rule whose `comment` doesn't start with `{{ routeros_firewall_comment_prefix }}` gets the prefix prepended. Human reviews these in the YAML diff before committing.
6. Render the captured state to `{{ routeros_firewall_export_path }}` via `ansible.builtin.copy: content: "{{ data | to_nice_yaml(indent=2, sort_keys=false) }}"`, `delegate_to: localhost`.
7. Print summary: rule counts per path, count of auto-prefixed comments.

### `playbooks/routeros/firewall-audit.yaml` — drift report

1. Load credentials + desired state (`routeros_firewall` from host_vars).
2. Assert required inputs non-null.
3. Assert every desired rule's `comment` starts with `{{ routeros_firewall_comment_prefix }}`. Fail with full list of offenders on failure.
4. Assert comment uniqueness per path. Fail with conflicts listed.
5. For each managed path: `community.routeros.api_modify` with `check_mode: true`, `handle_absent_entries: remove`, passing `routeros_firewall[<path>]`.
6. Run the play with `ansible-playbook --check --diff` (or set `diff: true` on the tasks). The `--diff` flag is required — without it `api_modify` reports `changed: true/false` but does not emit the per-field before/after structure the audit report relies on.
7. Final task aggregates `result.diff` across paths, renders summary to stdout: counts of added/removed/modified per path, then the structured diff itself.

### Data flow diagram

```
        ┌──────────────────┐         ┌──────────────────────────────────┐
        │ 1Password        │         │ host_vars/<host>/firewall.yml   │
        │ (rb5009-api)     │         │ (desired state)                  │
        └────────┬─────────┘         └────────────────┬─────────────────┘
                 │                                    │
       inventory │ lookup                             │ vars (only for audit)
                 ▼                                    ▼
        ┌────────────────────────────────────────────────────────┐
        │ roles/routeros_firewall                                 │
        │   tasks/main.yml     → api_modify (check_mode=true)    │
        │   tasks/export.yml   → api_info                         │
        └────────────────────┬─────────────────────┬──────────────┘
                             │                     │
                             ▼                     ▼
                  rb5009 RouterOS API     stdout diff (audit)
                  (api-ssl, port 8729)    OR YAML write (export)
```

## 8. Error handling

| Failure mode | Behavior |
|---|---|
| API unreachable | `api_modify` fails the task / play. Read-only, no cleanup needed. |
| Wrong credentials | `api_modify` fails with auth error. Operator checks 1Password / `op service-account ratelimit` for awx-vault rate-limit issues (see `reference_1p_service_account_ratelimit.md`). |
| Cert validation fail | Default `validate_certs: true` in role; lab overrides to `false` until real cert lands. Documented compromise. |
| Schema mistake in YAML | Pre-task assertions catch missing fields, unprefixed comments, duplicate comments — fail before any API call. |
| `api_modify` rejects an attribute | Task fails with the offending field named. Likely cause: RouterOS 7.x added a field the collection doesn't yet model. Mitigation: drop the field from YAML; document in spec. |
| Missing `community.routeros` collection | EE already bundles it (verified in `execution-environments/igou-aap-ee-rhel9/execution-environment.yml`). Local dev requires `ansible-galaxy collection install -r requirements.yml`. |

## 9. Testing & CI

### Local / pre-merge

- `pre-commit run --all-files` (already runs ansible-lint + yamllint).
- `ansible-playbook playbooks/routeros/firewall-audit.yaml --syntax-check`.
- `ansible-playbook playbooks/routeros/firewall-export.yaml --syntax-check`.

### CI (GitHub Actions)

- Extend existing `syntax-check.yml` workflow to syntax-check the new playbooks. No collection install changes needed (already installs `community.routeros` 3.20.0).
- Optional new step: assertion-only dry-run against a fixture host_vars file (no network). Validates that the role's input assertions correctly catch malformed YAML. Worth doing if YAML mistakes start landing in PRs.

### Deferred to Phase 2

- Molecule scenario with a CHR (Cloud Hosted Router) container target: apply known-good YAML, re-run audit, assert no drift. Real integration test against the real RouterOS binary. Not in this phase because Phase 1 doesn't apply anything.

### AAP

Add to `igou-inventory/group_vars/aap/job_templates.yml`:

- `routeros_firewall_audit` — schedulable nightly; failure = drift detected; surfaces hand-edits.
- `routeros_firewall_export` — manual trigger; for bootstrap and on-demand snapshot refresh.

Both use the existing `igou-aap-ee-rhel9` EE (community.routeros already bundled).

## 10. Operator workflow

1. **Bootstrap.** Operator runs `firewall-export` against rb5009 → writes `igou-inventory/host_vars/rb5009.igou.systems/firewall.yml`.
2. **Review & curate.** Operator reads the generated YAML, refines `ansible:` prefix on auto-prefixed comments (give rules meaningful names), removes anything ephemeral (test rules, debugging artifacts), commits to `igou-inventory`.
3. **Verify.** Operator runs `firewall-audit` — expects zero drift if step 2 was honest.
4. **Steady state.** Nightly AAP `routeros_firewall_audit` runs; any drift = hand-edits made directly on the device, surfaces as job failure.
5. **Adopt a hand-edit.** Re-run `firewall-export` to a scratch path, diff against the committed YAML, hand-merge intentional changes back into committed YAML.
6. **Phase 2 (future spec).** Once trust is established and drift is consistently zero, flip `check_mode: true` → `false` in a separate playbook entrypoint to enforce.

## 11. Future-collection extraction sketch

When the role moves to `david_igou.routeros_configuration`:

- `roles/routeros_firewall/` → `collections/ansible_collections/david_igou/routeros_configuration/roles/routeros_firewall/`. Internal layout unchanged.
- Playbooks `playbooks/routeros/firewall-{audit,export}.yaml` → live in `extensions/eda/` or stay in this repo as consumer-side glue. Either works.
- Lab-specific glue in `igou-inventory/group_vars/routeros.yml` stays in this repo — that's the integration seam.
- No code changes required; the role is already lab-agnostic.

## 12. Open issues / follow-ups (out of scope for this spec)

- **rb5009 self-signed cert.** Tracked separately; until then `routeros_firewall_api_validate_certs: false` in lab inventory.
- **1Password item `rb5009-api`** needs to be created in the `awx` vault before any of this works. Pre-implementation prerequisite.
- **API service enablement** on rb5009. `api-ssl` should already be on per `baseline.yml`; verify before first run.
- **Phase 2 enforcement spec** — separate doc; will cover backup-before-apply, rollback, partial-apply behavior, the molecule scenario.
- **Switches (`crs310`, `crs317`, `crs328`)** — out of Phase 1 scope. May get their own host_vars/<host>/firewall.yml later; same role.
