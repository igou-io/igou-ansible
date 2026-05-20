---
title: Tekton/PaC build pipeline for igou-aap-ee-rhel9
date: 2026-05-20
status: approved
---

# Tekton/PaC build pipeline for igou-aap-ee-rhel9

## Problem

`execution-environments/igou-aap-ee-rhel9/` has no CI build today. The other three EEs build on GitHub Actions, but the RHEL9 EE needs the entitled Red Hat registry plus an Automation Hub token to fetch certified collections — both of which are painful from public GitHub runners and natural on the OpenShift cluster.

The `igou-openshift` repo already provisions everything needed on the cluster side for this repo to use OpenShift Pipelines via Pipelines-as-Code (PaC). What's missing is the per-EE PipelineRun manifest in *this* repo.

## Goal

Add Tekton manifests under `.tekton/` that, when the repo is mirrored to Forgejo, cause Pipelines-as-Code to build and push `igou-aap-ee-rhel9` to internal Quay on every push to `main` that touches the EE definition.

## Non-goals

- Building the other three EEs (`igou-awx-ee`, `igou-awx-ee-fedora`, `igou-networking-ee`). Their GitHub Actions workflows remain unchanged.
- Adding the missing `ansible.cfg` to `execution-environments/igou-aap-ee-rhel9/`. The user will handle that separately. The pipeline manifests land but the EE won't actually build until `ansible.cfg` is provided.
- Reconciling the AAP version skew (cluster ImageStream is AAP-26, EE pins AAP-25). Flagged for follow-up.
- Modifying anything in `igou-openshift`.

## Reference

Based on [`djdanielsson/rh1-ee`](https://github.com/djdanielsson/rh1-ee/tree/main/.tekton) — a single-EE-at-root layout. Adapted for our multi-EE-under-subdir layout.

## Cluster-side context (already in place — do not modify)

From `clusters/ocp/pac-tenants/values.yaml:15` in `igou-openshift`:

- **Namespace**: `igou-ansible` (PaC tenant, container-builder profile).
- **PaC `Repository` CR**: points at `https://forgejo.apps.ocp.igou.systems/igou-io/igou-ansible`.
- **`ServiceAccount`**: `pipeline-sa`, with:
  - `quay-push-config` (dockerconfigjson, push + pull) — from 1Password `ci-quay-shared`.
  - `rh-automationhub-credentials` (workspace secret, field `token`) — RH SSO offline token for Automation Hub.
- **`ImageStream`**: `ee-minimal-rhel9` pre-imported from `registry.redhat.io/ansible-automation-platform-26/ee-minimal-rhel9:latest`. *(Note: EE pins AAP-25; see Known issues.)*
- **`Repository.spec.params`** (interpolated as PaC `{{ name }}` at trigger time):
  - `ansible_galaxy_server_list = "rh_certified,validated,community"`
  - `ansible_galaxy_server_rh_certified_{url,auth_url,token}` (token from `rh-automationhub-credentials/token`)
  - `ansible_galaxy_server_validated_{url,auth_url,token}` (same token)
  - `ansible_galaxy_server_community_url` (public Galaxy, no auth)
- **Egress NetworkPolicies**: allow 10.10.9.10:443 (apps router) for Quay push + Forgejo clone.

## File layout

```
.tekton/
├── README.md                              # ~30 lines: what's here, link to igou-openshift tenant config
├── igou-aap-ee-rhel9-push.yml             # PipelineRun (PaC trigger)
└── tasks/
    └── ansible-builder-task.yml           # Reusable in-repo Task: runs `ansible-builder create`
```

## PipelineRun: `.tekton/igou-aap-ee-rhel9-push.yml`

### Annotations

```yaml
metadata:
  name: igou-aap-ee-rhel9-push
  annotations:
    pipelinesascode.tekton.dev/on-cel-expression: |
      event == "push" && target_branch == "main" && (
        files.all.exists(f, f.startsWith("execution-environments/igou-aap-ee-rhel9/"))
        || files.all.exists(f, f == "requirements.yml")
      )
    pipelinesascode.tekton.dev/task: ".tekton/tasks/ansible-builder-task.yml"
    pipelinesascode.tekton.dev/max-keep-runs: "3"
```

`git-clone` is fetched via cluster resolver (see pipelineSpec below) — no annotation needed.

### Pipeline-level params

A single list-of-strings param assembled from Repository.params, passed downstream to buildah as `--build-arg`s:

```yaml
spec:
  params:
    - name: ansible_galaxy_environment_variables
      value:
        - "ANSIBLE_GALAXY_SERVER_LIST={{ ansible_galaxy_server_list }}"
        - "ANSIBLE_GALAXY_SERVER_RH_CERTIFIED_URL={{ ansible_galaxy_server_rh_certified_url }}"
        - "ANSIBLE_GALAXY_SERVER_RH_CERTIFIED_AUTH_URL={{ ansible_galaxy_server_rh_certified_auth_url }}"
        - "ANSIBLE_GALAXY_SERVER_RH_CERTIFIED_TOKEN={{ ansible_galaxy_server_rh_certified_token }}"
        - "ANSIBLE_GALAXY_SERVER_VALIDATED_URL={{ ansible_galaxy_server_validated_url }}"
        - "ANSIBLE_GALAXY_SERVER_VALIDATED_AUTH_URL={{ ansible_galaxy_server_validated_auth_url }}"
        - "ANSIBLE_GALAXY_SERVER_VALIDATED_TOKEN={{ ansible_galaxy_server_validated_token }}"
        - "ANSIBLE_GALAXY_SERVER_COMMUNITY_URL={{ ansible_galaxy_server_community_url }}"
```

No `COMMUNITY_TOKEN` / `COMMUNITY_AUTH_URL` — public Galaxy needs neither and they aren't on the Repository CR.

### Workspaces

```yaml
spec:
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: [ReadWriteOnce]
          resources:
            requests:
              storage: 1Gi
```

1Gi matches rh1-ee. Bump later if needed; the tenant `ResourceQuota` allows up to 50Gi requests.storage / 5 PVCs.

### `pipelineSpec` — three serial tasks

```yaml
pipelineSpec:
  workspaces:
    - name: shared-workspace
  tasks:

    - name: fetch-source
      taskRef:
        resolver: cluster
        params:
          - { name: kind,      value: task }
          - { name: name,      value: git-clone }
          - { name: namespace, value: openshift-pipelines }
      params:
        - { name: url,      value: "{{ repo_url }}" }
        - { name: revision, value: "{{ revision }}" }
        - { name: verbose,  value: "true" }
      workspaces:
        - { name: output, workspace: shared-workspace }

    - name: ansible-builder
      runAfter: [fetch-source]
      taskRef:
        name: ansible-builder
      params:
        - { name: FILENAME,        value: "execution-environments/igou-aap-ee-rhel9/execution-environment.yml" }
        - { name: BUILD_CONTEXT,   value: "execution-environments/igou-aap-ee-rhel9/context" }
        - { name: OUTPUT_FILENAME, value: "Containerfile" }
      workspaces:
        - { name: source, workspace: shared-workspace }

    - name: build-image
      runAfter: [ansible-builder]
      matrix:
        params:
          - name: IMAGE
            value:
              - "quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:{{ revision }}"
              - "quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest"
      taskRef:
        resolver: cluster
        params:
          - { name: kind,      value: task }
          - { name: name,      value: buildah }
          - { name: namespace, value: openshift-pipelines }
      params:
        - { name: DOCKERFILE, value: "Containerfile" }
        - { name: CONTEXT,    value: "execution-environments/igou-aap-ee-rhel9/context" }
        - { name: VERBOSE,    value: "true" }
        - { name: BUILD_ARGS, value: "$(params.ansible_galaxy_environment_variables[*])" }
      workspaces:
        - { name: source, workspace: shared-workspace }
```

## Task: `.tekton/tasks/ansible-builder-task.yml`

Identical in structure to `djdanielsson/rh1-ee/.tekton/tasks/ansible-builder-task.yml` — copied verbatim except for the pinned `BUILDER_IMAGE`:

```yaml
apiVersion: tekton.dev/v1beta1
kind: Task
metadata:
  name: ansible-builder
spec:
  description: >-
    Creates a build context (Containerfile + dependencies) from an
    execution-environment.yml spec.
  workspaces:
    - name: source
      description: Source workspace containing the cloned repo.
  params:
    - { name: FILENAME,        type: string, default: "execution-environment.yml" }
    - { name: BUILD_CONTEXT,   type: string, default: "context" }
    - { name: OUTPUT_FILENAME, type: string, default: "Containerfile" }
    - { name: VERBOSITY,       type: string, default: "2" }
    - { name: BUILDER_IMAGE,   type: string, default: "ghcr.io/ansible/community-ansible-dev-tools:v25.9.0" }
  steps:
    - name: ansible-builder-create
      workingDir: $(workspaces.source.path)
      image: $(params.BUILDER_IMAGE)
      script: |
        #!/bin/sh
        set -eux
        ansible-builder create \
          -f "$(params.FILENAME)" \
          -c "$(params.BUILD_CONTEXT)" \
          --output-filename "$(params.OUTPUT_FILENAME)" \
          -v "$(params.VERBOSITY)"
```

Reusable as-is for the other three EEs later: the caller passes `FILENAME` and `BUILD_CONTEXT` per-EE.

## `.tekton/README.md`

Short (~30 lines), covering:

- What this directory is (PaC manifests, triggered by Forgejo mirror).
- Pointer to `igou-openshift/clusters/ocp/pac-tenants/values.yaml` (line 15) for the cluster-side wiring — namespace, ServiceAccount, secrets, Repository CR, Galaxy params.
- How the pipeline runs: PaC → fetch-source → ansible-builder → buildah matrix → push to internal Quay.
- How to add Tekton for the other EEs: copy `igou-aap-ee-rhel9-push.yml`, rename, swap the three `execution-environments/<ee-name>/` paths, swap the image name.
- Known issue: `execution-environments/igou-aap-ee-rhel9/ansible.cfg` is missing — pipeline will fail at `ansible-builder create` until added.

## Triggers

| Event | Branch / Ref | Path filter | Action |
| --- | --- | --- | --- |
| `push` | `main` | `execution-environments/igou-aap-ee-rhel9/**` *or* `requirements.yml` | Build & push `:latest` + `:<sha>` |

No `pull_request`, no tag-based release pipeline. Add later if needed.

## Test plan

Manual, post-merge — there is no local PaC simulator. In order:

1. **Lint locally**: `yamllint .tekton/ && kubeconform -strict .tekton/igou-aap-ee-rhel9-push.yml .tekton/tasks/ansible-builder-task.yml`. (`kubeconform` won't know the PaC `Repository` CRD or Tekton beta APIs, so expect schema-load skips — the assertion is "no parse errors".)
2. **Merge to main** with a no-op touch under `execution-environments/igou-aap-ee-rhel9/` (e.g., whitespace in `execution-environment.yml`).
3. **Observe PipelineRun on the cluster**: `oc -n igou-ansible get pipelinerun -w`. Expect three TaskRuns: `fetch-source` → `ansible-builder` → `build-image` (two matrix instances).
4. **Expected first-run failure** at `ansible-builder` step: `additional_build_files` references `ansible.cfg`, which doesn't exist. This validates the pipeline plumbing is correct; the EE-config gap is a separate task.
5. **Once `ansible.cfg` is added**: re-run. Expect `build-image` matrix to push both `:latest` and `:<sha>` to `quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9`.
6. **Verify image**: `podman pull quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest && podman run --rm <image> ansible --version`.

## Known issues / follow-ups (out of scope)

1. **`ansible.cfg` missing from `execution-environments/igou-aap-ee-rhel9/`** — referenced in `additional_build_files`, blocks builds. User handling separately.
2. **AAP version skew**: cluster ImageStream is AAP-26, EE pins AAP-25. EE wins because `ansible-builder` pulls from `registry.redhat.io` directly, not the ImageStream. Reconcile in a follow-up.
3. **No PR-trigger / no release-tag pipeline** — deliberately minimal. Add when there's a concrete need.
