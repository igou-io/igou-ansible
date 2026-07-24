# role-windows_power

Live, on-cluster scenario that exercises `roles/windows_power` against a real
**Windows 11** VM. It provisions one `win11` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), applies the role, then
proves the result with an independent `powercfg` read and a raw registry read —
not by trusting the register the role itself populated.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount.

## Win11 client-SKU note

The shared sysprep unattend runs in **`local_account`** mode (a `molecule`
LocalAccounts admin + `LocalAccountTokenFilterPolicy=1`) so psrp can drive the
client-SKU guest with a full network token. See
`templates/windows-unattend.xml.j2`.

## What verify proves

Converge applies `windows_power` (`windows_power_disable_sleep: true`). Verify:

1. **`powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE`** reports the AC idle
   standby-timeout index as `0x00000000` (disabled).
2. **`HKLM:\SYSTEM\CurrentControlSet\Control\Power` `HibernateEnabled`** reads
   `0` (hibernation disabled) — a raw `win_reg_stat` read.

The role is idempotent: the first converge flips both settings (changed), the
idempotence re-run is **changed=0**.

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

molecule test -s role-windows_power
```

Budget ~20–30 min end to end (most of it the Windows clone + first boot).

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner) and the
`<host>-sysprep` Secret. `state: absent` is idempotent; the `molecule` namespace
ends empty.
