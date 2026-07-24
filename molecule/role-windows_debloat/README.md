# role-windows_debloat

Live, on-cluster scenario that exercises the `roles/windows_debloat` role
against a real **Windows 11** VM. It provisions one `win11` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), applies the role, then
proves the result with **independent** Appx / registry / on-disk reads — not by
trusting the role's own registers.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount.

## Win11 client-SKU note

The shared sysprep unattend runs in **`local_account`** mode (a `molecule`
LocalAccounts admin + `LocalAccountTokenFilterPolicy=1`) so psrp can drive the
client-SKU guest with a full network token. See
`templates/windows-unattend.xml.j2`.

## What verify proves

Converge applies the role with its default (tiny11builder-parity) Appx list
and toggles. `windows_debloat_remove_edge` stays **off** (its default): Edge
browser removal on a live host is unsupported and build-dependent (tiny11 does
it offline against a mounted image), so it is not part of the reliably-testable
surface. Verify then checks, independently:

1. A **sampled set of targeted Appx identities** (BingNews, GamingApp,
   XboxGamingOverlay, ZuneMusic, MSTeams, plus tiny11-parity additions
   WindowsMaps, WindowsCamera, windowscommunicationsapps) appears in
   **neither** `Get-AppxProvisionedPackage -Online` **nor**
   `Get-AppxPackage -AllUsers`.
2. The key registry values read back as set: `DisableWindowsConsumerFeatures=1`,
   `AllowTelemetry=0`, `SystemPaneSuggestionsEnabled=0`,
   `ContentDeliveryAllowed=0`, Teams `DisableInstallation=1`,
   `TurnOffWindowsCopilot=1`, AdvertisingInfo `Enabled=0`,
   `DisableFileSyncNGSC=1`.
3. `dmwappushservice` is disabled+stopped and the Compatibility Appraiser
   scheduled task reads `Disabled` (or either is absent on the build).
4. DISM reports **reserved storage disabled**.
5. `OneDrive.exe` is **absent** from both known locations
   (`%LOCALAPPDATA%\Microsoft\OneDrive` and `%ProgramFiles%\Microsoft OneDrive`).

The `idempotence` step in the sequence confirms a second converge is
`changed=0` — every role task is idempotent on re-run.

## Prerequisites

- The live cluster reachable with the `ocp-ansible-molecule` SA token/host
  exported, its CDI clone grant for `win11`, and a `win11` golden PVC/DataSource.
- **`pypsrp`** on the controller/EE.
- Collections from `collections.yml` (the provisioner + `ansible.windows`).

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s role-windows_debloat
```

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner) and the
`<host>-sysprep` Secret. `state: absent` is idempotent; the `molecule` namespace
ends empty.
