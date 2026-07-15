# playbook-windows-install_applications

Live, on-cluster scenario that exercises `playbooks/windows/install_applications.yaml`
against a real **Windows 11** VM. It provisions one `win11` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), installs a small
deterministic app set with Chocolatey, then proves the result with an
independent `choco list` and on-disk binary checks — not by trusting the
`ansible_chocolatey` fact the playbook itself populates.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount.

## Internet egress + first-converge duration

The FIRST converge **bootstraps Chocolatey itself** from
`https://community.chocolatey.org` and then downloads the packages — the cluster
has outbound egress for this. Expect the first converge to be noticeably slower
than the others (Chocolatey install + two package downloads); the idempotence
re-run is fast and **changed=0** (`choco` state `present` is idempotent).

## Win11 client-SKU note

The shared sysprep unattend runs in **`local_account`** mode (a `molecule`
LocalAccounts admin + `LocalAccountTokenFilterPolicy=1`) so psrp can drive the
client-SKU guest with a full network token. See
`molecule/_windows_common/templates/windows-unattend.xml.j2`.

## What verify proves

Converge installs `7zip` and `notepadplusplus` (state `present`). Verify:

1. **`choco list`** reports both packages as locally installed — version-robust
   (tries the v1 `--local-only` form, falls back to the v2 form), and
   independent of the `ansible_chocolatey` fact.
2. Both **binaries exist on disk** — `C:\Program Files\7-Zip\7z.exe` and
   `C:\Program Files\Notepad++\notepad++.exe`.

## Prerequisites

- The live cluster reachable with the `ocp-ansible-molecule` SA token/host
  exported, its CDI clone grant for `win11`, and a `win11` golden PVC/DataSource.
- Outbound HTTPS egress from the guest to `community.chocolatey.org`.
- **`pypsrp`** on the controller/EE.
- Collections from `collections.yml` (includes `chocolatey.chocolatey`).

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s playbook-windows-install_applications
```

Budget ~25–35 min end to end (the Chocolatey bootstrap adds a few minutes).

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner) and the
`<host>-sysprep` Secret. `state: absent` is idempotent; the `molecule` namespace
ends empty.
