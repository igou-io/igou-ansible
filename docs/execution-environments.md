# Execution environments

Custom container images that bundle ansible-core + collections + system
dependencies. Built with `ansible-builder`, pushed to `quay.io/igou/`, used
by `ansible-navigator` (and AWX/AAP) to run playbooks in a reproducible env.

Defined under `execution-environments/<name>/execution-environment.yml`.

## What each EE is for

| EE | Base image | Use case |
|---|---|---|
| `igou-awx-ee` | `quay.io/centos/centos:stream10-minimal` | **Default** for AWX. Bundles `terraform`, `1password-cli`, `oc`, `helm`, `kustomize`, the full `requirements.yml` (community.docker, community.routeros, kubernetes.core, etc.). Use for nearly everything. |
| `igou-aap-ee-rhel9` | `registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel9` | For Red Hat AAP / Controller (subscription required to pull). Smallest, most upstream-faithful. |

## When to rebuild

- A pinned package in `execution-environment.yml` updated (renovate-bot opens
  a PR — `centos`, `ansible-core==X.Y.Z`, etc.).
- `requirements.yml` at repo root changed (galaxy roles/collections).
- A new tool needs to be available system-wide inside the EE.
- Periodically — the `igou-awx-ee` GitHub Actions workflow rebuilds it weekly
  on Sunday.

## Rebuild manually (locally)

```bash
cd execution-environments/<ee-name>/
ansible-builder build --tag quay.io/igou/<ee-name>:latest
podman push quay.io/igou/<ee-name>:latest
```

The base image must be available — for `igou-aap-ee-rhel9`, you'll need
`podman login registry.redhat.io` first (subscription credentials).

## Rebuild via CI

The reusable workflow at `.github/workflows/ee-build.yml` is invoked by:

- `igou-awx-ee-build.yml`

It runs on pushes to `execution-environments/igou-awx-ee/**` and on a weekly cron.
Push to quay requires the `QUAY_PASSWORD` and `QUAY_USERNAME` secrets and the
`AH_TOKEN` secret (Ansible Automation Hub for `redhat.*` collections).

The AAP EE is built by `.tekton/igou-aap-ee-rhel9-push.yml` on OpenShift, where
the entitled base image and Automation Hub collections are available.

## Pointing ansible-navigator at a specific EE

`ansible-navigator.yml` at the repo root sets the default. Override per-run:

```bash
ansible-navigator run playbooks/foo.yml \
  --execution-environment-image quay.io/igou/igou-awx-ee:latest \
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
