# playbook-windows-manage_services

**PILOT** Windows molecule scenario — the reference pattern the other
`playbook-windows-*` scenarios copy.

Live, on-cluster scenario that exercises `playbooks/windows/manage_services.yaml`
against a real **Windows Server 2025** VM. It provisions one `win2k25` golden
clone via `david_igou.molecule_provisioners` (connection `psrp`), runs the
service-management playbook unmodified, then proves the result with independent
`win_service_info` reads — not by trusting the module's own return.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount.

## Architecture (shared vs. scenario-specific)

- **Shared plumbing** (`molecule/_windows_common/`, reused by every Windows scenario):
  - `templates/windows-unattend.xml.j2` — per-clone **specialization** unattend
    (specialize + oobeSystem passes only) for a sysprep-generalized golden. Sets
    the ComputerName + Administrator password and stands up WinRM-over-HTTPS on
    5986 (self-signed cert, listener, firewall on all profiles) at first boot.
  - `playbooks/windows-sysprep-secrets.yml` — renders that template into a
    `<host>-sysprep` Secret (key `unattend.xml`) per molecule host; imported by
    `create.yml` (create) and `destroy.yml` (`windows_sysprep_state: absent`).
- **Scenario-specific** (this directory): which VM (`inventory/`), which playbook
  (`converge.yml`), and the independent asserts (`verify.yml`).

## Prerequisites

- The live `ocp.igou.systems` cluster reachable, with:
  - the `ocp-ansible-molecule` SA token and API host exported (see below),
  - the SA's cross-namespace CDI clone grant for `win2k25` in
    `openshift-virtualization-os-images`,
  - a `win2k25` golden PVC/DataSource present there.
- **`pypsrp`** on the controller/EE (runtime lib for the `psrp` connection).
- `ansible.windows` + `community.windows` (installed by the galaxy dependency
  step from `collections.yml`).

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s playbook-windows-manage_services
```

Step-wise (useful because first boot + specialize is slow):

```bash
molecule create   -s playbook-windows-manage_services   # sysprep Secret + VM
molecule prepare  -s playbook-windows-manage_services   # wait for psrp (up to 25 min)
molecule converge -s playbook-windows-manage_services   # run manage_services.yaml
molecule verify   -s playbook-windows-manage_services   # independent asserts
molecule destroy  -s playbook-windows-manage_services   # cleanup (ns ends empty)
```

## Expected duration

Smart-clone ~2–5 min, first boot + OOBE specialize + FirstLogonCommands ~5–15
min before `psrp` connects, then converge/idempotence/verify are quick. Budget
~20–30 min end to end.

## What verify proves

1. `W32Time` is **running** with **auto** start (independent `win_service_info`).
2. `Spooler` is **stopped** and **disabled** (independent `win_service_info`).
3. Starting the disabled `Spooler` **fails** (negative test), and it stays
   stopped — `start_mode=disabled` is truly enforced, not cosmetic.
4. `C:\molecule-specialize-done.txt` exists — the sysprep unattend
   FirstLogonCommands ran, so the whole specialize/WinRM path executed.

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner) and the
`<host>-sysprep` Secret. `state: absent` is idempotent, so re-running destroy —
or destroying after a failed create — is a safe no-op. The `molecule` namespace
ends empty of scenario resources.
