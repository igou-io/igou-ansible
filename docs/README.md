# `docs/` index

Operator-facing documentation for the homelab. Source-of-truth for "I forgot
how X worked" two weeks from now.

## Operations runbooks

| Doc | What it covers |
|---|---|
| [`netboot-operations.md`](netboot-operations.md) | netboot.xyz menu, host pins, ISOs, kickstart/cloud-init, OpenShift PXE assets, rb5009 iPXE binaries, smoke testing, troubleshooting |
| [`openshift-operations.md`](openshift-operations.md) | Initial cluster (agent-install), GitOps bootstrap, secret sync to 1Password, SNO ISO, add-node (link), CSR approval, common breaks |
| [`truenas-operations.md`](truenas-operations.md) | Docker containers, users, NFS netboot, API smoke test, deprecated playbooks |
| [`hermes-vm-lifecycle.md`](hermes-vm-lifecycle.md) | Hermes KubeVirt VM: provision/rebuild/deprovision (`hermes-state` survives), snapshot create/list/prune/restore (double-guarded), AAP templates + nightly schedule |
| [`execution-environments.md`](execution-environments.md) | What each EE is for, when to rebuild, manual rebuild + push |
| [`disaster-recovery.md`](disaster-recovery.md) | "X is dead, what do I run?" per component (rb5009, truenas, netbootxyz container, OCP cluster, OCP worker, homelab pets) |
| [`troubleshooting.md`](troubleshooting.md) | Symptom-keyed cross-cutting issues (PXE, DHCP, container, secrets, lint, CI) |

## Designs and plans (under `superpowers/`)

`docs/superpowers/specs/*` — design specs (architecture, decisions log).
`docs/superpowers/plans/*` — implementation plans (task-by-task).

Each is dated `YYYY-MM-DD-<topic>-design.md` / `YYYY-MM-DD-<topic>.md`. New work
goes in via the brainstorming → spec → plan → execute flow. Specs of completed
work are kept; specs of abandoned work are deleted.

## What lives where (cross-repo)

- `igou-ansible` — playbooks, roles, EEs, docs (this tree).
- `igou-inventory` — inventory + group_vars + host_vars (separate repo,
  symlinked at `igou-inventory/`). Worked examples in
  `group_vars/all/netboot.yml`. Secrets go via 1Password lookups.
- `igou-openshift`, `igou-kubernetes`, `igou-infrastructure`, `rosa-gitops` —
  declarative GitOps trees referenced by the bootstrap playbooks. Out of scope
  for this docs tree.

## Conventions

- All commands assume CWD `/workspace/igou-ansible` and `KUBECONFIG` /
  `OP_SERVICE_ACCOUNT_TOKEN` set when an OCP / 1Password operation needs them.
- `ansible-navigator run …` and `ansible-playbook …` are interchangeable for
  any of the playbooks shown here. Examples use `ansible-playbook` for
  brevity; `ansible-navigator` adds the EE container layer.
- Lint everywhere: `ansible-lint --profile=production` and `yamllint`. Both
  must be clean before a commit.
- The `syntax-check` GitHub workflow currently fails project-wide because it
  doesn't skip task-include files. One-line fix is the standing follow-up;
  the rest of CI (lint, GitGuardian) is green.
