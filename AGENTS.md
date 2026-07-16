# AGENTS.md

This file provides guidance to AI Agents when working with code in this repository.

## Project Overview

Ansible automation framework for homelab and production infrastructure. Playbooks are primarily executed via AAP or ansible-navigator with containerized execution environments (EEs). Inventory lives in a separate repo (`igou-inventory`, symlinked).

## Common Commands

### Running playbooks

```bash
# Via ansible-navigator (uses podman + EE container)
ansible-navigator run playbooks/<domain>/<playbook>.yaml -i igou-inventory/inventory.yaml
```

### Linting

```bash
make lint
make yamllint
make syntax-check
```

### Molecule testing

```bash
# Run a specific scenario
molecule test -s <scenario-name>
```

### Building execution environments

```bash
make ee
```

## Development

When writing a new role, use the skill `ansible-scaffold-role`

When creating a new playbook or other content, use the skill `write-content` and `write-content-tests`

If you are being building a new collection, that will live as its own project under the david-igou github account. Scaffold it into the /workspace directory using the skill

## Architecture

### Playbook organization

Playbooks are organized by infrastructure domain under `playbooks/`:
- `kubernetes/` - k3s bootstrap, cluster config, service accounts
- `openshift/` - cluster operations, virtualization
- `kubevirt/` - VM deployment and management
- `truenas/` - storage configuration
- `terraform/` - plan/apply workflows
- `linux/`, `rhel/` - system-level operations
- `windows/` - Windows host automation (WinRM/SSH): app provisioning, users, updates, IIS, AD join
- `aap/` - automation platform configuration
- `armbian/` - ARM SBC fleet lifecycle (image build, provisioning, boot modes)
- Root-level playbooks for common ops (system-update, system-reboot)

Many playbooks use `ansible_limit` variable for dynamic host targeting;
`playbooks/armbian/` uses `target_hosts` instead. AAP template extra_vars
must match the playbook's hosts var or the play silently matches no hosts.

### ARM SBC boot modes

The `boards` fleet boots via per-MAC pxelinux pins on rb5009 with four
modes: `local_kernel` (fleet default — kernel from local disk, updated
via apt), `nfs` (reimaging), `sd` (bring-up), `local` (transitional).
**When working with the fleet, read `playbooks/armbian/AGENTS.md` first** —
it covers boot modes, playbooks/job templates, workflows, and per-board
quirks, and points to the authoritative `docs/armbian-boot-modes.md`.

### Execution environments

Built with `ansible-builder`, defined in `execution-environments/`. Each has `execution-environment.yml` + supporting files. CI builds publish the images when their definitions change.

- `igou-awx-ee` - Primary EE (CentOS Stream 10, includes terraform, 1password-cli, oc, helm, kustomize)
- `igou-aap-ee-rhel9` - AAP on RHEL9

### Roles

Mix of community galaxy roles (pinned versions in `requirements.yml`) and custom roles in `roles/`. Custom roles include `kubevirt_create_datavolume`, `kubevirt_provision_survey`, `kubevirt_vm_launch`, `lvm`, `raid`.

**Roles are pure functions.** A role is pure over its declared inputs, with
`meta/argument_specs.yml` as its signature. Do **not** put secret lookups
(`community.general.onepassword`, etc.) inside role tasks — that creates an
untested production path, makes a 1Password outage indistinguishable from a
real role failure (worse under `no_log`), and hard-couples the role to one
secret backend. Resolved credential values are passed in as `no_log`
suboptions; the `op://` lookup expressions live as *values* in inventory
`group_vars` (e.g. `igou-inventory/group_vars/rustfs.yml`).

### Molecule testing

Scenarios in `molecule/` follow a `<type>-<subject>[-<qualifier>]` naming scheme — `playbook-`, `role-`, or `logic-` (localhost-only) prefixes. Backends are never encoded in the name; they are selected at runtime via `mp_backend`/`PROVISIONER` and matrixed in CI. Provisioning is delegated to the `david_igou.molecule_provisioners` collection. Each scenario is self-contained (no cross-scenario shared plumbing): the sysprep-based Windows scenarios each carry their own copy of `windows-sysprep-secrets.yml` + `templates/windows-unattend.xml.j2`, deliberately un-DRY so a scenario is easy to follow in isolation. Environment variable templating is used extensively for flexibility.

### Inventory (separate repo)

`igou-inventory/` contains `inventory.yaml` with groups: `metal`, `rk8s` (with `rk8s_control_plane` / `rk8s_workers` children), `openshift_clusters`, `openshift_workers_ocp`, `aap`, `truenas`, `routeros`, `armbian`/`boards`, `netboot_server`. Group/host vars in `group_vars/` and `host_vars/`.

## Code Style

- YAML files must start with `---`
- 2-space indentation, indent sequences
- Use YAML 1.2 booleans only (`true`/`false`, not `yes`/`no`)
- No line-length limit enforced
- Block style only — no JSON-style flow maps/sequences (`metadata: { name: x }`, `disks: [ { ... } ]`); expand every content-bearing map/sequence to multi-line block form. Reserve `{}`/`[]` for genuinely empty values (`rng: {}`)
- ansible-lint production profile applies
- Vault-encrypted files are named `vault.yml`

## CI/CD

GitHub Actions workflows build EE container images. Reusable workflow in `.github/workflows/ee-build.yml`. Triggered on path changes to `execution-environments/` dirs or weekly on Sunday. Renovate auto-merges dependency updates in `requirements.yml`.
