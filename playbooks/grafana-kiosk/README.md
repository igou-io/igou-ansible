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

`molecule test -s playbook-grafana-kiosk` converges **both stacks** in guests capped
at the Pi Zero 2 W's 512MB envelope, against a mock Grafana that echoes the
Authorization header. Verify proves end-to-end token injection, unit
enablement, config permissions, and runs real headless browser renders
(Chromium screenshot and cog 20s survival) inside the memory cap. Provisioning uses
`david_igou.molecule_provisioners` (see `molecule/playbook-grafana-kiosk/inventory/`);
pick a backend with `PROVISIONER`:

| `PROVISIONER` | Guests | Notes |
|---|---|---|
| `podman` (default) | systemd containers, cgroup-capped at 512m | Broken in the igou devcontainer (nested rootless podman has no cgroup delegation) — use `docker` there |
| `docker` | same containers via host-side docker | Enforces the memory cap everywhere |
| `kubevirt` | real 512MiB VMs on the cluster (`containerdisks/debian:12` for chromium, `debian:13` + virtio display for cog) | Needs a `KUBECONFIG` with VM + Service CRUD in the `molecule` namespace (ansible-molecule SA: `op://claude/ocp-ansible-molecule/token`). The zram/sysctl metal paths genuinely run, and the VMs have virtual displays |

### Render testing (run without destroy)

Only `molecule test` destroys instances; the step commands leave everything
up so you can iterate on the play and eyeball what the kiosk actually
renders:

```bash
export PROVISIONER=kubevirt   # and a KUBECONFIG for the ansible-molecule SA

molecule converge -s playbook-grafana-kiosk   # create + prepare + converge; instances stay up
KIOSK_FETCH_SCREENSHOT=1 molecule verify -s playbook-grafana-kiosk   # re-runnable
```

With `KIOSK_FETCH_SCREENSHOT=1` (off by default — plain test runs leave the
render in the guest), `verify` fetches the headless Chromium render to
`$MOLECULE_EPHEMERAL_DIRECTORY/screenshots/kiosk-chromium.png` — verify
prints the exact path; it lives under
`~/.ansible/tmp/molecule.*.playbook-grafana-kiosk/`. Open it locally, tweak the play
or dashboard vars, re-run `converge`/`verify`, repeat.

On the kubevirt backend you can also watch the **live** kiosk on the VM's
virtual display. Molecule defaults to `kiosk_start_browser: false` (units
enabled but stopped); override it and attach VNC:

```bash
molecule converge -s playbook-grafana-kiosk -- -e kiosk_start_browser=true
virtctl vnc -n molecule kiosk-cog        # or kiosk-chromium
```

> **Cloud-kernel caveat:** the containerdisk ships Debian's `cloud` kernel,
> which has no DRM drivers — `/dev/dri` is missing and the browser units
> can't render to the display. `apt install linux-image-amd64 && apt purge
> "linux-image-*cloud*"` + `sudo reboot` in the guest first, or stick to
> the headless screenshot loop.
>
> **cog specifics:** `kiosk-cog` boots `debian:13` with a virtio display
> (per-host override in `inventory/hosts.yml`): Bookworm's cog 0.16
> segfaults on virtio-gpu, and Mesa can't drive the default bochs VGA at
> all. The molecule inventory also presets `kiosk_cog_env` with
> `WEBKIT_DISABLE_DMABUF_RENDERER=1` + `WEBKIT_DISABLE_COMPOSITING_MODE=1`
> — WebKit's dmabuf renderer and accelerated compositing both crash on
> virtual GPUs. Don't set either on real hardware.
>
> **cog + real Grafana verdict (2026-07-10 VM bench):** cog renders simple
> pages (the mock) fine on the virtio display, but crash-loops 10–60s into
> the real Grafana app in `libcogplatform-drm` buffer handling, under every
> renderer combination (dmabuf off, compositing off, llvmpipe). Use the
> **chromium** guest for live-Grafana render testing — playlist rotation
> verified working there. The cog-on-real-hardware bench gate (Pi vc4)
> remains open; this VM result raises its risk.
>
> **Ephemeral-disk caveat:** containerdisk roots survive a guest-initiated
> `sudo reboot`, but anything that recreates the virt-launcher pod
> (`virtctl restart`, node drain) resets the VM to a fresh image — re-run
> `molecule prepare --force` and `molecule converge` afterwards.

To get a shell, use `virtctl ssh debian@vmi/kiosk-cog -n molecule`, or plain
`ssh` with the host/NodePort/key the provisioner wrote to
`$MOLECULE_EPHEMERAL_DIRECTORY/inventory/` + `identity_file`. When you're
done:

```bash
molecule destroy -s playbook-grafana-kiosk
```

(`molecule test -s playbook-grafana-kiosk --destroy=never` gives the same
keep-alive behavior for a full one-shot run.)
