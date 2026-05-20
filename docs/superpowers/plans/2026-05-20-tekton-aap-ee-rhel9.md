# Tekton/PaC build pipeline for igou-aap-ee-rhel9 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `.tekton/` manifests so `igou-aap-ee-rhel9` builds via OpenShift Pipelines + Pipelines-as-Code (PaC) when files in that EE's directory change on `main`. The repo is mirrored to Forgejo; PaC picks up `.tekton/` from the mirror.

**Architecture:** Three files under `.tekton/`:
1. A reusable in-repo `Task` that runs `ansible-builder create` (writes a `Containerfile` into a context dir).
2. A `PipelineRun` (PaC-triggered) that wires three serial tasks: `git-clone` (cluster resolver) → `ansible-builder` (our in-repo Task) → `buildah` (cluster resolver, matrix over two image tags).
3. A short README explaining what's here and how the cluster-side wiring lives in `igou-openshift`.

PaC trigger uses `on-path-change` with two exact paths under the EE dir. Galaxy auth flows through PaC `{{ }}` interpolation from `Repository.spec.params` already declared on the cluster.

**Tech Stack:** Tekton Pipelines (`tekton.dev/v1` PipelineRun, `tekton.dev/v1beta1` Task), OpenShift Pipelines `buildah` + `git-clone` cluster-resolver tasks, Pipelines-as-Code (Forgejo via the gitea provider), `ghcr.io/ansible/community-ansible-dev-tools:v25.9.0` builder image, yamllint for local validation.

---

## Reference material

- **Spec:** `docs/superpowers/specs/2026-05-20-tekton-aap-ee-rhel9-design.md` — full design, the source of truth for what every file must contain.
- **Upstream reference:** `djdanielsson/rh1-ee` at `.tekton/{push.yml,release.yml,tasks/ansible-builder-task.yml}`. We adapt the same pattern for our multi-EE subdir layout.
- **Cluster-side wiring (already in place, do not modify):** `/workspace/igou-openshift/clusters/ocp/pac-tenants/values.yaml` line 15 onward — namespace `igou-ansible`, `pipeline-sa`, `quay-push-config`, `rh-automationhub-credentials`, the `ee-minimal-rhel9` ImageStream, and the `Repository.spec.params` (`ansible_galaxy_server_*`).
- **PaC `on-path-change` annotation docs:** `openshift-pipelines/pipelines-as-code` repo, `docs/content/docs/guides/event-matching/path-matching.md`. Globs are gobwas/glob, exact paths match exact paths.
- **PaC Forgejo support:** confirmed via `pkg/provider/gitea/gitea.go:588` — `files.{all,added,modified,deleted,renamed}` is populated, but our pipeline uses `on-path-change` so we don't depend on CEL.
- **Existing EE this builds:** `execution-environments/igou-aap-ee-rhel9/execution-environment.yml` (AAP-25 base) + `execution-environments/igou-aap-ee-rhel9/requirements.txt`.

## Starting state

Current `igou-ansible` working tree (on `main`): clean except for two design/plan docs being authored (`docs/superpowers/specs/2026-05-20-tekton-aap-ee-rhel9-design.md` already committed, this plan file). The `.tekton/` directory does not exist yet.

`/workspace/igou-openshift` PaC tenant for `igou-ansible` is already deployed and synced — no changes needed there. Verify on the cluster with `oc -n igou-ansible get repository,sa,pvc,imagestream,secret 2>/dev/null | head` before merging this PR if you want a sanity check, but that's optional.

## Conventions

- **Run from:** `/workspace/igou-ansible`.
- **Branch:** `tekton/aap-ee-rhel9-build`.
- **File mode:** plain text, LF line endings, trailing newline (pre-commit's `end-of-file-fixer` enforces this).
- **YAML style:** `---` document marker at top, 2-space indent, **block style for all maps and sequences** — no `{ key: value }` or `[a, b]` flow syntax inside our manifests. (PaC's own annotations like `on-event: "[push]"` are an exception — that bracketed-list-as-string form is the documented PaC annotation value syntax, not YAML flow.)
- **Quoting:** quote any string containing `{{ }}` (PaC templates), `$(...)` (Tekton var refs), or a colon/comma so the YAML parser doesn't choke. Block strings (`|` or `>`) for multi-line scripts.
- **API versions:** `apiVersion: tekton.dev/v1` for `PipelineRun`, `apiVersion: tekton.dev/v1beta1` for `Task`. Match rh1-ee exactly.
- **Commit cadence:** one commit per file added — three commits before the local-lint commit + push.
- **Out of scope (do NOT touch in this PR):**
  - `execution-environments/igou-aap-ee-rhel9/ansible.cfg` (missing, user handles separately).
  - The other three EEs.
  - Anything in `/workspace/igou-openshift`.

## File structure

```
.tekton/
├── README.md                              # ~30 lines, plain Markdown
├── igou-aap-ee-rhel9-push.yml             # PipelineRun, PaC-triggered
└── tasks/
    └── ansible-builder-task.yml           # in-repo Task, runs ansible-builder create
```

No other files change.

---

## Task 1: Branch and scaffold the `.tekton/` directory

**Files:**
- Create directory: `.tekton/tasks/`

- [ ] **Step 1: Confirm clean working tree on `main`**

Run:
```bash
git status --short
git rev-parse --abbrev-ref HEAD
```
Expected: only untracked `docs/superpowers/plans/2026-05-20-tekton-aap-ee-rhel9.md` (this file) and `.claude/`. Branch is `main`. If there are unrelated uncommitted edits, stop and ask the user before proceeding.

- [ ] **Step 2: Create feature branch**

Run:
```bash
git checkout -b tekton/aap-ee-rhel9-build
```
Expected: `Switched to a new branch 'tekton/aap-ee-rhel9-build'`.

- [ ] **Step 3: Create the `.tekton/tasks/` directory**

Run:
```bash
mkdir -p .tekton/tasks
ls -la .tekton/
```
Expected: `.tekton/` exists, contains `tasks/` subdirectory. No commit yet — empty dirs aren't tracked.

---

## Task 2: Create the in-repo `ansible-builder` Task

**Files:**
- Create: `.tekton/tasks/ansible-builder-task.yml`

- [ ] **Step 1: Write the Task manifest**

Create `.tekton/tasks/ansible-builder-task.yml` with this exact content:

```yaml
---
# yaml-language-server: $schema=https://www.schemastore.org/api/json/catalog.json
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
    - name: FILENAME
      description: Execution environment file definition.
      type: string
      default: "execution-environment.yml"
    - name: BUILD_CONTEXT
      description: Execution environment build context.
      type: string
      default: "context"
    - name: OUTPUT_FILENAME
      description: Name of file to write image definition to (Dockerfile or Containerfile).
      type: string
      default: "Containerfile"
    - name: VERBOSITY
      description: ansible-builder output verbosity.
      type: string
      default: "2"
    - name: BUILDER_IMAGE
      description: The location of the ansible-builder image.
      type: string
      default: "ghcr.io/ansible/community-ansible-dev-tools:v25.9.0"
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

- [ ] **Step 2: Validate with yamllint**

Run:
```bash
yamllint .tekton/tasks/ansible-builder-task.yml
```
Expected: no output (clean). If yamllint complains about indentation or trailing whitespace, fix and re-run. The repo's `.yamllint` extends the default profile; document-start (`---`) is required (already in the file).

- [ ] **Step 3: Verify the manifest parses as valid Kubernetes-ish YAML**

Run:
```bash
python3 -c "import sys, yaml; doc = yaml.safe_load(open('.tekton/tasks/ansible-builder-task.yml')); assert doc['apiVersion'] == 'tekton.dev/v1beta1' and doc['kind'] == 'Task' and doc['metadata']['name'] == 'ansible-builder' and len(doc['spec']['params']) == 5 and len(doc['spec']['steps']) == 1; print('OK')"
```
Expected: `OK`. (kubeconform would skip Tekton CRDs without a schema bundle, so this is the cheap structural check.)

- [ ] **Step 4: Commit**

Run:
```bash
git add .tekton/tasks/ansible-builder-task.yml
git commit -m "$(cat <<'EOF'
tekton: add in-repo ansible-builder Task

Runs `ansible-builder create` against an execution-environment.yml inside
the workspace and writes a Containerfile to the configured build context.
Adapted verbatim from djdanielsson/rh1-ee's .tekton/tasks/ansible-builder-task.yml.
Reusable for any EE by overriding FILENAME / BUILD_CONTEXT params.
EOF
)"
```
Expected: `1 file changed, ~45 insertions(+)`. Commit lands on `tekton/aap-ee-rhel9-build`.

---

## Task 3: Create the `PipelineRun` for igou-aap-ee-rhel9

**Files:**
- Create: `.tekton/igou-aap-ee-rhel9-push.yml`

- [ ] **Step 1: Write the PipelineRun manifest**

Create `.tekton/igou-aap-ee-rhel9-push.yml` with this exact content:

```yaml
---
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  name: igou-aap-ee-rhel9-push
  annotations:
    pipelinesascode.tekton.dev/on-event: "[push]"
    pipelinesascode.tekton.dev/on-target-branch: "[main]"
    pipelinesascode.tekton.dev/on-path-change: "[execution-environments/igou-aap-ee-rhel9/execution-environment.yml, execution-environments/igou-aap-ee-rhel9/requirements.txt]"
    pipelinesascode.tekton.dev/task: ".tekton/tasks/ansible-builder-task.yml"
    pipelinesascode.tekton.dev/max-keep-runs: "3"
spec:
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes:
            - ReadWriteOnce
          resources:
            requests:
              storage: 1Gi
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
  pipelineSpec:
    workspaces:
      - name: shared-workspace
    tasks:
      - name: fetch-source
        taskRef:
          resolver: cluster
          params:
            - name: kind
              value: task
            - name: name
              value: git-clone
            - name: namespace
              value: openshift-pipelines
        params:
          - name: url
            value: "{{ repo_url }}"
          - name: revision
            value: "{{ revision }}"
          - name: verbose
            value: "true"
        workspaces:
          - name: output
            workspace: shared-workspace

      - name: ansible-builder
        runAfter:
          - fetch-source
        taskRef:
          name: ansible-builder
        params:
          - name: FILENAME
            value: "execution-environments/igou-aap-ee-rhel9/execution-environment.yml"
          - name: BUILD_CONTEXT
            value: "execution-environments/igou-aap-ee-rhel9/context"
          - name: OUTPUT_FILENAME
            value: "Containerfile"
        workspaces:
          - name: source
            workspace: shared-workspace

      - name: build-image
        runAfter:
          - ansible-builder
        matrix:
          params:
            - name: IMAGE
              value:
                - "quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:{{ revision }}"
                - "quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest"
        taskRef:
          resolver: cluster
          params:
            - name: kind
              value: task
            - name: name
              value: buildah
            - name: namespace
              value: openshift-pipelines
        params:
          - name: DOCKERFILE
            value: "Containerfile"
          - name: CONTEXT
            value: "execution-environments/igou-aap-ee-rhel9/context"
          - name: VERBOSE
            value: "true"
          - name: BUILD_ARGS
            value: "$(params.ansible_galaxy_environment_variables[*])"
        workspaces:
          - name: source
            workspace: shared-workspace
```

- [ ] **Step 2: Validate with yamllint**

Run:
```bash
yamllint .tekton/igou-aap-ee-rhel9-push.yml
```
Expected: no output. The repo's `.yamllint` allows long lines (line-length disabled) so the long `on-path-change` annotation is fine.

- [ ] **Step 3: Structural sanity check**

Run:
```bash
python3 - <<'EOF'
import yaml
doc = yaml.safe_load(open('.tekton/igou-aap-ee-rhel9-push.yml'))
assert doc['apiVersion'] == 'tekton.dev/v1'
assert doc['kind'] == 'PipelineRun'
assert doc['metadata']['name'] == 'igou-aap-ee-rhel9-push'
ann = doc['metadata']['annotations']
assert 'pipelinesascode.tekton.dev/on-path-change' in ann
assert 'pipelinesascode.tekton.dev/on-cel-expression' not in ann, "must not coexist with on-path-change"
tasks = doc['spec']['pipelineSpec']['tasks']
assert [t['name'] for t in tasks] == ['fetch-source', 'ansible-builder', 'build-image']
matrix = tasks[2]['matrix']['params'][0]['value']
assert len(matrix) == 2 and ':latest' in matrix[1]
print('OK')
EOF
```
Expected: `OK`.

- [ ] **Step 4: Verify the on-path-change paths point at files that actually exist**

Run:
```bash
test -f execution-environments/igou-aap-ee-rhel9/execution-environment.yml \
  && test -f execution-environments/igou-aap-ee-rhel9/requirements.txt \
  && echo OK
```
Expected: `OK`. Both files must exist or the trigger will never fire.

- [ ] **Step 5: Commit**

Run:
```bash
git add .tekton/igou-aap-ee-rhel9-push.yml
git commit -m "$(cat <<'EOF'
tekton: add PaC PipelineRun for igou-aap-ee-rhel9

Builds the rhel9 EE on every push to main that modifies
execution-environments/igou-aap-ee-rhel9/{execution-environment.yml,requirements.txt}.

Three-task pipeline: git-clone (cluster resolver) -> ansible-builder
(in-repo Task, generates Containerfile) -> buildah (cluster resolver,
matrix-pushes :revision and :latest to quay.apps.ocp.igou.systems).

Galaxy auth flows via Repository.spec.params from the igou-ansible PaC
tenant defined in igou-openshift; we pass them as BUILD_ARGS so the
generated Containerfile picks them up at install time.
EOF
)"
```
Expected: `1 file changed, ~100 insertions(+)`.

---

## Task 4: Create the `.tekton/README.md`

**Files:**
- Create: `.tekton/README.md`

- [ ] **Step 1: Write the README**

Create `.tekton/README.md` with this exact content:

```markdown
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
```

- [ ] **Step 2: Validate the README is well-formed**

Run:
```bash
wc -l .tekton/README.md
head -1 .tekton/README.md
```
Expected: 30–45 lines, first line is `# `.tekton/` — OpenShift Pipelines manifests`.

- [ ] **Step 3: Commit**

Run:
```bash
git add .tekton/README.md
git commit -m "$(cat <<'EOF'
tekton: add README pointing at the igou-openshift PaC tenant

Brief explainer of what lives in .tekton/, where the cluster-side
config lives, the pipeline flow, how to add Tekton for the other EEs,
and the two known gaps (missing ansible.cfg, AAP version skew).
EOF
)"
```
Expected: `1 file changed, ~35 insertions(+)`.

---

## Task 5: Repo-wide lint sweep

**Files:** none modified — just verification.

- [ ] **Step 1: Run yamllint across the whole repo**

Run:
```bash
yamllint .
```
Expected: no output, exit 0. If any new `.tekton/**` file fails, fix it in place and amend the relevant prior commit (or add a fixup commit). Do NOT skip — the `lint.yml` GitHub workflow will block the PR otherwise.

- [ ] **Step 2: Confirm ansible-lint doesn't pick up `.tekton/` as Ansible content**

Run:
```bash
ansible-lint --profile=production --offline 2>&1 | tail -20
```
Expected: same lint results as `main` (the `.tekton/` files aren't Ansible content, so ansible-lint should ignore them by file-type detection). If ansible-lint suddenly fails on our new files, add `.tekton/` to its skip list — but it shouldn't.

If ansible-lint isn't installed locally, skip this step; CI will catch any regression.

- [ ] **Step 3: Confirm the branch has exactly three new commits ahead of `main`**

Run:
```bash
git log --oneline main..HEAD
```
Expected: three commits in this order (most recent first):
1. `tekton: add README pointing at the igou-openshift PaC tenant`
2. `tekton: add PaC PipelineRun for igou-aap-ee-rhel9`
3. `tekton: add in-repo ansible-builder Task`

- [ ] **Step 4: Confirm the file layout matches the plan**

Run:
```bash
find .tekton -type f | sort
```
Expected exactly:
```
.tekton/README.md
.tekton/igou-aap-ee-rhel9-push.yml
.tekton/tasks/ansible-builder-task.yml
```

---

## Task 6: Push the branch and open the PR

**Files:** none modified — git/remote operations only. **STOP and ask the user before running this task** if they have not already authorized pushing branches.

- [ ] **Step 1: Push the branch to origin**

Run:
```bash
git push -u origin tekton/aap-ee-rhel9-build
```
Expected: push succeeds, prints a "Create pull request" URL.

- [ ] **Step 2: Open the pull request**

Run:
```bash
gh pr create --title "tekton: add PaC build pipeline for igou-aap-ee-rhel9" --body "$(cat <<'EOF'
## Summary
- Adds `.tekton/` manifests that wire `igou-aap-ee-rhel9` into OpenShift Pipelines via Pipelines-as-Code on the Forgejo mirror.
- Pipeline: `git-clone` → in-repo `ansible-builder` Task → `buildah` matrix → push `:revision` + `:latest` to `quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9`.
- Trigger: push to `main` that modifies `execution-environments/igou-aap-ee-rhel9/{execution-environment.yml,requirements.txt}` only.
- Cluster-side wiring (namespace, SA, secrets, ImageStream, `Repository` CR, galaxy params) was already provisioned in `igou-openshift/clusters/ocp/pac-tenants/values.yaml` — no changes there.

Spec: `docs/superpowers/specs/2026-05-20-tekton-aap-ee-rhel9-design.md`.
Reference: [`djdanielsson/rh1-ee/.tekton/`](https://github.com/djdanielsson/rh1-ee/tree/main/.tekton).

## Known follow-ups (not in this PR)
- `execution-environments/igou-aap-ee-rhel9/ansible.cfg` is missing — pipeline will fail at `ansible-builder` until added.
- Cluster ImageStream is AAP-26; EE pins AAP-25. EE wins (pulls direct from `registry.redhat.io`).

## Test plan
- [ ] `yamllint .` passes locally
- [ ] After merge, observe `oc -n igou-ansible get pipelinerun -w` triggered by a no-op whitespace change in `execution-environment.yml`
- [ ] Expect first run to fail at `ansible-builder` (missing `ansible.cfg`) — that validates the plumbing
- [ ] Once `ansible.cfg` is in place, verify both image tags push: `podman pull quay.apps.ocp.igou.systems/igou-io/igou-aap-ee-rhel9:latest`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: prints the PR URL.

- [ ] **Step 3: Hand the PR URL back to the user**

No command — just paste the URL from Step 2's output into the chat so the user can review.

---

## Post-merge verification (out of plan scope, included for the user's records)

After this PR merges to `main`, the Forgejo mirror will sync and PaC will register the manifests. To verify:

1. Make a trivial change under `execution-environments/igou-aap-ee-rhel9/` (e.g., a whitespace tweak in `execution-environment.yml`) and merge it.
2. `oc -n igou-ansible get pipelinerun -w` — expect one PipelineRun with three TaskRuns (`fetch-source`, `ansible-builder`, `build-image`-matrix-{0,1}).
3. Expected first failure at `ansible-builder` — `additional_build_files` references the missing `ansible.cfg`. This confirms the pipeline plumbing is correct.
4. Once `ansible.cfg` lands separately, re-run and expect both tags to push.
