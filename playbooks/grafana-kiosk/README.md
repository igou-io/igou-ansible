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
| `kiosk_grafana_token` | — | Explicit token for test fixtures; prefer the lookup below on managed hosts |
| `kiosk_grafana_token_lookup` | — **required** unless `kiosk_grafana_token` | Full lookup expression for the SA token, e.g. `"{{ lookup('community.general.onepassword', 'grafana-kiosk', field='password', vault='claude') }}"` |
| `kiosk_proxy_listen` | `127.0.0.1:8480` | |
| `kiosk_playlist` | `false` | grafana-kiosk playlist mode (chromium stack) |
| `kiosk_grafana_kiosk_mode` | `full` | grafana-kiosk kiosk-mode (chromium stack) |
| `kiosk_service_max_runtime` | `24h` | Daily browser recycle |
| `kiosk_zram_size` | `ram / 2` | zram-generator expression |
| `kiosk_gpu_mem` | `96` | Pi GPU memory split |
| `kiosk_drm_video_mode` | unset | e.g. `1920x1080` (cog stack) |
| `kiosk_cog_env` | `{}` | Extra `Environment=` lines for the cog unit (VM render testing; leave empty on real hardware) |
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

`molecule test -s grafana-kiosk` converges both browser stacks in disposable
Debian 13 KubeVirt guests with 2 GiB of memory each. A small local HTTP server
stands in for Grafana so the test can verify the proxy's authorization header.
Verification checks active zram, the protected proxy config, enabled browser
units, a Chromium screenshot, and a short headless cog run. These guests do
not measure behavior within a Pi Zero 2 W's 512 MiB memory limit or against
a live Grafana dashboard. Run with `use ocp-ansible-molecule`; see
`molecule/README.md` for the Casval default and shared-storage override.

### Render testing (run without destroy)

The step commands leave the guests running for another render check:

```bash
use ocp-ansible-molecule

molecule converge -s grafana-kiosk
KIOSK_FETCH_SCREENSHOT=1 molecule verify -s grafana-kiosk
```

`KIOSK_FETCH_SCREENSHOT=1` fetches the Chromium render into the scenario's
ephemeral `screenshots/` directory; verify prints the path. Plain test runs
leave the screenshot in the guest.

To inspect a live virtual display, set `kiosk_start_browser: true` and find
the run-suffixed VM name before attaching VNC:

```bash
molecule converge -s grafana-kiosk -- -e kiosk_start_browser=true
oc get vm -n molecule
virtctl vnc -n molecule RUN_SUFFIXED_KIOSK_COG_NAME
```

The Debian cloud kernel has no DRM device, so the browser units are normally
left enabled but stopped in this test. The headless render checks are the
repeatable smoke test. Container-disk roots are disposable if the launcher
pod is recreated.

Use the run-suffixed VMI name for `virtctl ssh`, or use the connection details
in the scenario's ephemeral inventory. When finished:

```bash
molecule destroy -s grafana-kiosk
```

`molecule test -s grafana-kiosk` destroys its guests automatically.
