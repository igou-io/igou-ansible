# playbook-windows-provision_test_machine

Live, on-cluster scenario that exercises `playbooks/windows/provision_test_machine.yaml`
against a real **Windows 11** VM. It provisions one `win11` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), stands up a self-contained
test bed (dedicated test user + apps + Remote Desktop), then proves the result
with independent in-guest reads, an in-guest credential validation, and an
EXTERNAL RDP reachability probe — not by trusting the modules' own returns.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount.

## Win11 client-SKU note

The shared sysprep unattend runs in **`local_account`** mode (a `molecule`
LocalAccounts admin + `LocalAccountTokenFilterPolicy=1`) so the scenario's psrp
connection — as the `molecule` admin — has a full network token. See
`molecule/_windows_common/templates/windows-unattend.xml.j2`.

## What verify proves

Converge creates the default test user `apptester` (a NON-admin in Users +
Remote Desktop Users), installs `7zip`, and enables RDP. Verify:

1. `apptester` **exists, is enabled, and is a Remote Desktop user**
   (independent `Get-LocalUser` / `Get-LocalGroupMember`).
2. The **test user's password authenticates** — validated IN-GUEST via a
   LogonUser-style `PrincipalContext.ValidateCredentials` call. **Why not an
   external psrp login as the test user (the spec's first choice)?** `apptester`
   is a non-admin, the playbook does not set `LocalAccountTokenFilterPolicy` for
   it, and non-admins are not in the default WinRM RootSDDL — so both an external
   psrp session and a WinRM loopback `Invoke-Command` would be Access-Denied,
   testing WinRM ACLs instead of the password. `ValidateCredentials` does a real
   local network LogonUser with no WinRM/UAC-policy dependency, validating
   exactly the password the playbook set.
3. **RDP is reachable from OUTSIDE** — a NodePort to guest **3389** accepts a TCP
   connect from the controller (`wait_for` state=started), proving the listener +
   the `fDenyTSConnections` switch + the firewall rule (incl. the **Public**
   profile, the review fix) are all in effect.
4. `fDenyTSConnections=0` (independent registry read) and the installed **7-Zip
   binary is on disk**.

## Prerequisites

- The live cluster reachable with the `ocp-ansible-molecule` SA token/host
  exported, its CDI clone grant for `win11`, and a `win11` golden PVC/DataSource.
- Outbound HTTPS egress from the guest (Chocolatey bootstrap for the app).
- **`pypsrp`** on the controller/EE.
- Collections from `collections.yml` (includes `chocolatey.chocolatey`).

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s playbook-windows-provision_test_machine
```

Budget ~25–35 min end to end.

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner), the
`<host>-sysprep` Secret, and — belt-and-braces — the `wintm-test-rdp` Service
verify creates (verify also deletes it in an `always` block). `state: absent` is
idempotent; the `molecule` namespace ends empty.
