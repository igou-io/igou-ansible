# Execution environments

Custom container images that bundle ansible-core + collections + system
dependencies. Built with `ansible-builder`, pushed to `quay.io/igou/`, used
by `ansible-navigator` (and AWX/AAP) to run playbooks in a reproducible env.

Defined under `execution-environments/<name>/execution-environment.yml`.

## What each EE is for

| EE | Base image | Use case |
|---|---|---|
| `igou-awx-ee` | `quay.io/centos/centos:stream10-minimal` | **Default** for AWX. Bundles `terraform`, `1password-cli`, `oc`, `helm`, `kustomize`, the full `requirements.yml` (community.docker, community.routeros, kubernetes.core, etc.). Use for nearly everything. |
| `igou-awx-ee-fedora` | `quay.io/fedora/fedora:44` | Fedora-base variant of the above. Tracks bleeding-edge Python (3.14) and system libs. Used when CentOS Stream lags upstream. |
| `igou-aap-ee-rhel9` | `registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel9` | For Red Hat AAP / Controller (subscription required to pull). Smallest, most upstream-faithful. |
| `igou-networking-ee` | `quay.io/centos/centos:stream9-minimal` | **Legacy-friendly**. Pinned `community.general@10.2.0` and `ansible.netcommon@7.1.0`; legacy SSH crypto enabled to talk to old switches/routers. Use when a device refuses modern KEX or cipher algos. |

## When to rebuild

- A pinned package in `execution-environment.yml` updated (renovate-bot opens
  a PR — `quay.io/fedora`, `centos`, `ansible-core==X.Y.Z`, etc.).
- `requirements.yml` at repo root changed (galaxy roles/collections).
- A new tool needs to be available system-wide inside the EE.
- Periodically — the GitHub Actions workflow rebuilds them weekly on Sunday.

## Rebuild manually (locally)

```bash
cd execution-environments/<ee-name>/
ansible-builder build --tag quay.io/igou/<ee-name>:latest
podman push quay.io/igou/<ee-name>:latest
```

The base image must be available — for `igou-aap-ee-rhel9`, you'll need
`podman login registry.redhat.io` first (subscription credentials).

## Rebuild via CI

The reusable workflow at `.github/workflows/ee-build.yml` is invoked by per-EE
workflows:

- `igou-awx-ee-build.yml`
- `igou-awx-ee-fedora-build.yml`
- `igou-networking-ee-build.yml`

Each runs on push to `execution-environments/<ee>/**` and on a weekly cron.
Push to quay requires the `QUAY_PASSWORD` and `QUAY_USERNAME` secrets and the
`AH_TOKEN` secret (Ansible Automation Hub for `redhat.*` collections).

The AAP EE doesn't have a CI workflow because the base image requires a
subscription that GitHub Actions runners don't have.

## Pointing ansible-navigator at a specific EE

`ansible-navigator.yml` at the repo root sets the default. Override per-run:

```bash
ansible-navigator run playbooks/foo.yml \
  --execution-environment-image quay.io/igou/igou-networking-ee:latest \
  -i igou-inventory/inventory.yaml
```

## Common breaks

- **`AH_TOKEN` expired** → AAP EE build fails on `ansible-galaxy collection
  install` of `redhat.*` collections. Refresh the token in Ansible Automation
  Hub and update the GitHub repo secret.
- **`renovate/ee-container-images` PR fails** → usually a renovate digest
  pin to a moving tag; re-run the workflow or merge the next renovate PR.
- **`syntax-check` workflow runs against an EE-bumped PR** → the syntax-check
  failure is unrelated to the EE update; merge the EE PR anyway (the
  `Lint` workflow is the one that proves the EE works).

## Cross-reference

- Tools baked into `igou-awx-ee` are listed in `~/.claude/CLAUDE.md` (the
  global one) under "Available Tools." If you need a tool added, edit the
  EE's `execution-environment.yml` and rebuild.
