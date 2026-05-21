# AAP Configuration Repeatability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `playbooks/aap/configure-aap*.yml` runnable repeatably against the OCP-deployed AAP at `https://automation.apps.ocp.igou.systems` via `ansible-navigator` using the AAP RHEL9 EE, with auth resolved from 1Password at run time.

**Architecture:** Inventory adds `group_vars/aap/auth.yml` with `aap_*` connection vars pulled from a 1Password `aap` item (vault `awx`); navigator config under `playbooks/aap/ansible-navigator.yml` pins the AAP EE for AAP plays only; a repo-root `Makefile` provides `make aap-configure`/`-sync-credentials`/`-sync-templates` as canonical entry points. Auth and EE stop depending on shell-exported `CONTROLLER_*` state.

**Tech Stack:** `ansible-navigator`, `infra.aap_configuration` collection (dispatch role), AAP 2.5 (OCP operator), `community.general.onepassword` lookup, 1Password Service Account, GNU Make.

**Repos touched:**
- `/workspace/igou-ansible` (main, this plan's home).
- `/workspace/igou-ansible/igou-inventory` (symlink to separate repo `/workspace/igou-inventory`; commits land in that repo's history).

**Spec:** `docs/superpowers/specs/2026-05-21-aap-configuration-design.md` (commit `53c4109`).

---

## File Structure

| Action | Repo | Path | Responsibility |
| ---- | ---- | ---- | ---- |
| NEW | `igou-inventory` | `group_vars/aap/auth.yml` | AAP gateway connection vars (`aap_hostname`/`username`/`password`/`validate_certs`) from 1Password lookup. |
| EDIT | `igou-inventory` | `group_vars/aap/execution_environments.yml` | Repoint `igou-aap-ee-rhel9` at `quay.apps.ocp.igou.systems` and drop `credential:`. |
| EDIT | `igou-inventory` | `group_vars/aap/credentials.yml` | Repoint `internal_quay` credential host (placeholder retention only). |
| NEW | `igou-ansible` | `playbooks/aap/ansible-navigator.yml` | Per-playbook navigator config pinning the AAP RHEL9 EE. |
| NEW | `igou-ansible` | `Makefile` | `aap-configure`, `aap-sync-credentials`, `aap-sync-templates` targets (with `_check-inv` guard on `ANSIBLE_INVENTORY`). |
| EDIT | `igou-ansible` | `playbooks/aap/configure-aap-templates.yml` | Replace `infra.controller_configuration.dispatch` with `infra.aap_configuration.dispatch`. |
| OUT-OF-BAND | 1Password (vault `awx`) | item `aap`, field `host` | Set to `https://automation.apps.ocp.igou.systems`. |

---

## Task 0: Prerequisite — Verify 1Password `aap` item

**Files:** none.

This is a manual verification step. The 1Password item update (vault `awx`, item `aap`, field `host`) must be done out-of-band by the user before downstream tasks can validate. If `op` CLI is available in the runtime, verify via CLI; otherwise verify in the 1Password UI.

- [ ] **Step 1: Confirm OP_SERVICE_ACCOUNT_TOKEN is exported**

```bash
test -n "$OP_SERVICE_ACCOUNT_TOKEN" && echo "OP token set" || echo "OP TOKEN MISSING — export it before proceeding"
```

Expected: `OP token set`.

- [ ] **Step 2: Read the current `aap.host` field**

```bash
op item get aap --vault awx --field host
```

Expected output: `https://automation.apps.ocp.igou.systems`.

If the value is anything else (e.g. `https://automation.hub.sno.igou.systems`), STOP and ask the user to update the 1Password item via UI or:

```bash
op item edit aap --vault awx host='https://automation.apps.ocp.igou.systems'
```

- [ ] **Step 3: Read username and password fields exist (do not print password values)**

```bash
op item get aap --vault awx --fields label=username --format json | jq -r .value | head -c 40 ; echo
op item get aap --vault awx --fields label=password --format json | jq -r 'if .value then "password field present (length: \(.value | length))" else "MISSING" end'
```

Expected: a non-empty username and `password field present (length: N)` for some N>0.

---

## Task 1: Add `group_vars/aap/auth.yml` to igou-inventory

**Files:**
- Create: `/workspace/igou-ansible/igou-inventory/group_vars/aap/auth.yml`

- [ ] **Step 1: Write the new auth.yml**

Create `/workspace/igou-ansible/igou-inventory/group_vars/aap/auth.yml`:

```yaml
---
# Connection to the OCP-deployed AAP gateway. Resolved at run time from the
# `aap` item in 1Password vault `awx`. OP_SERVICE_ACCOUNT_TOKEN must be
# exported in the navigator runner env.
aap_hostname: "{{ lookup('community.general.onepassword', 'aap', field='host', vault='awx') }}"
aap_username: "{{ lookup('community.general.onepassword', 'aap', field='username', vault='awx') }}"
aap_password: "{{ lookup('community.general.onepassword', 'aap', field='password', vault='awx') }}"
aap_validate_certs: true
```

- [ ] **Step 2: Lint the new file**

```bash
yamllint /workspace/igou-ansible/igou-inventory/group_vars/aap/auth.yml
```

Expected: no output, exit 0.

- [ ] **Step 3: Verify the lookup resolves**

From `/workspace/igou-ansible`:

```bash
ANSIBLE_INVENTORY=igou-inventory/inventory.yaml \
  ansible-inventory -i igou-inventory/inventory.yaml --host aap_host \
  | jq -r .aap_hostname
```

Expected: `https://automation.apps.ocp.igou.systems`.

If output is empty or has a different host, recheck Task 0 — the 1Password item is the source of truth and this step is what validates it end-to-end.

- [ ] **Step 4: Confirm aap_validate_certs is the literal boolean true**

```bash
ANSIBLE_INVENTORY=igou-inventory/inventory.yaml \
  ansible-inventory -i igou-inventory/inventory.yaml --host aap_host \
  | jq .aap_validate_certs
```

Expected: `true` (lowercase, no quotes).

- [ ] **Step 5: Commit (in the igou-inventory repo)**

```bash
cd /workspace/igou-ansible/igou-inventory
git add group_vars/aap/auth.yml
git commit -m "$(cat <<'EOF'
aap: add group_vars/aap/auth.yml for gateway connection

Resolves aap_hostname/username/password/validate_certs at run time
from the `aap` item in 1Password vault `awx`. Makes
playbooks/aap/configure-aap*.yml runnable without exporting
CONTROLLER_HOST/USERNAME/PASSWORD in the shell.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
cd /workspace/igou-ansible
```

---

## Task 2: Repoint igou-aap-ee-rhel9 EE record at the OCP registry

**Files:**
- Modify: `/workspace/igou-ansible/igou-inventory/group_vars/aap/execution_environments.yml`

- [ ] **Step 1: Read the current file to find exact lines**

```bash
cat /workspace/igou-ansible/igou-inventory/group_vars/aap/execution_environments.yml
```

Expected: shows three EE entries; the `igou-aap-ee-rhel9` entry currently has `image: quay.internal.example.com/igou/igou-aap-ee-rhel9:latest` and `credential: internal_quay`.

- [ ] **Step 2: Apply the edit**

Replace this block:

```yaml
  - name: igou-aap-ee-rhel9
    image: quay.internal.example.com/igou/igou-aap-ee-rhel9:latest
    pull: always
    organization: igou
    description: Igou supported EE (internal quay — placeholder host, update before applying)
    credential: internal_quay
```

with:

```yaml
  - name: igou-aap-ee-rhel9
    image: quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest
    pull: always
    organization: igou
    description: Igou supported EE (built by Tekton, pushed to OCP internal registry)
```

- [ ] **Step 3: Lint**

```bash
yamllint /workspace/igou-ansible/igou-inventory/group_vars/aap/execution_environments.yml
```

Expected: no output, exit 0.

- [ ] **Step 4: Confirm the rendered var has the new image and no credential field**

From `/workspace/igou-ansible`:

```bash
ANSIBLE_INVENTORY=igou-inventory/inventory.yaml \
  ansible-inventory -i igou-inventory/inventory.yaml --host aap_host \
  | jq '.controller_execution_environments[] | select(.name == "igou-aap-ee-rhel9")'
```

Expected: JSON object with `"image": "quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest"` and no `credential` key.

- [ ] **Step 5: Commit (in the igou-inventory repo)**

```bash
cd /workspace/igou-ansible/igou-inventory
git add group_vars/aap/execution_environments.yml
git commit -m "$(cat <<'EOF'
aap: repoint igou-aap-ee-rhel9 EE at OCP internal registry

quay.apps.ocp.igou.systems/igou-io is anonymously pullable for this
image; drop the placeholder internal_quay credential reference from
the EE record.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
cd /workspace/igou-ansible
```

---

## Task 3: Update internal_quay credential placeholder

**Files:**
- Modify: `/workspace/igou-ansible/igou-inventory/group_vars/aap/credentials.yml`

- [ ] **Step 1: Locate the internal_quay credential block**

```bash
grep -n "internal_quay\|quay.internal.example.com" /workspace/igou-ansible/igou-inventory/group_vars/aap/credentials.yml
```

Expected: shows the `- name: internal_quay` entry and the placeholder host inside its `inputs:` block.

- [ ] **Step 2: Apply the edit**

Replace this block:

```yaml
  - name: internal_quay
    credential_type: Container Registry
    organization: igou
    description: Internal quay registry (placeholder host — update before applying)
    inputs:
      host: "https://quay.internal.example.com"
      username: "{{ lookup('community.general.onepassword', 'internal-quay', field='username', vault='awx') }}"
      password: "{{ lookup('community.general.onepassword', 'internal-quay', field='password', vault='awx') }}"
      verify_ssl: true
```

with:

```yaml
  - name: internal_quay
    credential_type: Container Registry
    organization: igou
    description: Internal OCP registry — placeholder credential (image pull is anonymous; kept for future-use scenarios)
    inputs:
      host: "https://quay.apps.ocp.igou.systems"
      username: "{{ lookup('community.general.onepassword', 'internal-quay', field='username', vault='awx') }}"
      password: "{{ lookup('community.general.onepassword', 'internal-quay', field='password', vault='awx') }}"
      verify_ssl: true
```

- [ ] **Step 3: Lint**

```bash
yamllint /workspace/igou-ansible/igou-inventory/group_vars/aap/credentials.yml
```

Expected: no output, exit 0.

- [ ] **Step 4: Commit (in the igou-inventory repo)**

```bash
cd /workspace/igou-ansible/igou-inventory
git add group_vars/aap/credentials.yml
git commit -m "$(cat <<'EOF'
aap: repoint internal_quay credential host to OCP registry

No active consumer references this credential after the EE-record
edit; entry kept as a placeholder for future-use scenarios. The
1Password lookups for username/password are left intact and will
fail at apply time if no `internal-quay` item exists — that's fine
since the credential is decorative.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
cd /workspace/igou-ansible
```

---

## Task 4: Add per-playbook navigator config

**Files:**
- Create: `/workspace/igou-ansible/playbooks/aap/ansible-navigator.yml`

- [ ] **Step 1: Write the per-playbook navigator config**

Create `/workspace/igou-ansible/playbooks/aap/ansible-navigator.yml`:

```yaml
---
# Per-playbook navigator config for AAP configuration runs. Selected via
# ANSIBLE_NAVIGATOR_CONFIG=playbooks/aap/ansible-navigator.yml (the Make
# targets in the repo root Makefile set this). Navigator reads exactly one
# config; the repo-root ansible-navigator.yml is untouched and non-AAP
# plays keep using igou-awx-ee.
ansible-navigator:
  ansible:
    config:
      path: ansible.cfg
  ansible-runner:
    artifact-dir: ~/ansible-navigator
  execution-environment:
    container-engine: podman
    enabled: true
    environment-variables:
      pass:
        - OP_SERVICE_ACCOUNT_TOKEN
    image: quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest
    pull:
      policy: missing
  logging:
    file: ~/ansible-navigator/ansible-navigator.log
    level: debug
  mode: stdout
  playbook-artifact:
    enable: false
```

- [ ] **Step 2: Lint**

```bash
yamllint /workspace/igou-ansible/playbooks/aap/ansible-navigator.yml
```

Expected: no output, exit 0.

- [ ] **Step 3: Confirm navigator parses the config without errors**

```bash
ANSIBLE_NAVIGATOR_CONFIG=/workspace/igou-ansible/playbooks/aap/ansible-navigator.yml \
  ansible-navigator settings --json 2>&1 | jq -r '.["ansible-navigator"]["execution-environment"]["image"]'
```

Expected: `quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest`.

If `ansible-navigator settings` is not available in the active venv, fall back to:

```bash
python3 -c "import yaml; print(yaml.safe_load(open('/workspace/igou-ansible/playbooks/aap/ansible-navigator.yml'))['ansible-navigator']['execution-environment']['image'])"
```

Expected: same string.

- [ ] **Step 4: Commit (in igou-ansible)**

```bash
cd /workspace/igou-ansible
git add playbooks/aap/ansible-navigator.yml
git commit -m "$(cat <<'EOF'
aap: add per-playbook ansible-navigator.yml for AAP RHEL9 EE

Selected via ANSIBLE_NAVIGATOR_CONFIG (set by the new Makefile
targets). Pins image=quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest
and limits env pass-through to OP_SERVICE_ACCOUNT_TOKEN — auth comes
from group_vars/aap/auth.yml via 1Password lookup, not env vars.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Rename infra.controller_configuration.dispatch to infra.aap_configuration.dispatch

**Files:**
- Modify: `/workspace/igou-ansible/playbooks/aap/configure-aap-templates.yml`

- [ ] **Step 1: Read the current file**

```bash
cat /workspace/igou-ansible/playbooks/aap/configure-aap-templates.yml
```

Expected: shows the play that includes `infra.controller_configuration.dispatch`.

- [ ] **Step 2: Apply the edit**

In `/workspace/igou-ansible/playbooks/aap/configure-aap-templates.yml`, replace:

```yaml
        name: infra.controller_configuration.dispatch
```

with:

```yaml
        name: infra.aap_configuration.dispatch
```

- [ ] **Step 3: Verify the only role reference now uses the new collection**

```bash
grep -n "controller_configuration\|aap_configuration" /workspace/igou-ansible/playbooks/aap/configure-aap-templates.yml
```

Expected: exactly one match line, containing `infra.aap_configuration.dispatch`. No remaining `controller_configuration` reference.

- [ ] **Step 4: Lint with ansible-lint (production profile, repo standard)**

```bash
cd /workspace/igou-ansible
ansible-lint --profile=production playbooks/aap/configure-aap-templates.yml
```

Expected: clean exit (0). If ansible-lint flags the file for unrelated reasons, evaluate each finding but only fix what this change introduced; do not expand scope.

- [ ] **Step 5: Commit (in igou-ansible)**

```bash
cd /workspace/igou-ansible
git add playbooks/aap/configure-aap-templates.yml
git commit -m "$(cat <<'EOF'
aap: rename dispatch role to infra.aap_configuration in templates playbook

Brings configure-aap-templates.yml in line with the other two AAP
playbooks (configure-aap.yml and configure-aap-credentials.yml),
which already use infra.aap_configuration. The
infra.controller_configuration collection was renamed/consolidated
into infra.aap_configuration for AAP 2.5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add repo-root Makefile with AAP targets

**Files:**
- Create: `/workspace/igou-ansible/Makefile`

- [ ] **Step 1: Confirm no existing Makefile**

```bash
test -e /workspace/igou-ansible/Makefile && echo "EXISTS — STOP" || echo "no existing Makefile — proceed"
```

Expected: `no existing Makefile — proceed`. If a Makefile already exists, STOP and ask the user; do not overwrite.

- [ ] **Step 2: Write the Makefile**

Create `/workspace/igou-ansible/Makefile`:

```makefile
AAP_NAV_CFG := playbooks/aap/ansible-navigator.yml

.PHONY: _check-inv aap-configure aap-sync-credentials aap-sync-templates

_check-inv:
	@test -n "$(ANSIBLE_INVENTORY)" || { \
	  echo "ANSIBLE_INVENTORY not set (export it pointing at igou-inventory/inventory.yaml)"; \
	  exit 1; \
	}

aap-configure: _check-inv ## Apply all AAP objects via infra.aap_configuration.dispatch
	ANSIBLE_NAVIGATOR_CONFIG=$(AAP_NAV_CFG) \
	  ansible-navigator run playbooks/aap/configure-aap.yml

aap-sync-credentials: _check-inv ## Sync only AAP credentials
	ANSIBLE_NAVIGATOR_CONFIG=$(AAP_NAV_CFG) \
	  ansible-navigator run playbooks/aap/configure-aap-credentials.yml

aap-sync-templates: _check-inv ## Sync only AAP job templates / projects / workflows / schedules
	ANSIBLE_NAVIGATOR_CONFIG=$(AAP_NAV_CFG) \
	  ansible-navigator run playbooks/aap/configure-aap-templates.yml
```

NOTE: Make recipes use **tabs** (not spaces) for indentation. If your editor inserts spaces, fix before committing.

- [ ] **Step 3: Confirm tabs are used**

```bash
grep -P "^\t" /workspace/igou-ansible/Makefile | wc -l
```

Expected: a number >= 5 (each recipe line starts with a tab).

If 0, the file used spaces. Re-author with tabs.

- [ ] **Step 4: Confirm _check-inv guard fires when ANSIBLE_INVENTORY is unset**

```bash
cd /workspace/igou-ansible
unset ANSIBLE_INVENTORY
make _check-inv 2>&1 | head -3
echo "exit: $?"
```

Expected: prints the `ANSIBLE_INVENTORY not set ...` message and exits non-zero.

- [ ] **Step 5: Confirm _check-inv guard passes when ANSIBLE_INVENTORY is set**

```bash
cd /workspace/igou-ansible
ANSIBLE_INVENTORY=igou-inventory/inventory.yaml make _check-inv
echo "exit: $?"
```

Expected: no output, exit 0.

- [ ] **Step 6: Commit (in igou-ansible)**

```bash
cd /workspace/igou-ansible
git add Makefile
git commit -m "$(cat <<'EOF'
aap: add Makefile with aap-configure / aap-sync-* entry points

Canonical entrypoints for repeatable AAP configuration runs via
ansible-navigator. _check-inv guards against missing
ANSIBLE_INVENTORY (the source of truth for which inventory file to
load) and ANSIBLE_NAVIGATOR_CONFIG is set to the per-playbook AAP
navigator config so the AAP RHEL9 EE is selected for these plays.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Validation — sync credentials end-to-end

This task runs the smallest-blast-radius dispatch playbook and confirms it reconciles cleanly.

**Files:** none modified.

- [ ] **Step 1: Confirm prerequisites are set**

```bash
test -n "$OP_SERVICE_ACCOUNT_TOKEN" || { echo "OP token missing"; exit 1; }
test -n "$ANSIBLE_INVENTORY" || export ANSIBLE_INVENTORY=igou-inventory/inventory.yaml
echo "OP token: present"
echo "inventory: $ANSIBLE_INVENTORY"
cd /workspace/igou-ansible
```

Expected: both lines print, no error.

- [ ] **Step 2: Verify the AAP gateway route resolves**

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://automation.apps.ocp.igou.systems/api/gateway/v1/ping/
```

Expected: a 2xx or 401 (auth required but route is up). If 5xx or DNS failure, fix infrastructure before running the dispatch.

- [ ] **Step 3: Run the credentials sync**

```bash
cd /workspace/igou-ansible
make aap-sync-credentials
```

Expected: navigator pulls the AAP RHEL9 EE on first run, then runs the play, then exits 0. The play output shows each controller_credential entry reconciled (changed=N initial run, changed=0 on re-run).

If output reports `HTTP 401`, the 1Password `aap` item still has wrong password — fix Task 0 and rerun.
If output reports `HTTP 403 license/subscription`, AAP subscription wasn't attached — out-of-band prerequisite from spec, surface to user.
If output reports `role not found: infra.aap_configuration.dispatch`, the EE image is stale — rebuild via `.tekton/igou-aap-ee-rhel9-push.yml` and re-run.

- [ ] **Step 4: Re-run for idempotency check**

```bash
cd /workspace/igou-ansible
make aap-sync-credentials
```

Expected: `changed=0` for every credential. Any `changed=1` on the second run indicates a non-idempotent input (e.g., 1Password lookup returning differently formatted whitespace) — investigate before proceeding.

---

## Task 8: Validation — sync templates end-to-end

**Files:** none modified.

- [ ] **Step 1: Run the templates sync**

```bash
cd /workspace/igou-ansible
make aap-sync-templates
```

Expected: navigator runs the play, reconciles each controller_template (and the projects/workflows/schedules dispatch branches the templates playbook also covers), exits 0.

- [ ] **Step 2: Re-run for idempotency**

```bash
cd /workspace/igou-ansible
make aap-sync-templates
```

Expected: `changed=0` for every template.

---

## Task 9: Validation — full configure-aap

**Files:** none modified.

- [ ] **Step 1: Run the full dispatch**

```bash
cd /workspace/igou-ansible
make aap-configure
```

Expected: navigator runs the full dispatch role (orgs + credentials + EEs + projects + inventories + labels + notifications + schedules + workflows + templates), exits 0.

- [ ] **Step 2: Re-run for idempotency**

```bash
cd /workspace/igou-ansible
make aap-configure
```

Expected: `changed=0` everywhere.

- [ ] **Step 3: Verify the EE record in the AAP UI**

Open `https://automation.apps.ocp.igou.systems` in a browser, log in as admin, navigate to Execution Environments. Confirm:

- `igou-aap-ee-rhel9` shows image `quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest`.
- `igou-aap-ee-rhel9` has no credential attached.

If either is wrong, the dispatch role didn't pick up the inventory edits — re-run `make aap-sync-credentials` then `make aap-configure` and recheck.

---

## Task 10: Memory write

**Files:**
- Create: `/home/igou/.claude/projects/-workspace-igou-ansible/memory/reference_aap_endpoint.md`
- Modify: `/home/igou/.claude/projects/-workspace-igou-ansible/memory/MEMORY.md`

- [ ] **Step 1: Write the reference memory file**

Create `/home/igou/.claude/projects/-workspace-igou-ansible/memory/reference_aap_endpoint.md`:

```markdown
---
name: reference-aap-endpoint
description: OCP-deployed AAP 2.5 gateway URL and 1Password item locations for configuration runs
metadata:
  type: reference
---

AAP 2.5 instance (post-2026-05 OCP operator deploy):

- **Gateway URL:** `https://automation.apps.ocp.igou.systems` (signed cert, validate=true).
- **Controller route:** `https://aap-controller-ansible-automation-platform.apps.ocp.igou.systems` (direct; not used by `infra.aap_configuration` plays in this repo — plays target the gateway).
- **OCP namespace:** `ansible-automation-platform`.

Auth source (consumed by `igou-inventory/group_vars/aap/auth.yml`):

- **1Password item:** `aap` in vault `awx`.
- Fields: `host` (gateway URL), `username` (admin), `password` (mirrored from OCP secret `aap-admin-password` in the AAP namespace).

EE for AAP configuration runs:

- **Image:** `quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest`.
- **Pull:** anonymous (no credential needed on the OCP internal registry route for this image).
- **Build pipeline:** `.tekton/igou-aap-ee-rhel9-push.yml` in `igou-ansible`.

Run entrypoint:

- `make aap-configure` / `make aap-sync-credentials` / `make aap-sync-templates` in `/workspace/igou-ansible`.
- Requires `OP_SERVICE_ACCOUNT_TOKEN` and `ANSIBLE_INVENTORY` exported.

The stale `CONTROLLER_HOST=https://automation.hub.sno.igou.systems` env var is no longer used by these plays.

Designed in `docs/superpowers/specs/2026-05-21-aap-configuration-design.md`. Related: [[netboot_architecture_constraints]] for analogous post-migration architecture documentation.
```

- [ ] **Step 2: Append a pointer to MEMORY.md**

Edit `/home/igou/.claude/projects/-workspace-igou-ansible/memory/MEMORY.md` to add (after the existing entries):

```markdown
- [AAP endpoint + auth + EE](reference_aap_endpoint.md) — OCP gateway URL, 1Password `aap` item (vault `awx`), AAP RHEL9 EE image, `make aap-configure` entrypoint.
```

- [ ] **Step 3: Verify MEMORY.md is still under 200 lines**

```bash
wc -l /home/igou/.claude/projects/-workspace-igou-ansible/memory/MEMORY.md
```

Expected: a number well under 200.

---

## Self-review (executed when writing this plan)

**Spec coverage:** Every change in the spec's "Detailed changes" section maps to a task:

- `igou-inventory/group_vars/aap/auth.yml` → Task 1.
- `igou-inventory/group_vars/aap/execution_environments.yml` → Task 2.
- `igou-inventory/group_vars/aap/credentials.yml` → Task 3.
- `igou-ansible/playbooks/aap/ansible-navigator.yml` → Task 4.
- `igou-ansible/playbooks/aap/configure-aap-templates.yml` → Task 5.
- `igou-ansible/Makefile` → Task 6.
- Out-of-band 1Password edit → Task 0 (verification only; actual edit is user-side).
- Validation steps 1–8 from the spec → Tasks 7, 8, 9.

**Placeholder scan:** no `TBD`/`TODO` strings; every step shows the exact commands or file contents.

**Type/name consistency:** `aap_hostname` / `aap_username` / `aap_password` / `aap_validate_certs` used consistently across Tasks 1 and 7+. Image string `quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest` used identically in Tasks 2, 4, and 10. Make target names `aap-configure` / `aap-sync-credentials` / `aap-sync-templates` consistent across Tasks 6, 7, 8, 9, 10.
