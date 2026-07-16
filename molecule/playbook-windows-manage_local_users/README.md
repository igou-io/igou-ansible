# playbook-windows-manage_local_users

Live, on-cluster scenario that exercises `playbooks/windows/manage_local_users.yaml`
against a real **Windows 11** VM. It provisions one `win11` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), runs the user/group
playbook unmodified, then proves the result with independent `Get-LocalUser` /
`Get-LocalGroupMember` reads and by actually logging in as the created user.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount.

## Win11 client-SKU note

The shared sysprep unattend runs in **`local_account`** mode: it creates a
`molecule` LocalAccounts admin and sets `LocalAccountTokenFilterPolicy=1`. That
policy is load-bearing here — verify logs in a second psrp session AS the
created `moluser` (a local Administrator), which only succeeds because
non-builtin local admins receive a full NTLM network token under that policy.
See `templates/windows-unattend.xml.j2`.

## What converge/verify do

- **prepare** seeds `molgone` so converge's `state: absent` is a REAL
  present -> absent transition, not a vacuous no-op.
- **converge** creates the `AppTesters` group, a present `moluser`
  (member of Administrators + AppTesters), and removes `molgone`.
- **verify** reads the live account DB:
  1. `moluser` **exists and is enabled**; `molgone` **is gone** (independent read).
  2. `moluser` is a **member of Administrators and AppTesters** (independent read).
  3. A **fresh psrp login AS `moluser`** (only `ansible_user`/`ansible_password`
     overridden) runs `win_whoami` and confirms the identity — proving the
     password the playbook set actually authenticates.

## Prerequisites

- The live cluster reachable with the `ocp-ansible-molecule` SA token/host
  exported, its CDI clone grant for `win11`, and a `win11` golden PVC/DataSource.
- **`pypsrp`** on the controller/EE.
- Collections from `collections.yml`.

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s playbook-windows-manage_local_users
```

Budget ~20–30 min end to end.

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner) and the
`<host>-sysprep` Secret. `state: absent` is idempotent; the `molecule` namespace
ends empty.
