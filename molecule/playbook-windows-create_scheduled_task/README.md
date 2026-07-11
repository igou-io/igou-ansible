# playbook-windows-create_scheduled_task

Live, on-cluster scenario that exercises
`playbooks/windows/create_scheduled_task.yaml` against a real **Windows Server
2025** VM. It provisions one `win2k25` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), registers a daily
scheduled task under `\Ansible\`, then proves the task **actually fires** —
existence alone is not enough.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount. Copied from the PILOT `playbook-windows-manage_services`; see
that scenario's README for the shared-plumbing architecture (sysprep Secret,
unattend template, provisioner lifecycle).

## Prerequisites

- The live `ocp.igou.systems` cluster reachable, with the
  `ocp-ansible-molecule` SA token + API host exported, the SA's cross-namespace
  CDI clone grant for `win2k25`, and a `win2k25` golden DataSource present.
- **`pypsrp`** on the controller/EE (runtime lib for the `psrp` connection).
- `ansible.windows` + `community.windows` (installed by the galaxy dependency
  step from `collections.yml`).

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s playbook-windows-create_scheduled_task
```

## What verify proves

1. `\Ansible\molecule-marker-task` is registered and **enabled** — an
   independent `win_scheduled_task_stat` read, not the converge module's return.
2. The task **fires**: verify removes any stale marker, `Start-ScheduledTask`s
   the task, and polls until `C:\molecule-task-fired.txt` appears with non-empty
   content. A daily trigger never auto-fires during the test window, so the
   marker's appearance is caused only by our explicit on-demand run.
3. `C:\molecule-specialize-done.txt` exists — the sysprep unattend
   FirstLogonCommands ran, so the whole specialize/WinRM path executed.

## Watch items (feed to Phase 5 — not pre-solved)

- **SYSTEM logon_type idempotence flap.** `community.windows.win_scheduled_task`
  can report `changed` on a second converge for tasks that run as `SYSTEM`
  because the module normalizes the principal `logon_type` differently on read
  vs. write. If the `idempotence` step fails with a diff limited to
  `logon_type` / principal on the SYSTEM task, that is the known flap — inspect
  the reported diff before treating it as a real regression. The playbook is NOT
  modified to paper over this (it must stay the real, shipped playbook); the fix
  if it bites is to pin `logon_type` in the playbook under a separate review,
  not here.

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner) and the
`<host>-sysprep` Secret. `state: absent` is idempotent, so re-running destroy —
or destroying after a failed create — is a safe no-op. The `molecule` namespace
ends empty of scenario resources.
