# grafana-kiosk

Boot-to-dashboard Grafana kiosk for a Raspberry Pi Zero 2 W, provisioned from
a stock Raspberry Pi OS **Lite** (arm64) install.

> **Hardware constraint:** the original Pi Zero / Zero W (ARMv6) has no
> working Chromium or usable WebKit on current Raspberry Pi OS. This domain
> is for the Zero 2 W (or anything bigger).

## Architecture

Both browser stacks display Grafana through a **local nginx proxy**
(`127.0.0.1:8480`) that injects `Authorization: Bearer <service-account
token>` and forwards to the real Grafana. The token lives in exactly one
root-only file on the Pi; the browser needs no login automation. Grafana's
kiosk/playlist/refresh behaviors are all URL parameters, so they work in any
browser.

Two switchable stacks (`kiosk_stack`):

| Stack | What runs | When |
|---|---|---|
| `cog` (default) | cog/WPE WebKit straight onto DRM/KMS via seatd — no X, no compositor | Lightest on 512MB. **Bench-test gate:** GPU accel through cog's DRM backend is unreliable on Pi vc4/v3d (Igalia/cog#368 closed not-planned) and Grafana-on-WPE has no public precedent — validate your dashboard renders before committing. |
| `chromium` | minimal X11 (xinit + openbox) + [grafana/grafana-kiosk](https://github.com/grafana/grafana-kiosk) driving Chromium | Proven fallback. X11 deliberately: grafana-kiosk's Wayland autostart is broken upstream (grafana-kiosk#135). |

Switching `kiosk_stack` on a converged host retires the other stack's
service automatically.

Memory/SD tuning applied on real hardware (skipped in containers): zstd zram
(`ram / 2`) + `vm.swappiness=100`, `gpu_mem=96` + `disable_splash=1` in the
Pi boot config, volatile capped journald. Services carry
`RuntimeMaxSec=24h` + `Restart=always` to recycle leaking browsers daily.

## Playbooks

- `converge.yaml` — guard (requires `--limit`) → `linux/baseline.yaml` →
  `linux/node_exporter.yaml` → `setup-kiosk.yaml`.
- `setup-kiosk.yaml` — proxy, tuning, and the selected browser stack. Targets
  `ansible_limit | default('grafana_kiosk')`.

```bash
ansible-playbook playbooks/grafana-kiosk/converge.yaml \
  -i igou-inventory/inventory.yaml -l kiosk.igou.systems
```

## Variables (host_vars)

| Var | Default | Notes |
|---|---|---|
| `kiosk_grafana_url` | — **required** | Upstream Grafana origin, e.g. `https://grafana.example.com` |
| `kiosk_stack` | `cog` | `cog` or `chromium` |
| `kiosk_dashboard_path` | `/` | e.g. `/playlists/play/<uid>` or `/d/<uid>/<slug>?orgId=1&refresh=30s` |
| `kiosk_grafana_token` | 1Password lookup | Explicit token (CI only — prefer the op lookup) |
| `kiosk_grafana_token_op_item` / `_op_vault` / `_op_field` | `grafana-kiosk` / `claude` / `password` | 1Password coordinates of the SA token |
| `kiosk_proxy_listen` | `127.0.0.1:8480` | |
| `kiosk_playlist` | `false` | grafana-kiosk playlist mode (chromium stack) |
| `kiosk_grafana_kiosk_mode` | `full` | grafana-kiosk kiosk-mode (chromium stack) |
| `kiosk_service_max_runtime` | `24h` | Daily browser recycle |
| `kiosk_zram_size` | `ram / 2` | zram-generator expression |
| `kiosk_gpu_mem` | `96` | Pi GPU memory split |
| `kiosk_drm_video_mode` | unset | e.g. `1920x1080` (cog stack) |
| `kiosk_chromium_packages` / `kiosk_chromium_bin` | Debian `chromium` | Override if RPi OS ships `chromium-browser` |
| `kiosk_verify_api` | `true` | End-of-play proxy/token verification |

## Prerequisites

1. Grafana service account (Viewer) + token, stored in 1Password
   (`op://claude/grafana-kiosk/password` by default).
2. Inventory: host in the `grafana_kiosk` group with `kiosk_grafana_url` in
   host_vars; rb5009 DHCP lease (MAC-only, no client-id) + DNS record.
3. Pi flashed with Raspberry Pi OS Lite arm64 (Imager customization:
   hostname, `igou` user + fleet key).

## Testing

`molecule test -s grafana-kiosk` converges **both stacks** in podman
containers hard-capped at 512MB (`--memory=512m --memory-swap=512m` — the Pi
Zero 2 W envelope), against a mock Grafana that echoes the Authorization
header. Verify proves end-to-end token injection, unit enablement, config
permissions, and runs real headless browser renders (Chromium screenshot,
cog 20s survival) inside the memory cap. Provisioning uses
`david_igou.molecule_provisioners` (see `molecule/grafana-kiosk/inventory/`).
