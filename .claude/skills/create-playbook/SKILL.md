---
name: create-playbook
description: Use when the user asks to add, scaffold, or create a new Ansible playbook in this repo (e.g. "/create-playbook reboot all worker nodes"). Argument is the playbook's purpose. Gathers target hosts, roles/collections, execution environment, and whether to register it as AAP config-as-code; writes the playbook under playbooks/<domain>/, updates requirements.yml, optionally appends a controller_templates entry to the symlinked igou-inventory repo, and runs syntax-check + ansible-lint before declaring success.
---

# create-playbook

Scaffold a new playbook that matches this repo's conventions. The argument passed to the skill is a short free-text description of what the playbook should do.

## Conventions you must follow

- Playbook files live under `playbooks/<domain>/<name>.yaml` (use `.yaml`, not `.yml`).
- Existing domains: `aap/`, `kubernetes/`, `kubevirt/`, `openshift/`, `openshift_virtualization/`, `linux/`, `rhel/`, `letsencrypt/`, `netboot/`, `routeros/`, `tailscale/`, `terraform/`, `truenas/`, `examples/`. Root-level playbooks exist for cross-cutting ops (`system-update.yaml`, `system-reboot.yaml`). Prefer an existing domain; only propose a new one if nothing fits.
- File starts with `---`. Use 2-space indent. YAML 1.2 booleans only (`true` / `false`).
- For node-targeted plays, default `hosts:` to `"{{ ansible_limit | default('all') }}"` so AAP `ansible_limit` works. For cluster-API plays (kubevirt, AAP config, k8s API calls), use `hosts: localhost`.
- Never pin `ansible_connection: local` on inventory `localhost`. If the play must run locally, set `connection: local` at the play level — see memory `feedback_localhost_connection`.
- Roles and collections go in `/workspace/igou-ansible/requirements.yml` (pinned version). Custom in-repo roles live under `roles/`.
- Execution environments: `igou-awx-ee` (default for general Linux work), `igou-awx-ee-fedora`, `igou-aap-ee-rhel9` (AAP-side config-as-code), `igou-networking-ee` (RouterOS / network work). Plays run via `ansible-navigator` typically don't set EE in the playbook itself — the EE is selected per job_template in AAP.
- AAP job templates are config-as-code in the separate `igou-inventory` repo at `igou-inventory/group_vars/aap/job_templates.yml` (symlinked in this workspace). Each entry follows the schema visible in that file.

## Procedure

### 1. Resolve the description

The skill argument (everything after `/create-playbook`) is the description. If it is empty or one word, ask the user to expand it in one sentence before proceeding. Otherwise summarize it back in one line so the user can correct course early.

### 2. Propose name + domain, then gather choices

Suggest a `<domain>/<filename>.yaml` based on the description. Then issue **one** `AskUserQuestion` call (4 questions max, so use exactly these four):

1. **Domain/path** — propose the inferred path as option 1; offer up to 3 plausible alternates from the existing domain list.
2. **Target hosts** —
   - `"{{ ansible_limit | default('all') }}"` (AAP-friendly node play, recommended for most cases)
   - `localhost` (cluster-API / cloud-API / local tooling play)
   - A specific inventory group (offer 2–3 likely groups: `rk8s`, `metal`, `truenas`, etc., picking based on the description)
   - Custom (user will type it)
3. **Execution environment** — `igou-awx-ee`, `igou-awx-ee-fedora`, `igou-aap-ee-rhel9`, `igou-networking-ee`. Recommend based on description (RouterOS → networking, AAP config → rhel9, default → awx-ee).
4. **Register in AAP config-as-code?** — `yes, append controller_templates entry to igou-inventory/group_vars/aap/job_templates.yml` / `no, just write the playbook`.

If the user picks "custom" hosts or a non-listed domain, follow up with a plain-text prompt for the value.

### 3. Identify roles and collections

Re-read the description with the chosen domain in mind and propose a list of roles/collections the playbook will likely need. Check `requirements.yml` to see which are already pinned. Present a plain-text proposal like:

```
Proposed dependencies:
  Already in requirements.yml: kubernetes.core, kubevirt.core
  To add to requirements.yml:
    - collection: community.crypto (version: TBD)
    - role: geerlingguy.docker (version: TBD)
  In-repo roles to reuse: lvm
```

Ask the user to confirm, edit, or strike entries. Resolve TBD versions by checking Galaxy (`ansible-galaxy collection info <name>` or `ansible-galaxy role info <name>`) — pin to the latest stable release matching the style already in `requirements.yml`.

### 4. Draft and show the playbook before writing

Compose the playbook body. Standard skeleton for a node play:

```yaml
---
- name: <Title case description>
  hosts: "{{ ansible_limit | default('all') }}"
  become: true
  gather_facts: true
  roles:
    - role: <role.name>
```

For a localhost/API play, omit `become`, use `gather_facts: false` unless facts are needed, and put logic in `tasks:` (see `playbooks/kubevirt/deploy_vm_raw.yaml` for the shape).

Show the user the full proposed playbook content, the proposed `requirements.yml` additions (as a diff), and — if AAP=yes — the proposed `controller_templates` entry. Ask "looks right?" Iterate inline until they approve. Do not write files until they approve.

### 5. Write the files

- Write `playbooks/<domain>/<filename>.yaml`.
- Append to `requirements.yml` if dependencies were added. Match the existing block style (roles in the `roles:` list, collections in the `collections:` list, version pinned as a string).
- If AAP=yes: append a new entry to `igou-inventory/group_vars/aap/job_templates.yml` matching the existing schema. Required fields: `name`, `description`, `labels`, `project: igou_ansible`, `job_type: run`, `playbook` (the path you just wrote), `inventory: igou_inventory`, `execution_environment` (from step 2), `ask_variables_on_launch: true`, `credentials` (suggest `ansible_user_ed25519` for node plays, `onepassword` + `aap_admin` for AAP plays — ask if unsure). Use the kebab-case-with-underscores convention (`system_update`, `aap_configure_all`).

### 6. Verify before declaring success

Run, from `/workspace/igou-ansible`:

```bash
ansible-playbook --syntax-check playbooks/<domain>/<filename>.yaml
ansible-lint --profile=production playbooks/<domain>/<filename>.yaml
```

Both must exit 0. If lint fails, fix the playbook and re-run — do not hand back lint errors for the user to fix. Per `superpowers:verification-before-completion` the rule is evidence before assertions: show the user the actual command output, do not just claim it passed.

### 7. Report

End with a concise summary:

- File(s) written, with paths.
- Which repos have uncommitted changes (`igou-ansible` always; `igou-inventory` if AAP=yes — call this out explicitly, it's a separate repo).
- Verification output (one line each: "syntax-check: OK", "ansible-lint: OK").
- Suggested next step (e.g. "run `make aap-sync-templates` to push the new job template" if AAP=yes, or "run via `ansible-navigator run playbooks/<path> -i igou-inventory/inventory.yaml`" otherwise).

Do **not** commit on the user's behalf in either repo. Do not run the playbook. Do not push.

## Anti-patterns

- Writing the file before the user approves the draft.
- Skipping the lint step "to save time" — the production profile is what pre-commit runs, so a skipped lint just defers the failure.
- Adding `ansible_connection: local` to an inventory entry to make a localhost play work. Put `connection: local` at the play level instead.
- Picking an EE that is not one of the four listed names. New EEs need to be built and registered separately, that is not in this skill's scope.
- Editing `igou-inventory/` without telling the user it is a separate repo with its own git history.
- Inventing a new top-level domain when an existing one (e.g. `linux/`, `rhel/`) fits.
