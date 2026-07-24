# role-windows_desktop_apps

Live, on-cluster scenario that exercises the `roles/windows_desktop_apps` role
against a real **Windows 11** VM. It provisions one `win11` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), applies the role with a
small deterministic app set, then proves the result with an independent
`choco list`, on-disk binary checks, and a UserChoice ProgId read — not by
trusting the facts the role itself registers.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount.

## Internet egress + first-converge duration

The FIRST converge **bootstraps Chocolatey itself** from
`https://community.chocolatey.org` and then downloads the packages — the cluster
has outbound egress for this. Expect the first converge to be noticeably slower
than the others; the idempotence re-run is fast and **changed=0** (`choco` state
`present`, `win_package` with `creates_path`, and the Firefox-default guard are
all idempotent).

## Win11 client-SKU note

The shared sysprep unattend runs in **`local_account`** mode (a `molecule`
LocalAccounts admin + `LocalAccountTokenFilterPolicy=1`) so psrp can drive the
client-SKU guest with a full network token. The role's execution-policy guard
exists precisely because this client SKU defaults to `Restricted`, which blocks
Chocolatey's bootstrap `.ps1`. See `templates/windows-unattend.xml.j2`.

## What converge applies

Converge imports the role with:

- `windows_desktop_apps_packages`: `7zip` + `firefox`
- `windows_desktop_apps_firefox_default`: `true`
- `windows_desktop_apps_msi_packages`: `[]`

The MSI/EXE path (`windows_desktop_apps_msi_packages`) is left empty here — it is
exercised in production by the `deploy_windows_desktop` playbook's Chrome
enterprise MSI.

## What verify proves

1. **`choco list`** reports both `7zip` and `firefox` as locally installed —
   version-robust (tries the v1 `--local-only` form, falls back to the v2 form),
   resolving `choco.exe` by absolute path (the WinRM env block is cached before
   the bootstrap adds it to PATH), independent of the role's facts.
2. Both **binaries exist on disk** — `C:\Program Files\7-Zip\7z.exe` and
   `C:\Program Files\Mozilla Firefox\firefox.exe`.
3. The **http UserChoice ProgId** reads `FirefoxURL*` — Firefox is the default
   browser for the connect user.

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

molecule test -s role-windows_desktop_apps
```

Budget ~25–35 min end to end (the Chocolatey bootstrap adds a few minutes).

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner) and the
`<host>-sysprep` Secret. `state: absent` is idempotent; the `molecule` namespace
ends empty.
