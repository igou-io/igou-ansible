# `.tekton/` — OpenShift Pipelines manifests

This directory holds [Pipelines-as-Code](https://pipelinesascode.com/) (PaC) manifests. PaC watches the Forgejo mirror of this repo (`https://forgejo.apps.ocp.igou.systems/igou-io/igou-ansible`) and applies any matching `PipelineRun` it finds under `.tekton/`.

## Cluster-side wiring

The namespace, ServiceAccount, secrets, ImageStream, `Repository` CR, and `Repository.spec.params` (galaxy server URLs + AH token) all live in `igou-openshift` at `clusters/ocp/pac-tenants/values.yaml` (look for `name: igou-ansible`). Do not duplicate that config here.

## What runs today

| File | Trigger | Builds |
| --- | --- | --- |
| `igou-aap-ee-rhel9-push.yml` | push to `main` touching `execution-environments/igou-aap-ee-rhel9/execution-environment.yml` or `…/requirements.txt` | `quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:{revision,latest}` |

Pipeline flow (all three EE pipelines, when added, follow the same shape):

```
git-clone (cluster resolver)
  -> ansible-builder (in-repo Task, .tekton/tasks/ansible-builder-task.yml)
  -> buildah (cluster resolver, matrix over image tags)
```

## Adding Tekton for another EE

1. Copy `igou-aap-ee-rhel9-push.yml` to `<ee-name>-push.yml`.
2. Rename the `metadata.name`.
3. Update the two file paths in `pipelinesascode.tekton.dev/on-path-change`.
4. Update `FILENAME`, `BUILD_CONTEXT`, and the buildah `CONTEXT` params (three `execution-environments/<ee-name>/…` paths).
5. Update the image name in the `build-image` matrix.

The in-repo Task (`tasks/ansible-builder-task.yml`) is reusable as-is.

## Known gaps

- `execution-environments/igou-aap-ee-rhel9/ansible.cfg` is referenced from the EE's `additional_build_files` but missing from the directory. The pipeline plumbing is correct, but the build fails at the `ansible-builder` step until that file is added.
- Cluster `ImageStream` is `ansible-automation-platform-26/ee-minimal-rhel9:latest`; the EE pins AAP-25. The EE wins (it pulls direct from `registry.redhat.io`). Reconcile in a follow-up.
