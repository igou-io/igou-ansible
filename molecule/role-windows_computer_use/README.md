# role-windows_computer_use

Live, on-cluster scenario that exercises `roles/windows_computer_use` against a
real **Windows 11** VM. It provisions one `win11` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), applies the role, then
proves each computer-use setting with **independent** registry / `powercfg` /
scheduled-task reads — not by trusting the role's own registers, and never the
password.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount.

## Win11 client-SKU note

The shared sysprep unattend runs in **`local_account`** mode (a `molecule`
LocalAccounts admin + `LocalAccountTokenFilterPolicy=1`) so psrp can drive the
client-SKU guest with a full network token. The role's autologon user/password
default to that same psrp identity (`ansible_user` / `ansible_password`). See
`templates/windows-unattend.xml.j2`.

## What verify proves

Converge applies the role (autologon, quiet UAC, no toasts, resolution
`1920x1080`). Verify independently reads:

1. **Winlogon** — `AutoAdminLogon=1` and `DefaultUserName` = the connect user.
2. **Never-lock policies** — `NoLockScreen=1`, `InactivityTimeoutSecs=0`.
3. **Quiet UAC** — `ConsentPromptBehaviorAdmin=0`, `PromptOnSecureDesktop=0`.
4. **Toasts off** — `ToastEnabled=0` in the connect user's hive.
5. **Display never blanks** — `powercfg` AC `VIDEOIDLE` index `0x00000000`.
6. **Resolution pin** — `C:\ProgramData\pin-resolution.ps1` on disk and the
   `pin-display-resolution` logon scheduled task present and enabled.

## Prerequisites

- The live cluster reachable with the `ocp-ansible-molecule` SA token/host
  exported, its CDI clone grant for `win11`, and a `win11` golden PVC/DataSource.
- **`pypsrp`** on the controller/EE.
- Collections from `collections.yml` (`ansible.windows`, `community.windows`).

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s role-windows_computer_use
```

Budget ~20–30 min end to end (most of it the Win11 clone + OOBE specialize).

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner) and the
`<host>-sysprep` Secret. `state: absent` is idempotent; the `molecule` namespace
ends empty.
