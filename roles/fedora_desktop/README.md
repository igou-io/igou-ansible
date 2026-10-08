# fedora_desktop

Configure a Fedora x86_64 cloud guest as a persistent GNOME desktop with the
official OpenAI RPM, Codex CLI, T3, Node.js, Firefox, and optional Chrome.
Requirements: systemd, an independent blank or ext4 state block device, Ansible
2.18+, `community.general`, `ansible.posix`, and upstream network access.

The role is a pure function over `meta/argument_specs.yml`. It performs no
secret-backend lookups. OAuth belongs on the caller's controller; the optional
join-key argument accepts only a resolved short-lived key.

| Input | Default / purpose |
|---|---|
| `fedora_desktop_user`, `fedora_desktop_uid` | `codex`, `1100`: unprivileged persistent user identity |
| `fedora_desktop_state_device` | `/dev/disk/by-id/virtio-codex-state` |
| `fedora_desktop_state_dir` | `/var/lib/codex-desktop` |
| `fedora_desktop_packages` | GNOME, browser, guest access, time sync, native build and SELinux utilities |
| `fedora_desktop_node_version` | `26.10.0`, matching the maintained devenv Node pin |
| `fedora_desktop_t3_version` | `0.0.45`, matching the maintained devenv T3 pin |
| `fedora_desktop_codex_version` | `0.160.1`, matching the maintained devenv Codex pin |
| `fedora_desktop_t3_port` | `3773`, loopback listener |
| `fedora_desktop_openai_rpm_url` | Official x86_64 latest bootstrap RPM |
| `fedora_desktop_openai_signing_key_url` | Reviewed official Linux signing bundle |
| `fedora_desktop_openai_signing_fingerprint` | Expected fingerprint; key import fails on mismatch |
| `fedora_desktop_chrome` | `false`: optional Chrome |
| `fedora_desktop_tailscale` | `true`: install Tailscale and persist identity |
| `fedora_desktop_tailscale_authkey` | Empty; optional short-lived join key, declared `no_log` |
| `fedora_desktop_tailscale_tags` | `tag:codex` |

```yaml
---
- name: Configure the dedicated Fedora desktop
  hosts: codex_desktop_guest
  become: true
  roles:
    - role: fedora_desktop
```

The role is intended to converge idempotently. Real installation requires a
running guest; check mode cannot prove mount/startup behavior. Rollback consists
of restoring the previous OS disk and retaining the separate state disk. There
is no state-destruction interface. Sign-in, encrypted keyring enrollment/unlock,
and T3 pairing remain interactive. GNOME uses its normal Wayland session and
the official app's normal session autostart; SELinux is retained.

Functional scenario: `molecule test -s role-fedora-desktop`. Operator runbook:
[`docs/codex-desktop.md`](../../docs/codex-desktop.md).

Author: David Igou. License: MIT.
