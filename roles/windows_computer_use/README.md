# windows_computer_use

Make a Windows desktop ready for **screen-driven / computer-use agents** (e.g.
Codex computer use) — a desktop that is always logged on, never locks, never
blanks, elevates without a secure-desktop prompt the agent can't see, shows no
toasts, and holds a fixed resolution for deterministic screenshots.

> **LAB-ONLY.** Console auto-logon stores the password readably in the Winlogon
> registry hive. The autologon settings take effect at the next boot.

## What it does

- **Console auto-logon** as the connect user + **never lock / never blank** the
  desktop (`windows_computer_use_autologon`).
- **Silent admin elevation off the secure desktop** — `EnableLUA` stays `1` so
  MSIX/Store apps still run (`windows_computer_use_quiet_uac`).
- **Toast notifications off** for the connect user
  (`windows_computer_use_disable_notifications`).
- **Pins the display resolution at logon** via a scheduled task running a staged
  script (resolution can only change from an interactive session, never over
  WinRM) (`windows_computer_use_resolution`).

## Requirements

- A Windows host reachable over **psrp / WinRM** (`ansible_connection: psrp`),
  connected as a local admin. The role reuses the connection identity
  (`ansible_user` / `ansible_password`) as the autologon defaults.
- Collections: **`ansible.windows`** (win_regedit, win_shell, win_copy) and
  **`community.windows`** (win_scheduled_task).

## Role variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `windows_computer_use_autologon` | `true` | Enable console auto-logon and keep the desktop from locking/blanking. Gates the autologon, never-lock, and never-power-off-the-display tasks |
| `windows_computer_use_autologon_user` | `{{ ansible_user }}` | User written as the Winlogon `DefaultUserName` |
| `windows_computer_use_autologon_password` | `{{ ansible_password }}` | Password written as the Winlogon `DefaultPassword` (`no_log`; passed in, never looked up) |
| `windows_computer_use_quiet_uac` | `true` | Admins elevate silently (`ConsentPromptBehaviorAdmin=0`) off the secure desktop (`PromptOnSecureDesktop=0`); `EnableLUA` stays `1` |
| `windows_computer_use_disable_notifications` | `true` | Disable toast notifications for the connect user |
| `windows_computer_use_resolution` | `1920x1080` | `WxH` pinned at each logon via a scheduled task; `''` leaves the resolution alone (skips both resolution tasks) |

## Example

```yaml
- name: Ready the desktop for computer use
  hosts: windows_desktop_targets
  gather_facts: false
  tasks:
    - name: Apply computer-use readiness
      ansible.builtin.import_role:
        name: windows_computer_use
      vars:
        windows_computer_use_resolution: 1920x1080
```

The role is **idempotent** — a re-run reports `changed: false`.

## Testing

Functional Molecule scenario:
[`molecule/role-windows_computer_use/`](../../molecule/role-windows_computer_use/) —
provisions a `win11` golden clone on the live cluster, applies the role over
psrp, and verifies each setting with independent registry / `powercfg` /
scheduled-task reads (never the password).
