# Repeatable AAP Configuration via `infra.aap_configuration` — Design

**Date:** 2026-05-21
**Scope:** Make `playbooks/aap/configure-aap*.yml` runnable repeatably against the OCP-deployed AAP instance at `https://automation.apps.ocp.igou.systems`, via `ansible-navigator` using the AAP RHEL9 EE, with auth resolved from 1Password at run time and no shell-side env-var setup required beyond `OP_SERVICE_ACCOUNT_TOKEN`.

## Goal

- One repeatable command shape for AAP configuration: `make aap-configure` (and per-object `make aap-sync-credentials` / `make aap-sync-templates`).
- Auth lands in the play via inventory-resolved 1Password lookups, not shell env state. Operators and CI converge on the same path.
- The new OCP-internal AAP RHEL9 EE (`quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest`) is the execution environment for these runs.
- Inconsistencies discovered during exploration (stale `CONTROLLER_HOST`, deprecated `infra.controller_configuration.dispatch` role reference, placeholder `quay.internal.example.com` EE image, placeholder `internal_quay` credential host) are cleaned up.

## Non-goals

- No new gateway-scope objects (users, teams, RBAC, authenticators, license, settings) — explicitly deferred.
- No new controller-scope objects beyond the 20 templates already curated in `igou-inventory/group_vars/aap/job_templates.yml`.
- No hub or EDA configuration.
- No survey definitions.
- No changes to `igou-inventory/inventory.yaml` host membership or to non-`aap/` group_vars.
- No EE rebuild — the published `quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest` image is treated as a given.
- No TLS plumbing — the gateway route has a signed cert already; `aap_validate_certs: true`.

## Prerequisites (out-of-band, one-time)

- AAP subscription/license attached to the deployed AAP instance.
- 1Password item `aap` in vault `awx` exists with `host`/`username`/`password` fields. The `host` field is **updated** to `https://automation.apps.ocp.igou.systems` (previously a stale SNO/hub URL). This same item is the source for both the new `group_vars/aap/auth.yml` lookup and the existing `controller_credentials.aap_admin` entry — one source of truth.
- `OP_SERVICE_ACCOUNT_TOKEN` exported in the operator's shell (standard repo pattern).
- `ANSIBLE_INVENTORY` exported in the operator's shell pointing at `igou-inventory/inventory.yaml` (the Makefile targets check this and fail fast if unset).
- The OCP-internal registry route `quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest` is anonymously pullable for this image (confirmed during scoping).

## Architecture

Two repos, both edited. `igou-inventory` owns AAP connection vars and AAP object definitions; `igou-ansible` owns playbooks, EE selection, and operator entry points.

```
Operator shell                                                              OCP cluster
─────────────                                                              ─────────────
$ make aap-configure                                                       gateway
  │                                                                        ↑
  │  ANSIBLE_NAVIGATOR_CONFIG=playbooks/aap/ansible-navigator.yml          │ HTTPS, signed cert
  │  ANSIBLE_INVENTORY=…/igou-inventory/inventory.yaml                     │
  ▼                                                                        │
  ansible-navigator run playbooks/aap/configure-aap.yml                    │
  │                                                                        │
  ├── per-playbook navigator config                                        │
  │     image: quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest │
  │     pass: OP_SERVICE_ACCOUNT_TOKEN                                     │
  │                                                                        │
  ▼                                                                        │
  EE container (AAP RHEL9 EE)                                              │
  │                                                                        │
  ├── group_vars/aap/auth.yml                                              │
  │     aap_hostname/username/password ← 1Password "aap" item              │
  │     aap_validate_certs: true                                           │
  │                                                                        │
  ├── group_vars/aap/* (existing per-object files)                         │
  │     controller_* and aap_organizations object trees                    │
  │                                                                        │
  └── infra.aap_configuration.dispatch ─────────────────────────────────────►
```

## Detailed changes

### `igou-inventory` — 1 new file, 2 edits

#### NEW `group_vars/aap/auth.yml`

```yaml
---
# Connection to the OCP-deployed AAP gateway. Resolved at run time from the
# `aap` item in 1Password vault `awx`. `OP_SERVICE_ACCOUNT_TOKEN` must be
# exported in the navigator runner env.
aap_hostname: "{{ lookup('community.general.onepassword', 'aap', field='host', vault='awx') }}"
aap_username: "{{ lookup('community.general.onepassword', 'aap', field='username', vault='awx') }}"
aap_password: "{{ lookup('community.general.onepassword', 'aap', field='password', vault='awx') }}"
aap_validate_certs: true
```

Notes:

- File sits alongside the other per-object files; Ansible auto-loads every `*.yml` under `group_vars/aap/`.
- `aap_*` (not `controller_*`) are the canonical AAP 2.5 names that `infra.aap_configuration` consumes natively for connection.
- The legacy `CONTROLLER_HOST` / `CONTROLLER_USERNAME` / `CONTROLLER_PASSWORD` env vars become unused for AAP plays. They remain in the repo-root `ansible-navigator.yml`'s env-pass list (harmless for non-AAP plays that don't reference them), and are deliberately omitted from the new `playbooks/aap/ansible-navigator.yml` pass list. The stale `automation.hub.sno.igou.systems` value the operator may still have exported stops mattering.
- `group_vars/aap/organization.yml` already pins `ansible_connection: local` for the group — preserved as the [[feedback_localhost_connection]] exception for the stub `aap_host`.

#### EDIT `group_vars/aap/execution_environments.yml`

Repoint the `igou-aap-ee-rhel9` EE record at the real registry and drop the credential reference (the image is anonymously pullable from `quay.apps.ocp.igou.systems`).

```diff
 - name: igou-aap-ee-rhel9
-  image: quay.internal.example.com/igou/igou-aap-ee-rhel9:latest
+  image: quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest
   pull: always
   organization: igou
-  description: Igou supported EE (internal quay — placeholder host, update before applying)
-  credential: internal_quay
+  description: Igou supported EE (built by Tekton, pushed to OCP internal registry)
```

#### EDIT `group_vars/aap/credentials.yml`

Keep the `internal_quay` credential entry but repoint its host so it isn't misleading. No active consumer references it after the EE-record edit above; it remains for future-use scenarios (e.g., if an authenticated mirror is added later).

```diff
 - name: internal_quay
   credential_type: Container Registry
   organization: igou
-  description: Internal quay registry (placeholder host — update before applying)
+  description: Internal OCP registry — placeholder credential (image pull is anonymous; kept for future-use scenarios)
   inputs:
-    host: "https://quay.internal.example.com"
+    host: "https://quay.apps.ocp.igou.systems"
     username: "{{ lookup('community.general.onepassword', 'internal-quay', field='username', vault='awx') }}"
     password: "{{ lookup('community.general.onepassword', 'internal-quay', field='password', vault='awx') }}"
     verify_ssl: true
```

### `igou-ansible` — 2 new files, 1 edit

#### NEW `playbooks/aap/ansible-navigator.yml`

Per-playbook navigator config, selected via `ANSIBLE_NAVIGATOR_CONFIG=playbooks/aap/ansible-navigator.yml` (set by the Makefile targets). Navigator reads exactly one config; the repo-root `ansible-navigator.yml` is untouched and non-AAP plays keep using `igou-awx-ee`.

```yaml
---
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

Notes:

- Pass-through env list is intentionally minimal — only `OP_SERVICE_ACCOUNT_TOKEN`. Auth values come from the 1Password lookup in `group_vars/aap/auth.yml`, not env.
- `pull.policy: missing` so the EE image is fetched once and reused. Bump to `always` for fresh-pull-per-run if drift becomes an issue.
- `ANSIBLE_INVENTORY` does not need to be in the env-pass list; `ansible-navigator` reads it on the host side to locate inventory before mounting into the EE.

#### NEW `Makefile` (repo root)

There is no repo-root `Makefile` in `igou-ansible` today. New file:

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

#### EDIT `playbooks/aap/configure-aap-templates.yml`

Swap the role reference from the deprecated collection name to the consolidated one, matching the other two AAP playbooks.

```diff
-      name: infra.controller_configuration.dispatch
+      name: infra.aap_configuration.dispatch
```

## Out-of-band

| Change | Item / Vault |
| ---- | ---- |
| EDIT | 1Password item `aap` in vault `awx` — update `host` field to `https://automation.apps.ocp.igou.systems` |

## Validation

In order, smallest blast radius first:

1. `yamllint igou-inventory/group_vars/aap/auth.yml` clean.
2. `yamllint playbooks/aap/ansible-navigator.yml` clean.
3. With `OP_SERVICE_ACCOUNT_TOKEN` and `ANSIBLE_INVENTORY` exported: `ansible-inventory -i $ANSIBLE_INVENTORY --host aap_host | jq -r .aap_hostname` returns `https://automation.apps.ocp.igou.systems`. Proves the 1Password lookup resolves and `auth.yml` is loaded.
4. `make aap-sync-credentials` — smallest blast radius. If credentials reconcile cleanly, gateway auth and EE pull both work.
5. `make aap-sync-templates` — confirms the renamed `infra.aap_configuration.dispatch` role applies on the templates branch.
6. `make aap-configure` — full dispatch.
7. Re-run `make aap-configure` immediately: zero changes (idempotent).
8. In the AAP UI, the `igou-aap-ee-rhel9` Execution Environment record shows image `quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest` and no credential attached.

If validation fails:

- 401 in step 4 → 1Password `aap` item still has the old host or wrong password.
- 403 license/subscription in step 4 → subscription not attached (out-of-band prerequisite).
- "role not found" in step 5 → `infra.aap_configuration` missing from the published EE image (image stale; rebuild via `.tekton/igou-aap-ee-rhel9-push.yml`).

## File summary

`igou-inventory`:

| Change | Path |
| ---- | ---- |
| NEW | `group_vars/aap/auth.yml` |
| EDIT | `group_vars/aap/execution_environments.yml` |
| EDIT | `group_vars/aap/credentials.yml` |

`igou-ansible`:

| Change | Path |
| ---- | ---- |
| NEW | `playbooks/aap/ansible-navigator.yml` |
| NEW | `Makefile` |
| EDIT | `playbooks/aap/configure-aap-templates.yml` |

1Password (vault `awx`):

| Change | Item |
| ---- | ---- |
| EDIT | `aap` — update `host` field |
