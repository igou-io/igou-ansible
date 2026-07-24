# windows_desktop_apps

Install the application layer of a Windows desktop over `psrp`/WinRM: an
execution-policy guard, a Chocolatey package set, direct MSI/EXE installers, and
(optionally) Firefox as the default browser. Extracted verbatim from the
`deploy_windows_desktop` playbook so the same behavior is reusable and testable.

## What it does

1. Raises `LocalMachine` execution policy to `RemoteSigned` if it is more
   restrictive — client SKUs default to `Restricted`, which blocks Chocolatey's
   downloaded bootstrap `.ps1`. Runs **first**; the install depends on it.
2. Installs `windows_desktop_apps_packages` with Chocolatey (retried per item).
3. Installs `windows_desktop_apps_msi_packages` with `win_package` (retried per
   item; `creates_path` keeps vendor-direct installers idempotent).
4. When `windows_desktop_apps_firefox_default`, makes Firefox the default browser
   for the psrp connect user. Windows guards http/https UserChoice with a per-user
   hash, so this drives Firefox's own `default-browser-agent` (the only headless
   path) and **fails** if the UserChoice write is rejected.

Every task is idempotent on re-run.

## Requirements

- A Windows host reachable over `psrp` (WinRM). No secret lookups happen inside
  the role — connection creds are inventory/host vars (`ansible_user`, etc.).
- Collections: `chocolatey.chocolatey`, `ansible.windows`.
- The controller/EE needs `pypsrp` for the `psrp` connection plugin.

## Variables

| Var | Default | Meaning |
|-----|---------|---------|
| `windows_desktop_apps_packages` | `[]` | Chocolatey packages: list of dicts `{name, version?, state?}` |
| `windows_desktop_apps_msi_packages` | `[]` | Direct installers: list of dicts `{path, product_id?, arguments?, creates_path?}` |
| `windows_desktop_apps_firefox_default` | `false` | Make Firefox the default browser for the connect user. Needs an interactive session for that user (e.g. autologon console); rejected — and failed honestly — otherwise |
| `windows_desktop_apps_retries` | `5` | Per-item retry count for the two install tasks (CDN-timeout headroom) |
| `windows_desktop_apps_retry_delay` | `60` | Seconds between install retries |

## Example

```yaml
- name: Provision desktop apps
  hosts: windows_desktop_targets
  gather_facts: false
  roles:
    - role: windows_desktop_apps
      vars:
        windows_desktop_apps_packages:
          - name: firefox
          - name: git
          - name: 7zip
        windows_desktop_apps_msi_packages:
          - path: https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise64.msi
            creates_path: C:\Program Files\Google\Chrome\Application\chrome.exe
        windows_desktop_apps_firefox_default: true
```

## Testing

See the Molecule scenario `molecule/role-windows_desktop_apps/` — it provisions a
`win11` golden clone on the live cluster over `psrp`, applies this role, and
verifies the packages/binaries independently.
