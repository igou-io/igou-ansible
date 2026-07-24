# windows_debloat

Strip consumer bloat from a Windows guest over **psrp/WinRM**: remove bloatware
Appx packages, disable consumer content / setup nags / suggestions /
news-and-interests widgets / telemetry, and uninstall OneDrive. Each of the
three passes is independently gated and idempotent, so a re-run reports
`changed: false`.

## Requirements

- A Windows host reachable over `psrp` (WinRM-over-HTTPS) — the target of the play.
- `ansible.windows` collection (the role uses `ansible.windows.win_shell` and
  `ansible.windows.win_regedit`).

## Role variables

| Var | Default | Meaning |
|-----|---------|---------|
| `windows_debloat_remove_appx` | desktop bloatware set (see `defaults/main.yml`) | Appx identity strings to remove (provisioned + all-users). The removal task runs **only when this list is non-empty**. Kept off the list: Store, Terminal, Notepad, Calculator, Photos, Camera, Paint, DesktopAppInstaller and the runtime frameworks |
| `windows_debloat_disable_consumer_content` | `true` | Write the machine/user registry values that disable consumer content, setup nags, suggestions, widgets, and telemetry |
| `windows_debloat_remove_onedrive` | `true` | Uninstall the per-user OneDrive client |

## Example

```yaml
- name: Debloat the Windows desktop
  hosts: windows_desktop_targets
  gather_facts: false
  tasks:
    - name: Strip consumer bloat
      ansible.builtin.import_role:
        name: windows_debloat
      vars:
        windows_debloat_remove_appx:
          - Microsoft.BingNews
          - Microsoft.XboxGamingOverlay
        windows_debloat_remove_onedrive: true
```

## Testing

Functional coverage lives in the Molecule scenario
[`molecule/role-windows_debloat`](../../molecule/role-windows_debloat/) — it
provisions a `win11` golden clone, applies the role, and verifies the sampled
Appx packages are gone, the key registry values read back correctly, and
`OneDrive.exe` is absent, then re-runs for idempotence.
