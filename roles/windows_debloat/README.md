# windows_debloat

Strip consumer bloat from a Windows guest over **psrp/WinRM**: remove bloatware
Appx packages, disable consumer content / sponsored apps / setup nags /
suggestions / widgets, kill telemetry collection, turn off Copilot, uninstall
OneDrive, disable reserved storage, and (opt-in) uninstall Edge. Every pass is
independently gated and idempotent, so a re-run reports `changed: false`.

The feature set is a **runtime port of
[ntdevlabs/tiny11builder](https://github.com/ntdevlabs/tiny11builder)**
(`tiny11maker.ps1`), which performs the equivalent surgery offline on a mounted
`install.wim`. See the parity table below for what is ported and what is
image-time-only.

## Requirements

- A Windows host reachable over `psrp` (WinRM-over-HTTPS) — the target of the play.
- `ansible.windows` collection (the role uses `ansible.windows.win_shell` and
  `ansible.windows.win_regedit`).

## Role variables

| Var | Default | Meaning |
|-----|---------|---------|
| `windows_debloat_remove_appx` | tiny11 parity set (see `defaults/main.yml`) | Appx identity strings to remove (provisioned + all-users). Runs **only when non-empty**. Kept off the list (as tiny11 keeps them): Store, Notepad, Calculator, Photos, DesktopAppInstaller, runtime frameworks. Pass a narrower list to keep e.g. Terminal/Paint/Camera (the `deploy_windows_desktop` playbook does) |
| `windows_debloat_disable_consumer_content` | `true` | Machine + connect-user registry: consumer content, sponsored/preinstalled apps (full ContentDeliveryManager set), nags, suggestions, widgets, Start-pin seed for new profiles, chat icon, machine telemetry policy, Teams / New Outlook / DevHome auto-install blockers |
| `windows_debloat_disable_telemetry` | `true` | Per-user privacy/collection registry (advertising id, tailored experiences, speech/ink/typing harvesting), disable+stop `dmwappushservice`, disable telemetry scheduled tasks (Compatibility Appraiser, ProgramDataUpdater, CEIP folder, Chkdsk Proxy, WER QueueReporting) |
| `windows_debloat_disable_copilot` | `true` | `TurnOffWindowsCopilot` policy, Edge Copilot sidebar off, machine-wide search-box suggestions off (Copilot Appx removal is part of the list above) |
| `windows_debloat_remove_onedrive` | `true` | Uninstall the per-user OneDrive client + `DisableFileSyncNGSC` machine policy |
| `windows_debloat_disable_reserved_storage` | `true` | `DISM /Online /Set-ReservedStorageState /State:Disabled` (frees ~7 GiB) |
| `windows_debloat_remove_edge` | `false` | **Opt-in, best-effort.** Uninstall the Edge **browser** via its own installer (stops EdgeUpdate, spoofs an EEA region, `AllowUninstall` + `--force-uninstall`); fails honestly on builds that refuse. WebView2 runtime is always kept (MSIX/Store apps — e.g. the Codex desktop app — depend on it). See caveat below |

> **Edge browser removal caveat.** `windows_debloat_remove_edge` is
> best-effort and **off by default**. tiny11 removes Edge by deleting files
> from an *offline* mounted image; there is no supported runtime equivalent,
> and current Win11 client builds refuse `setup.exe --force-uninstall` even
> with EdgeUpdate stopped and an EEA region set (verified against the `win11`
> golden — the uninstaller runs cleanly but leaves `msedge.exe` in place). The
> task is retained because it succeeds on EEA-region and some other builds;
> when it can't remove Edge it fails loudly rather than reporting success. It
> is therefore not exercised by the molecule scenario.

## tiny11builder parity map

Ported (runtime equivalents): Appx removal list, sponsored-apps/
ContentDeliveryManager set, Start-pin seed, chat icon + Teams / New Outlook /
DevHome install blockers, `PushToInstall`/MRT policies, telemetry registry +
`dmwappushservice` + scheduled tasks (disabled instead of tiny11's offline
file deletion), Copilot policies, OneDrive uninstall + `DisableFileSyncNGSC`,
reserved storage, Edge removal (via installer instead of offline file
deletion).

Not applicable at runtime (image-build-only, stays in tiny11): hardware
requirement bypasses (`LabConfig`/`MoSetup`), OOBE `autounattend.xml` /
`BypassNRO` (owned by this repo's sysprep templates), `boot.wim` tweaks,
BitLocker `PreventDeviceEncryption` (OOBE-time), `/Cleanup-Image /ResetBase`,
ISO build, and `tiny11Coremaker`'s WinSxS/WinRE/system-package removal
(explicitly unserviceable). tiny11 writes user tweaks to the *default-user*
hive at image time; this role writes the psrp connect user's HKCU instead.

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
        windows_debloat_remove_edge: true
```

## Testing

Functional coverage lives in the Molecule scenario
[`molecule/role-windows_debloat`](../../molecule/role-windows_debloat/) — it
provisions a `win11` golden clone, applies the role (including
`windows_debloat_remove_edge: true`), and verifies sampled Appx packages are
gone, key registry values read back correctly, telemetry tasks/service are
disabled, reserved storage is off, `OneDrive.exe` and `msedge.exe` are absent,
then re-runs for idempotence.
