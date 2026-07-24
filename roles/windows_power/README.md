# windows_power

Disable idle standby and hibernation on a Windows guest. A Win11 client SKU
sleeps on idle by default, and a sleeping KubeVirt guest drops RDP, WinRM, and
the qemu guest agent while the VMI still reports **Ready** (live-hit 2026-07-19:
a guest slept and stayed unreachable for 5 days). This role zeroes the AC
idle standby-timeout and turns hibernation off with `powercfg`.

Intentionally a tiny single-concern role.

## Requirements

- A Windows host reachable over `psrp`/WinRM (the `pypsrp` lib on the
  controller/EE for `psrp`).
- Collections: `ansible.windows`.

## Role variables

| Var | Default | Meaning |
|-----|---------|---------|
| `windows_power_disable_sleep` | `true` | Gate the task. When `true`, zero the AC idle standby-timeout and turn hibernation off. When `false`, the role does nothing. |

Idempotent: re-runs report `changed: false` once the AC standby-idle index is
`0x00000000` and `HibernateEnabled` is `0`.

## Example

```yaml
- name: Keep the Windows guest awake
  hosts: windows
  gather_facts: false
  tasks:
    - name: Disable sleep and hibernation
      ansible.builtin.import_role:
        name: windows_power
      vars:
        windows_power_disable_sleep: true
```

## Testing

See the molecule scenario `molecule/role-windows_power/` — it provisions a
`win11` golden clone, applies the role, and verifies independently that
`powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE` shows AC index
`0x00000000` and that `HKLM:\SYSTEM\CurrentControlSet\Control\Power`
`HibernateEnabled` is `0`.
