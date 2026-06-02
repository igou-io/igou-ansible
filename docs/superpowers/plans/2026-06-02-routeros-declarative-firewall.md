# RouterOS Declarative Firewall — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 1 (audit-only) of the declarative RouterOS firewall design — a lab-agnostic `roles/routeros_firewall/` role plus two playbooks (`firewall-audit.yaml`, `firewall-export.yaml`) that read live state via `community.routeros.api_info` / `api_modify --check`, with inventory-side glue resolving credentials via 1Password.

**Architecture:** A self-contained Ansible role consuming caller-supplied API credentials. Audit entrypoint runs `community.routeros.api_modify` in `check_mode: true` against `routeros_firewall[<path>]` desired state, emitting a structured `--diff` report. Export entrypoint runs `community.routeros.api_info` and renders results to `host_vars/<host>/firewall.yml`. Lab-specific 1Password lookup lives in `igou-inventory/group_vars/routeros.yml`, never in the role.

**Tech Stack:** Ansible (community.routeros 3.20.0, ansible-core, ansible-navigator), 1Password CLI / community.general.onepassword lookup, RouterOS API over TLS (port 8729), AAP RHEL9 EE.

**Spec reference:** `docs/superpowers/specs/2026-06-02-routeros-declarative-firewall-design.md`

---

## Pre-implementation: repo context

**This repo (`igou-ansible`) is one git repo.** The symlinked `igou-inventory/` directory is a separate git repo (gitignored here). Tasks that touch `igou-inventory/...` must be committed inside that repo, not this one. Each task notes which repo to commit in.

**Operator-side prerequisites** (NOT in the implementation plan — these are environment setup the user does once, before Task 8 smoke test):

1. Create 1Password item `rb5009-api` in the `awx` vault with `username` and `password` fields. The RouterOS user needs `read,api,policy=read` group permissions for audit-only; Phase 2 will require `write` too.
2. Verify `api-ssl` is enabled on rb5009 (`/ip service print`). The existing `baseline.yml` keeps it enabled by default, but a manual confirmation is worth one minute before the smoke test.

These are documented as Section 12 follow-ups in the spec. The plan assumes they are done by the time Task 8 runs.

---

## File structure

**Created in `igou-ansible`:**
- `ansible.cfg` — moved from gitignored to committed; explicit `roles_path` / `collections_path`.
- `roles/routeros_firewall/meta/main.yml` — minimum Ansible version, no deps.
- `roles/routeros_firewall/defaults/main.yml` — every consumer-supplied variable, with defaults or `~` placeholders.
- `roles/routeros_firewall/tasks/_assert_inputs.yml` — shared input validation.
- `roles/routeros_firewall/tasks/main.yml` — audit entrypoint (default).
- `roles/routeros_firewall/tasks/export.yml` — export-to-YAML entrypoint.
- `playbooks/routeros/firewall-audit.yaml` — calls role with default `tasks_from`.
- `playbooks/routeros/firewall-export.yaml` — calls role with `tasks_from: export`.

**Modified in `igou-ansible`:**
- `.gitignore` — remove the `ansible.cfg` line.

**Modified in `igou-inventory` (separate repo):**
- `group_vars/routeros.yml` — add 1Password-backed API credential vars and `validate_certs: false` override.
- `group_vars/aap/job_templates.yml` — append `routeros_firewall_audit` and `routeros_firewall_export` entries.

**Created in `igou-inventory` (separate repo, by operator running export at Task 8):**
- `host_vars/rb5009.igou.systems/firewall.yml` — generated, then hand-curated.

---

## Task 1: Repo plumbing — commit ansible.cfg with explicit paths

**Why:** The current `ansible.cfg` is gitignored, so fresh clones lack the roles/collections paths the rest of the work depends on. Get it under version control with explicit paths before adding anything that depends on the role lookup.

**Files:**
- Modify: `/workspace/igou-ansible/ansible.cfg`
- Modify: `/workspace/igou-ansible/.gitignore`

- [ ] **Step 1: Inspect current state**

Run: `cat /workspace/igou-ansible/ansible.cfg && echo --- && grep -n "ansible.cfg" /workspace/igou-ansible/.gitignore`

Expected: shows current `[defaults]` section and the line number where `ansible.cfg` appears in `.gitignore`.

- [ ] **Step 2: Rewrite `ansible.cfg` with explicit paths**

Replace the entire file with:

```ini
[defaults]
remote_user = igou
roles_path = ./roles:.ansible/roles:~/.ansible/roles
collections_path = .ansible/collections:~/.ansible/collections
callbacks_enabled = profile_tasks
```

Notes:
- `./roles` first → committed role code wins lookups.
- `.ansible/roles` second → galaxy installs land in the gitignored dir.
- `collections_path` is new; previously defaulted to `~/.ansible/collections` only.

- [ ] **Step 3: Remove `ansible.cfg` from `.gitignore`**

Edit `/workspace/igou-ansible/.gitignore` — delete the line containing exactly `ansible.cfg` (do NOT delete the `.ansible/` line; that's a different entry covering the install target dir).

- [ ] **Step 4: Verify ansible-config parses it cleanly**

Run: `cd /workspace/igou-ansible && ansible-config dump --only-changed | grep -E "DEFAULT_ROLES_PATH|COLLECTIONS_PATHS"`

Expected: shows both paths exactly as configured. If `ansible-config` errors out, the INI syntax is wrong — re-read the file.

- [ ] **Step 5: Commit (in `igou-ansible`)**

```bash
cd /workspace/igou-ansible
git add ansible.cfg .gitignore
git commit -m "$(cat <<'EOF'
chore(ansible): commit ansible.cfg with explicit roles/collections paths

Move ansible.cfg out of .gitignore so fresh clones inherit roles_path
and collections_path. Pin ./roles first (committed code wins lookups)
and .ansible/roles / .ansible/collections second (galaxy install target,
gitignored).

Preparation for adding the first committed role under ./roles/.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Role skeleton and metadata

**Why:** Establish the role directory with `meta/main.yml` so Ansible recognizes it before any task content lands. Independent commit makes review and rollback easier.

**Files:**
- Create: `/workspace/igou-ansible/roles/routeros_firewall/meta/main.yml`

- [ ] **Step 1: Create the role meta file**

Write `/workspace/igou-ansible/roles/routeros_firewall/meta/main.yml`:

```yaml
---
galaxy_info:
  role_name: routeros_firewall
  author: David Igou
  description: >-
    Declarative management of MikroTik RouterOS firewall sub-paths
    (filter, nat, mangle, raw, address-list) via community.routeros
    api_modify / api_info. Phase 1 supports audit + export only;
    Phase 2 enables enforcement.
  license: MIT
  min_ansible_version: "2.16"
  platforms:
    - name: GenericLinux
      versions:
        - all
  galaxy_tags:
    - networking
    - mikrotik
    - routeros
    - firewall

dependencies: []
```

Notes:
- `platforms: GenericLinux` is correct here — the role runs on a controller, not on the target device. RouterOS isn't a "platform" Galaxy recognizes.
- `dependencies: []` is intentional; the role doesn't pull other roles in.

- [ ] **Step 2: Verify the role is discoverable**

Run: `cd /workspace/igou-ansible && ansible-galaxy role list 2>&1 | grep -E "routeros_firewall|^# /workspace"`

Expected: lists `routeros_firewall` under the `./roles` search path. If it's missing, `roles_path` from Task 1 isn't being read — re-check `ansible.cfg`.

- [ ] **Step 3: Commit (in `igou-ansible`)**

```bash
cd /workspace/igou-ansible
git add roles/routeros_firewall/meta/main.yml
git commit -m "$(cat <<'EOF'
feat(routeros_firewall): scaffold role with meta

First committed role under ./roles/. Metadata only; tasks land in
follow-up commits. Min Ansible 2.16 covers ansible-core present in
the AAP RHEL9 EE.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Role defaults — declare the consumer contract

**Why:** Lock in every variable the role consumes. Defaults are sensible-but-strict (`validate_certs: true`); secrets are `~` to force the consumer to supply them and trigger a clear failure in Task 4's assertions if they don't.

**Files:**
- Create: `/workspace/igou-ansible/roles/routeros_firewall/defaults/main.yml`

- [ ] **Step 1: Write defaults file**

```yaml
---
# routeros_firewall — Phase 1 (audit + export). Consumer contract.
#
# Every variable below is consumer-tunable. Secrets are unset (~) and
# MUST be supplied by the inventory (group_vars / host_vars) or via
# extra_vars. tasks/_assert_inputs.yml fails the play with a clear
# message if a required secret is missing.

# --- Connection ---
# Default api_host = ansible_host (or inventory_hostname if unset). This
# avoids the consumer having to repeat the address.
routeros_firewall_api_host: "{{ ansible_host | default(inventory_hostname) }}"
routeros_firewall_api_port: 8729
routeros_firewall_api_tls: true
routeros_firewall_api_validate_certs: true
routeros_firewall_api_username: ~
routeros_firewall_api_password: ~

# --- Behavior ---
# RouterOS /ip/firewall sub-paths the role will audit/export. Drop entries
# to scope down; add to routeros_firewall_ipv6_paths to manage IPv6.
# NOTE: community.routeros uses SPACE-separated path syntax internally
# (e.g. 'ip firewall filter'). These bare names get composed into that
# form inside tasks/main.yml and tasks/export.yml.
routeros_firewall_paths:
  - filter
  - nat
  - mangle
  - raw
  - address-list

routeros_firewall_ipv6_paths: []

# Per-path order-sensitivity. filter/nat/mangle/raw are order-sensitive
# (drop rules at the bottom, etc.); address-list is set-semantic. Maps
# directly to api_modify's `ensure_order` parameter, which requires
# handle_absent_entries=remove.
routeros_firewall_ordered:
  filter: true
  nat: true
  mangle: true
  raw: true
  address-list: false

# Every managed rule's `comment` must start with this prefix. The audit
# entrypoint asserts. The export entrypoint auto-prepends if missing.
routeros_firewall_comment_prefix: "ansible:"

# --- Export target ---
# Path where firewall-export.yaml writes the captured state. Default puts
# it next to the inventory it came from; override for non-standard layouts.
routeros_firewall_export_path: "{{ inventory_dir }}/host_vars/{{ inventory_hostname }}/firewall.yml"

# --- Desired state (caller supplies per host) ---
# Shape: { filter: [...], nat: [...], mangle: [...], raw: [...],
#         address-list: [...], ipv6-filter: [...], ... }
# See docs/superpowers/specs/2026-06-02-routeros-declarative-firewall-design.md §6.
routeros_firewall: {}
```

- [ ] **Step 2: Verify YAML is well-formed**

Run: `cd /workspace/igou-ansible && ansible-playbook --syntax-check -i localhost, -c local --extra-vars '@roles/routeros_firewall/defaults/main.yml' /dev/stdin <<'EOF'
---
- hosts: localhost
  gather_facts: false
  tasks:
    - debug: var=routeros_firewall_paths
EOF`

Expected: prints `playbook: /dev/stdin` and no parse errors. If yamllint catches it first, that's fine — just confirms structure.

- [ ] **Step 3: Commit (in `igou-ansible`)**

```bash
cd /workspace/igou-ansible
git add roles/routeros_firewall/defaults/main.yml
git commit -m "$(cat <<'EOF'
feat(routeros_firewall): declare consumer contract in defaults

Defaults file enumerates every variable the role consumes. Secrets are
left unset (~) so missing-input fails loudly via assertions (next
commit). Default behavior is strict (validate_certs=true); lab
overrides flip to false until a real cert lands on rb5009.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Input-assertion include

**Why:** A single shared include that both entrypoints run before doing anything device-side. Fails the play with clear messages when the consumer forgot to supply credentials, or when the desired-state YAML violates the comment-prefix or uniqueness rules.

**Files:**
- Create: `/workspace/igou-ansible/roles/routeros_firewall/tasks/_assert_inputs.yml`

- [ ] **Step 1: Write the assertion include**

```yaml
---
# Shared input validation for routeros_firewall. Imported by both
# tasks/main.yml (audit) and tasks/export.yml. Fails the play with
# actionable messages when the consumer's inputs are incomplete or
# the desired-state YAML violates role invariants.
#
# Variables read:
#   routeros_firewall_api_username       (required, non-null)
#   routeros_firewall_api_password       (required, non-null)
#   routeros_firewall                    (dict; per-path rule lists)
#   routeros_firewall_comment_prefix     (string)
#   _assert_check_desired                (bool; set true by audit, false by export)

- name: Assert API credentials are supplied
  ansible.builtin.assert:
    that:
      - routeros_firewall_api_username is not none
      - routeros_firewall_api_username | length > 0
      - routeros_firewall_api_password is not none
      - routeros_firewall_api_password | length > 0
    fail_msg: >-
      routeros_firewall_api_username and routeros_firewall_api_password
      must be supplied (typically via inventory group_vars). Got
      username='{{ routeros_firewall_api_username | default("<unset>") }}',
      password='{{ "<set>" if routeros_firewall_api_password else "<unset>" }}'.
    quiet: true

- name: Assert desired state YAML is well-formed (audit only)
  when: _assert_check_desired | default(false) | bool
  block:
    - name: Collect every managed rule with its source path
      ansible.builtin.set_fact:
        _routeros_firewall_flat: >-
          {{
            (routeros_firewall_paths + routeros_firewall_ipv6_paths)
            | map('extract', routeros_firewall, default=[])
            | zip(routeros_firewall_paths + routeros_firewall_ipv6_paths)
            | map('reverse')
            | list
          }}
      # Result: [[path1, [rule, rule, ...]], [path2, [...]], ...]

    - name: Find rules whose comment lacks the configured prefix
      ansible.builtin.set_fact:
        _routeros_firewall_unprefixed: >-
          {{
            _routeros_firewall_flat
            | subelements(1)
            | rejectattr('1.comment', 'defined')
            | list
            +
            _routeros_firewall_flat
            | subelements(1)
            | selectattr('1.comment', 'defined')
            | rejectattr('1.comment', 'match', '^' ~ routeros_firewall_comment_prefix)
            | list
          }}

    - name: Fail if any managed rule lacks the prefix
      ansible.builtin.assert:
        that:
          - _routeros_firewall_unprefixed | length == 0
        fail_msg: >-
          Every managed rule's `comment` must start with
          '{{ routeros_firewall_comment_prefix }}'. Offenders:
          {{ _routeros_firewall_unprefixed
             | map(attribute='1.comment', default='<missing comment>')
             | list }}
        quiet: true

    - name: Find duplicate comments within any single path
      ansible.builtin.set_fact:
        _routeros_firewall_duplicates: >-
          {{
            _routeros_firewall_flat
            | map('last')
            | map('map', attribute='comment')
            | map('community.general.counter')
            | map('dict2items')
            | map('selectattr', 'value', 'gt', 1)
            | map('map', attribute='key')
            | map('list')
            | list
          }}

    - name: Fail if duplicate comments exist within a path
      ansible.builtin.assert:
        that:
          - _routeros_firewall_duplicates | flatten | length == 0
        fail_msg: >-
          Comments must be unique within each managed path. Duplicates:
          {{ _routeros_firewall_duplicates }}
        quiet: true
```

Note: `community.general.counter` is the right filter for this counting check; it's already in the EE.

- [ ] **Step 2: Smoke-test the assertion include with a passing fixture**

Create a one-off test playbook at `/tmp/test_assert_pass.yaml`:

```yaml
---
- hosts: localhost
  gather_facts: false
  vars:
    routeros_firewall_api_username: "test-user"
    routeros_firewall_api_password: "test-pass"
    routeros_firewall_paths: [filter]
    routeros_firewall_ipv6_paths: []
    routeros_firewall_comment_prefix: "ansible:"
    routeros_firewall:
      filter:
        - chain: input
          action: drop
          comment: "ansible: rule 1"
        - chain: input
          action: accept
          comment: "ansible: rule 2"
  tasks:
    - ansible.builtin.import_role:
        name: routeros_firewall
        tasks_from: _assert_inputs.yml
      vars:
        _assert_check_desired: true
```

Run: `cd /workspace/igou-ansible && ansible-playbook /tmp/test_assert_pass.yaml`

Expected: all assertion tasks pass (`changed=0 failed=0`).

- [ ] **Step 3: Smoke-test with a failing fixture (unprefixed comment)**

Create `/tmp/test_assert_fail_prefix.yaml`:

```yaml
---
- hosts: localhost
  gather_facts: false
  vars:
    routeros_firewall_api_username: "test-user"
    routeros_firewall_api_password: "test-pass"
    routeros_firewall_paths: [filter]
    routeros_firewall_ipv6_paths: []
    routeros_firewall_comment_prefix: "ansible:"
    routeros_firewall:
      filter:
        - chain: input
          action: drop
          comment: "hand-built rule"   # missing prefix
        - chain: input
          action: accept
          comment: "ansible: ok"
  tasks:
    - ansible.builtin.import_role:
        name: routeros_firewall
        tasks_from: _assert_inputs.yml
      vars:
        _assert_check_desired: true
```

Run: `cd /workspace/igou-ansible && ansible-playbook /tmp/test_assert_fail_prefix.yaml; echo "exit=$?"`

Expected: assertion fails with the offender `"hand-built rule"` listed in `fail_msg`. Exit code 2 (assertion failed).

- [ ] **Step 4: Smoke-test with a failing fixture (duplicate comment)**

Create `/tmp/test_assert_fail_dup.yaml`:

```yaml
---
- hosts: localhost
  gather_facts: false
  vars:
    routeros_firewall_api_username: "test-user"
    routeros_firewall_api_password: "test-pass"
    routeros_firewall_paths: [filter]
    routeros_firewall_ipv6_paths: []
    routeros_firewall_comment_prefix: "ansible:"
    routeros_firewall:
      filter:
        - chain: input
          action: drop
          comment: "ansible: same"
        - chain: forward
          action: drop
          comment: "ansible: same"   # duplicate
  tasks:
    - ansible.builtin.import_role:
        name: routeros_firewall
        tasks_from: _assert_inputs.yml
      vars:
        _assert_check_desired: true
```

Run: `cd /workspace/igou-ansible && ansible-playbook /tmp/test_assert_fail_dup.yaml; echo "exit=$?"`

Expected: assertion fails listing `same` as a duplicate in `filter`. Exit code 2.

- [ ] **Step 5: Smoke-test with missing credentials**

Create `/tmp/test_assert_fail_creds.yaml`:

```yaml
---
- hosts: localhost
  gather_facts: false
  vars:
    routeros_firewall_api_username: ~
    routeros_firewall_api_password: ~
  tasks:
    - ansible.builtin.import_role:
        name: routeros_firewall
        tasks_from: _assert_inputs.yml
      vars:
        _assert_check_desired: false
```

Run: `cd /workspace/igou-ansible && ansible-playbook /tmp/test_assert_fail_creds.yaml; echo "exit=$?"`

Expected: assertion fails with clear message about missing credentials. Exit code 2.

- [ ] **Step 6: Clean up the temp fixtures**

Run: `rm -f /tmp/test_assert_*.yaml`

- [ ] **Step 7: Commit (in `igou-ansible`)**

```bash
cd /workspace/igou-ansible
git add roles/routeros_firewall/tasks/_assert_inputs.yml
git commit -m "$(cat <<'EOF'
feat(routeros_firewall): add shared input-assertion include

_assert_inputs.yml validates supplied credentials and (when called by
the audit entrypoint) checks that every desired rule carries the
configured comment prefix and that comments are unique per managed
path. Fails the play with actionable messages before any API call.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Export entrypoint

**Why:** Bootstrapping needs to come before audit — without this, the operator has no `host_vars/<host>/firewall.yml` to audit against.

**Files:**
- Create: `/workspace/igou-ansible/roles/routeros_firewall/tasks/export.yml`
- Create: `/workspace/igou-ansible/roles/routeros_firewall/templates/firewall.yml.j2`

- [ ] **Step 1: Write the Jinja template that renders the host_vars YAML**

Create `/workspace/igou-ansible/roles/routeros_firewall/templates/firewall.yml.j2`:

```jinja
---
# Generated by playbooks/routeros/firewall-export.yaml on {{ lookup('pipe', 'date -u +%FT%TZ') }}
# Source: live state of {{ inventory_hostname }}.
# Review the auto-applied '{{ routeros_firewall_comment_prefix }}' prefixes
# before committing. Hand-rename auto-imported comments to something
# meaningful; the role enforces uniqueness per path.
routeros_firewall:
{% for path, rules in _routeros_firewall_export_payload.items() %}
  {{ path }}:
{% for rule in rules %}
{%   set _existing = (rule.comment | default('') | string) %}
{%   if _existing.startswith(routeros_firewall_comment_prefix) %}
{%     set _fixed = rule %}
{%   else %}
{%     set _fixed = rule | combine({'comment': routeros_firewall_comment_prefix ~ ' ' ~ (_existing or 'auto-imported')}) %}
{%   endif %}
    - {{ _fixed | to_nice_yaml(indent=2) | indent(6) | trim }}
{% endfor %}
{% endfor %}
```

Notes:
- Ansible's template module defaults to `trim_blocks: true`, so the `{% set %}` / `{% if %}` lines don't emit blank lines.
- `to_nice_yaml(indent=2)` produces the rule dict with no leading indent on the first line; `| indent(6)` adds 6 spaces to subsequent lines (column-8 alignment after `    - `).
- Prefix application: if the existing comment already starts with `{{ routeros_firewall_comment_prefix }}`, leave the rule unchanged; otherwise prepend the prefix to the existing comment (or use `auto-imported` if there was no comment).

- [ ] **Step 2: Write the export task file**

Create `/workspace/igou-ansible/roles/routeros_firewall/tasks/export.yml`:

```yaml
---
# routeros_firewall — export-to-YAML entrypoint.
#
# Reads live firewall state from the target device via
# community.routeros.api_info, applies the configured comment prefix
# to any unprefixed rule, and renders host_vars/<host>/firewall.yml
# via templates/firewall.yml.j2. Operator reviews + commits the result.
#
# Read-only with respect to the device.

- name: Validate inputs (credentials only — no desired state yet)
  ansible.builtin.import_tasks: _assert_inputs.yml
  vars:
    _assert_check_desired: false

- name: Collect live firewall state from each managed path
  community.routeros.api_info:
    hostname: "{{ routeros_firewall_api_host }}"
    port: "{{ routeros_firewall_api_port }}"
    username: "{{ routeros_firewall_api_username }}"
    password: "{{ routeros_firewall_api_password }}"
    tls: "{{ routeros_firewall_api_tls }}"
    validate_certs: "{{ routeros_firewall_api_validate_certs }}"
    path: "{{ _routeros_firewall_full_path }}"
    hide_defaults: true
    unfiltered: false
  register: _routeros_firewall_info_results
  loop: >-
    {{
      (routeros_firewall_paths | map('regex_replace', '^', 'ip firewall ') | list)
      +
      (routeros_firewall_ipv6_paths | map('regex_replace', '^', 'ipv6 firewall ') | list)
    }}
  loop_control:
    loop_var: _routeros_firewall_full_path
    label: "{{ _routeros_firewall_full_path }}"
  delegate_to: localhost

- name: Map API paths back to host_vars keys (ipv6-* for IPv6, bare for IPv4)
  ansible.builtin.set_fact:
    _routeros_firewall_export_payload: >-
      {{
        dict(
          _routeros_firewall_info_results.results
          | map(attribute='_routeros_firewall_full_path')
          | map('regex_replace', '^ipv6 firewall ', 'ipv6-')
          | map('regex_replace', '^ip firewall ', '')
          | zip(
              _routeros_firewall_info_results.results
              | map(attribute='result')
              | map('default', [], true)
            )
        )
      }}

- name: Render the host_vars firewall.yml
  ansible.builtin.template:
    src: firewall.yml.j2
    dest: "{{ routeros_firewall_export_path }}"
    mode: "0644"
  delegate_to: localhost

- name: Summarize the export
  ansible.builtin.debug:
    msg: |-
      Exported {{ _routeros_firewall_export_payload | dict2items | map(attribute='value') | map('length') | sum }} rule(s)
      across {{ _routeros_firewall_export_payload | length }} path(s)
      to {{ routeros_firewall_export_path }}.
      Review auto-applied '{{ routeros_firewall_comment_prefix }}' prefixes before committing.
```

Notes:
- `community.routeros` uses **space-separated** RouterOS paths (e.g. `ip firewall filter`), NOT slash-separated. Both `api_info` and `api_modify` validate `path` against a fixed `choices` list in this form. The `regex_replace` chains in the loop build paths in this format.
- `api_info` returns the rule list under the `result` attribute on each loop entry. The `| map('default', [], true)` guards against `result` being missing in error cases.
- Path normalization runs `regex_replace` for IPv6 FIRST (mapping `ipv6 firewall filter` → `ipv6-filter`), then IPv4 (`ip firewall filter` → `filter`). IPv6 first because `^ip firewall ` is a prefix of `^ipv6 firewall ` if anchoring is relaxed; doing v6 first guarantees correctness.
- `hide_defaults: true` strips fields equal to RouterOS defaults; `unfiltered: false` excludes read-only/computed fields like `.id`, `bytes`, `packets`, `dynamic`.
- Dict keys use the bare sub-path (`filter`) for IPv4 and `ipv6-` prefix for IPv6, matching the spec data model and the audit entrypoint's consumption (Task 6).

- [ ] **Step 3: Syntax-check the role's export entrypoint**

Run:
```bash
cd /workspace/igou-ansible
ansible-playbook --syntax-check -i 'localhost,' -c local /dev/stdin <<'EOF'
---
- hosts: localhost
  gather_facts: false
  vars:
    routeros_firewall_api_username: "x"
    routeros_firewall_api_password: "y"
  tasks:
    - ansible.builtin.import_role:
        name: routeros_firewall
        tasks_from: export.yml
EOF
```

Expected: `playbook: /dev/stdin` with no errors. A successful syntax-check confirms YAML and task structure parse; runtime behavior is verified by Task 10 against the real device.

- [ ] **Step 4: Commit (in `igou-ansible`)**

```bash
cd /workspace/igou-ansible
git add roles/routeros_firewall/tasks/export.yml roles/routeros_firewall/templates/firewall.yml.j2
git commit -m "$(cat <<'EOF'
feat(routeros_firewall): add export-to-YAML entrypoint

tasks/export.yml reads live firewall state via api_info, maps the
RouterOS API paths back to the host_vars data-model keys (bare for
IPv4, ipv6-* prefix for IPv6), and renders the result via
templates/firewall.yml.j2. The template auto-prepends the configured
comment prefix to any rule whose comment lacks it. Operator reviews
and commits the generated file. Read-only re: device.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Audit entrypoint

**Why:** The Phase 1 deliverable. Reads desired state, validates it, runs `api_modify --check` per managed path, emits a structured diff.

**Files:**
- Create: `/workspace/igou-ansible/roles/routeros_firewall/tasks/main.yml`

- [ ] **Step 1: Write the audit task file**

```yaml
---
# routeros_firewall — audit (drift report) entrypoint. Default tasks_from
# for this role.
#
# Validates inputs, then calls community.routeros.api_modify in check_mode
# against each managed sub-path, passing the desired-state list from
# routeros_firewall[<path>]. Module computes adds/removes/modifies
# internally. Aggregated diff is emitted to stdout.
#
# The play MUST be invoked with `--diff` (or `diff: true` on the tasks)
# for the per-field before/after to appear. Without --diff the module
# still reports changed=true|false but no actionable detail.

- name: Validate inputs (credentials + desired state)
  ansible.builtin.import_tasks: _assert_inputs.yml
  vars:
    _assert_check_desired: true

- name: Audit IPv4 firewall sub-paths
  community.routeros.api_modify:
    hostname: "{{ routeros_firewall_api_host }}"
    port: "{{ routeros_firewall_api_port }}"
    username: "{{ routeros_firewall_api_username }}"
    password: "{{ routeros_firewall_api_password }}"
    tls: "{{ routeros_firewall_api_tls }}"
    validate_certs: "{{ routeros_firewall_api_validate_certs }}"
    path: "ip firewall {{ _routeros_firewall_path }}"
    data: "{{ routeros_firewall[_routeros_firewall_path] | default([]) }}"
    handle_absent_entries: remove
    ensure_order: "{{ routeros_firewall_ordered[_routeros_firewall_path] | default(true) }}"
  register: _routeros_firewall_audit_results_v4
  check_mode: true
  diff: true
  loop: "{{ routeros_firewall_paths }}"
  loop_control:
    loop_var: _routeros_firewall_path
    label: "ip firewall {{ _routeros_firewall_path }}"
  delegate_to: localhost

- name: Audit IPv6 firewall sub-paths
  community.routeros.api_modify:
    hostname: "{{ routeros_firewall_api_host }}"
    port: "{{ routeros_firewall_api_port }}"
    username: "{{ routeros_firewall_api_username }}"
    password: "{{ routeros_firewall_api_password }}"
    tls: "{{ routeros_firewall_api_tls }}"
    validate_certs: "{{ routeros_firewall_api_validate_certs }}"
    path: "ipv6 firewall {{ _routeros_firewall_path }}"
    data: "{{ routeros_firewall['ipv6-' ~ _routeros_firewall_path] | default([]) }}"
    handle_absent_entries: remove
    ensure_order: "{{ routeros_firewall_ordered[_routeros_firewall_path] | default(true) }}"
  register: _routeros_firewall_audit_results_v6
  check_mode: true
  diff: true
  loop: "{{ routeros_firewall_ipv6_paths }}"
  loop_control:
    loop_var: _routeros_firewall_path
    label: "ipv6 firewall {{ _routeros_firewall_path }}"
  delegate_to: localhost
  when: routeros_firewall_ipv6_paths | length > 0

- name: Summarize drift per path
  ansible.builtin.debug:
    msg: |-
      Drift summary for {{ inventory_hostname }}:
      {% for r in (_routeros_firewall_audit_results_v4.results | default([])) +
                  (_routeros_firewall_audit_results_v6.results | default([])) %}
        {{ r._routeros_firewall_path }}: changed={{ r.changed }}
      {% endfor %}
      (Drift exists where changed=true. Run with --diff for per-field before/after.)
```

Notes:
- `community.routeros` uses space-separated paths (`ip firewall filter`, NOT `/ip/firewall/filter`). Validated against the actual `choices` list in the module source.
- IPv6 desired state lives under composite keys like `ipv6-filter`, `ipv6-nat`, etc., in `routeros_firewall`. The export entrypoint emits these the same way.
- `ensure_order: true` for filter/nat/mangle/raw enforces that the rule order on the device matches the YAML list order. Required for firewall semantics (drop rules at the bottom). `address-list` is set-semantic — order doesn't matter — and `routeros_firewall_ordered['address-list']` defaults to `false`. `ensure_order` requires `handle_absent_entries: remove`.
- `delegate_to: localhost` — both `api_modify` and `api_info` run on the controller (or EE), opening an outbound API connection to the device. Inventory's `ansible_connection: ansible.netcommon.network_cli` is irrelevant to these tasks.
- `check_mode: true` is set at the task level so `--diff` alone enables drift output even without `--check`. This is intentional: even on a regular `ansible-playbook` invocation (no `--check`), the role never modifies anything.

- [ ] **Step 2: Syntax-check the audit entrypoint**

Run: `cd /workspace/igou-ansible && ansible-playbook --syntax-check -i 'localhost,' -c local /dev/stdin <<'EOF'
---
- hosts: localhost
  gather_facts: false
  vars:
    routeros_firewall_api_username: "x"
    routeros_firewall_api_password: "y"
    routeros_firewall:
      filter: []
      nat: []
      mangle: []
      raw: []
      address-list: []
  tasks:
    - ansible.builtin.import_role:
        name: routeros_firewall
EOF`

Expected: no errors. Confirms YAML/Jinja parses; runtime is verified at Task 8.

- [ ] **Step 3: Commit (in `igou-ansible`)**

```bash
cd /workspace/igou-ansible
git add roles/routeros_firewall/tasks/main.yml
git commit -m "$(cat <<'EOF'
feat(routeros_firewall): add audit entrypoint

tasks/main.yml runs community.routeros.api_modify in check_mode against
each managed firewall sub-path with handle_absent_entries=remove, then
summarizes drift per path. Requires --diff for per-field detail. No
device-side mutation in Phase 1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Playbook wrappers

**Why:** Thin entrypoints AAP and humans actually invoke. Match the style of existing `playbooks/routeros/*.yml`.

**Files:**
- Create: `/workspace/igou-ansible/playbooks/routeros/firewall-audit.yaml`
- Create: `/workspace/igou-ansible/playbooks/routeros/firewall-export.yaml`

- [ ] **Step 1: Write firewall-audit.yaml**

```yaml
---
# Audit RouterOS firewall state against the desired YAML in
# igou-inventory/host_vars/<inventory_hostname>/firewall.yml. Read-only.
#
# This playbook never modifies the device. It runs
# community.routeros.api_modify with check_mode: true (set inside the
# role) and emits a structured drift report.
#
# Run with --diff to see per-field before/after:
#   ansible-navigator run playbooks/routeros/firewall-audit.yaml \
#     -i igou-inventory/inventory.yaml --diff
#
# Default target: the routeros group. Override with -e host=<name>.
- name: Audit RouterOS firewall against desired state
  hosts: "{{ host | default('routeros_router') }}"
  gather_facts: false

  tasks:
    - name: Run routeros_firewall audit
      ansible.builtin.import_role:
        name: routeros_firewall
```

Note on `hosts:` default: targeting `routeros_router` (just rb5009) instead of the broader `routeros` group is intentional for Phase 1 — switches don't have host_vars/firewall.yml yet and shouldn't be implicitly included until they do.

- [ ] **Step 2: Write firewall-export.yaml**

```yaml
---
# Export live RouterOS firewall state into the declarative model. Writes
# igou-inventory/host_vars/<inventory_hostname>/firewall.yml. The
# operator reviews + commits the result. Read-only re: the device.
#
# Default target: rb5009 only. Override with -e host=<name>.
#
#   ansible-navigator run playbooks/routeros/firewall-export.yaml \
#     -i igou-inventory/inventory.yaml -e host=rb5009.igou.systems
- name: Export RouterOS firewall state to host_vars YAML
  hosts: "{{ host | default('routeros_router') }}"
  gather_facts: false

  tasks:
    - name: Run routeros_firewall export
      ansible.builtin.import_role:
        name: routeros_firewall
        tasks_from: export.yml
```

- [ ] **Step 3: Syntax-check both wrappers**

Run:
```bash
cd /workspace/igou-ansible
ansible-playbook --syntax-check playbooks/routeros/firewall-audit.yaml
ansible-playbook --syntax-check playbooks/routeros/firewall-export.yaml
```

Expected: both report `playbook: <path>` with no errors. Both will fail to *run* without an inventory and credentials, which is the right behavior.

- [ ] **Step 4: Commit (in `igou-ansible`)**

```bash
cd /workspace/igou-ansible
git add playbooks/routeros/firewall-audit.yaml playbooks/routeros/firewall-export.yaml
git commit -m "$(cat <<'EOF'
feat(routeros): add firewall-audit + firewall-export playbooks

Thin wrappers around the routeros_firewall role's audit (default) and
export entrypoints. Targets routeros_router (rb5009) by default;
override with -e host=. Audit emits structured drift; export writes
host_vars/<host>/firewall.yml for the operator to curate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Inventory glue — credentials and lab overrides

**Why:** The role is lab-agnostic; the 1Password lookup belongs in the inventory. This commit lives in the `igou-inventory` repo, not `igou-ansible`.

**Files (in `igou-inventory`):**
- Modify: `/workspace/igou-ansible/igou-inventory/group_vars/routeros.yml`

- [ ] **Step 1: Inspect current routeros group_vars**

Run: `head -80 /workspace/igou-ansible/igou-inventory/group_vars/routeros.yml`

Expected: shows the existing baseline/NTP/users vars. You'll append to this file, not replace it.

- [ ] **Step 2: Verify the `awx` 1Password vault has an `rb5009-api` item**

This is an operator prerequisite, not a code step. The role won't run without it. Check by:

```bash
op item get rb5009-api --vault awx --fields username,password 2>&1 | head
```

Expected: shows the username and a redacted password. If it fails, the operator must create the item before running Task 9's smoke test. See spec Section 12.

- [ ] **Step 3: Append routeros_firewall config to group_vars/routeros.yml**

Append this block to `/workspace/igou-ansible/igou-inventory/group_vars/routeros.yml` (do not replace any existing content):

```yaml

# --- Variables consumed by roles/routeros_firewall (in igou-ansible) ---

# API credentials for community.routeros.api_modify / api_info. Resolved
# from 1Password at play time. The role itself is lab-agnostic and does
# no secret resolution; this lookup lives in the inventory side.
routeros_firewall_api_username: >-
  {{ lookup('community.general.onepassword',
            'rb5009-api', field='username', vault='awx') }}
routeros_firewall_api_password: >-
  {{ lookup('community.general.onepassword',
            'rb5009-api', field='password', vault='awx') }}

# rb5009 currently presents a self-signed API cert. The role default is
# validate_certs=true; flip back once a real cert is wired.
# Tracked alongside spec Section 12 follow-ups.
routeros_firewall_api_validate_certs: false
```

- [ ] **Step 4: Verify lookup resolves (one-shot check; no device call)**

Run: `cd /workspace/igou-ansible && ansible -i igou-inventory/inventory.yaml routeros_router -m debug -a "msg='username={{ routeros_firewall_api_username }} password_len={{ routeros_firewall_api_password | length }}'"`

Expected: prints the username and the password length (e.g. `password_len=24`). If 1Password lookup fails, check `op signin` status and that the `awx` vault is accessible to this account.

If you see `password_len=0`, the lookup returned empty — fix before continuing.

- [ ] **Step 5: Commit (in `igou-inventory`)**

```bash
cd /workspace/igou-ansible/igou-inventory
git add group_vars/routeros.yml
git commit -m "$(cat <<'EOF'
feat(routeros): wire 1Password credentials for routeros_firewall role

Add routeros_firewall_api_{username,password} pulling from the 'awx'
1P vault's 'rb5009-api' item. Override validate_certs=false until a
real cert lands on rb5009. The role in igou-ansible is lab-agnostic
and depends on these being supplied from inventory.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: AAP job templates

**Why:** So the audit can run on a nightly schedule and export can be triggered on demand from the AAP UI without shelling into a controller.

**Files (in `igou-inventory`):**
- Modify: `/workspace/igou-ansible/igou-inventory/group_vars/aap/job_templates.yml`

- [ ] **Step 1: Find the right insertion point**

Run: `grep -n "^  - name:" /workspace/igou-ansible/igou-inventory/group_vars/aap/job_templates.yml | tail -5`

Expected: list of existing template names; pick a sensible spot (after other read-only/audit templates if grouped that way; otherwise append at the end).

- [ ] **Step 2: Append the two new job-template entries**

At the end of `controller_templates:` in `/workspace/igou-ansible/igou-inventory/group_vars/aap/job_templates.yml`, add:

```yaml
  # ===== RouterOS firewall (Phase 1: audit-only) =====
  - name: routeros_firewall_audit
    description: >-
      Audit RouterOS firewall state against the desired YAML in
      host_vars/<host>/firewall.yml. Read-only; emits a drift report.
    labels:
      - routeros
      - networking
      - audit
    project: igou_ansible
    job_type: run
    playbook: playbooks/routeros/firewall-audit.yaml
    inventory: igou_inventory
    execution_environment: igou-aap-ee-rhel9
    diff_mode: true
    concurrent_jobs_enabled: false
    ask_variables_on_launch: true
    verbosity: 1
    credentials:
      - onepassword
    extra_vars:
      host: routeros_router

  - name: routeros_firewall_export
    description: >-
      Export live RouterOS firewall state to host_vars/<host>/firewall.yml
      in the inventory repo. Read-only re: device; writes to controller
      filesystem (operator commits result to igou-inventory).
    labels:
      - routeros
      - networking
    project: igou_ansible
    job_type: run
    playbook: playbooks/routeros/firewall-export.yaml
    inventory: igou_inventory
    execution_environment: igou-aap-ee-rhel9
    concurrent_jobs_enabled: false
    ask_variables_on_launch: true
    verbosity: 1
    credentials:
      - onepassword
    extra_vars:
      host: routeros_router
```

Note: `diff_mode: true` on the audit template is critical — without it the drift report has no per-field detail.

- [ ] **Step 3: Verify the YAML still parses**

Run: `cd /workspace/igou-ansible/igou-inventory && yamllint group_vars/aap/job_templates.yml`

Expected: clean exit (or only style warnings, no errors).

- [ ] **Step 4: Commit (in `igou-inventory`)**

```bash
cd /workspace/igou-ansible/igou-inventory
git add group_vars/aap/job_templates.yml
git commit -m "$(cat <<'EOF'
feat(aap): add routeros_firewall_{audit,export} job templates

Audit template runs nightly-ready; surfaces drift on rb5009 firewall.
Export template is manual-trigger for bootstrap / snapshot refresh.
Both use the AAP RHEL9 EE which already bundles community.routeros.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: End-to-end smoke test

**Why:** Final verification that everything wired together actually talks to rb5009 and emits a sane drift report and export. Operator-driven, not automated.

**Prerequisites:** 1Password `rb5009-api` item exists; `api-ssl` is enabled on rb5009; Tasks 1-9 committed.

- [ ] **Step 1: Run the export playbook against rb5009**

```bash
cd /workspace/igou-ansible
ansible-navigator run playbooks/routeros/firewall-export.yaml \
  -i igou-inventory/inventory.yaml \
  -e host=rb5009.igou.systems \
  --mode stdout
```

Expected:
- Play targets `rb5009.igou.systems`.
- `api_info` tasks succeed (one per managed path).
- Final debug summary prints `Exported N rule(s) across M path(s) to /workspace/igou-ansible/igou-inventory/host_vars/rb5009.igou.systems/firewall.yml`.
- Exit code 0.

If `api_info` fails with auth: re-check Task 8 step 4. If with TLS: re-check `routeros_firewall_api_validate_certs: false` is set in inventory.

- [ ] **Step 2: Inspect the generated YAML**

Open `/workspace/igou-ansible/igou-inventory/host_vars/rb5009.igou.systems/firewall.yml` in an editor. Sanity checks:
- Top-level key is `routeros_firewall:`.
- Sub-keys are `filter`, `nat`, `mangle`, `raw`, `address-list` (some may be empty).
- Every rule has a `comment` starting with `ansible:`.
- Field names look like RouterOS API names (`in-interface-list`, not `in_interface_list`).

The operator may now hand-curate the file — rename auto-imported comments to something more meaningful, drop ephemeral rules, etc. THIS IS NOT a step the plan automates.

- [ ] **Step 3: Run the audit playbook against rb5009 (with --diff)**

```bash
cd /workspace/igou-ansible
ansible-navigator run playbooks/routeros/firewall-audit.yaml \
  -i igou-inventory/inventory.yaml \
  -e host=rb5009.igou.systems \
  --diff \
  --mode stdout
```

Expected immediately after Step 1's export (operator has not yet edited the YAML):
- All audit tasks report `changed=false`.
- Drift summary prints zero changed paths.
- Exit code 0.

If `changed=true` on first run when YAML was just exported, investigate: `api_info` and `api_modify`'s view of "default" fields may disagree. File this as a follow-up; do NOT block on it for the plan to be complete unless it's pervasive.

- [ ] **Step 4: Verify drift detection by introducing intentional drift**

Edit `/workspace/igou-ansible/igou-inventory/host_vars/rb5009.igou.systems/firewall.yml` and add a fake rule under `filter`:

```yaml
    - chain: input
      action: drop
      src-address: 192.0.2.99
      comment: "ansible: intentional drift test"
```

Run the audit again. Expected:
- `changed=true` for `/ip/firewall/filter`.
- Diff output shows the extra rule as "would be added".
- Exit code 0 (the playbook itself doesn't fail; drift is reported, not exception-raised).

Then revert the file (`git checkout host_vars/rb5009.igou.systems/firewall.yml`) and confirm a third audit reports no drift again.

- [ ] **Step 5: Commit the curated host_vars (in `igou-inventory`)**

After the operator has reviewed and curated the exported YAML, commit it:

```bash
cd /workspace/igou-ansible/igou-inventory
git add host_vars/rb5009.igou.systems/firewall.yml
git commit -m "$(cat <<'EOF'
feat(rb5009): commit declarative firewall state

Initial capture via playbooks/routeros/firewall-export.yaml followed
by operator review (renamed auto-prefixed comments, removed ephemeral
rules). This file is now the source of truth for rb5009 firewall
state; nightly routeros_firewall_audit surfaces drift.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Push both repos**

Both `igou-ansible` and `igou-inventory` need their respective commits pushed for AAP to pick them up. Don't push without confirming with the user, since `igou-inventory` carries the secrets-resolving lookups and a fresh git push touches AAP's project sync. See the executing-plans guidance on confirming destructive operations.

---

## Self-review

### Spec coverage

Walked the spec section-by-section:
- §1 (Goal): Tasks 5 + 6 + 7 (export, audit, playbook wrappers). ✓
- §2 (Non-goals): no implementation needed.
- §3.1 (api_modify check_mode): Task 6. ✓
- §3.2 (api-ssl transport): Task 3 defaults, Task 8 lab override. ✓
- §3.3 (audit-only, Phase 1): Task 6 sets check_mode=true; Phase 2 is out of scope. ✓
- §3.4 (host_vars location): Task 3 default, Task 10 step 5. ✓
- §3.5 (role, not playbook-native): Tasks 2-6 are the role. ✓
- §4 (Repo layout, ansible.cfg): Task 1. ✓
- §5 (Role contract): Tasks 3 + 4. ✓
- §6 (Data model): Task 5 generates, Task 6 consumes. ✓
- §7 (Components / data flow): Tasks 5 + 6 implement, Task 10 verifies. ✓
- §8 (Error handling): Task 4 covers schema mistakes + missing creds; runtime API failures bubble up natively from the modules. ✓
- §9 (Testing & CI): syntax-check at each step; CI extension implicit since the existing `syntax-check.yml` workflow auto-discovers new playbooks under `playbooks/`. ✓
- §10 (Operator workflow): Task 10 walks it end-to-end. ✓
- §11 (Future collection extraction): role is already lab-agnostic; nothing to do now. ✓
- §12 (Open issues): documented as operator prerequisites at the top. ✓

No gaps identified.

### Placeholder scan

- No "TBD", "TODO", "implement later", or vague "add error handling" lines.
- Every code step has actual code.
- Every command has expected output.
- Cross-task references (e.g. "see Task 8 step 4") name specific tasks/steps.

### Type / name consistency

- `routeros_firewall_*` variable prefix used consistently across role + inventory.
- `_routeros_firewall_*` for internal facts (underscore prefix marks them as private to the role).
- `_assert_check_desired` flag named consistently between `_assert_inputs.yml` and both callers.
- Comment prefix is `"ansible:"` everywhere (not `"ans:"` from an earlier brainstorming draft).
- `routeros_router` group (not `routeros`) used in playbook `hosts:` defaults and AAP `extra_vars` consistently.
- `community.routeros.api_modify` / `api_info` FQCN used everywhere; same for `community.general.onepassword` and `community.general.counter`.

No mismatches found.
