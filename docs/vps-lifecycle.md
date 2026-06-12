# VPS (igou.io) lifecycle

The personal VPS is fully declarative: `igou-inventory/host_vars/igou.io.yml`
is the single source of truth (hardening, packages, TLS, rootless podman
workloads, website), and AAP job templates apply it. Nothing on the host is
managed by hand.

## Layers and the AAP objects that maintain them

| Layer | Playbook | AAP template | Cadence |
|---|---|---|---|
| OS packages | `playbooks/system-update.yaml` | `system_update` | nightly via `update_all_linux` (vps included in the limit) |
| Patch + reboot | `playbooks/system-update.yaml` | `system_patch_cycle` | on demand (`ansible_limit=vps`) |
| Hardening / users | `playbooks/linux/baseline.yaml` | `linux_baseline` | on demand |
| Packages (podman, git, acl) | `playbooks/linux/install_packages.yaml` | `install_packages` | on demand |
| TLS cert (LE wildcard+apex) | `playbooks/linux/acme_certificate_podman_secret.yaml` | `acme_certificate_podman_secret` | weekly via `letsencrypt_renew_weekly` (no-op until <15 days remain) |
| Workload state (quadlets, secrets, nginx config) | `playbooks/linux/podman_quadlets.yaml` | `podman_quadlets` | on demand / after host_vars changes |
| Workload images | `playbooks/linux/podman_auto_update.yaml` | `podman_auto_update` | on demand (deliberate image rolls, auto-rollback) |
| Website content | `playbooks/linux/deploy_static_site.yaml` | `deploy_static_site` | nightly 20:00 via `deploy_static_site_nightly` (builds only on new commits) |
| Metrics (node_exporter) | `playbooks/linux/node_exporter.yaml` | `node_exporter` | in the converge workflow (localhost-bound, scraped over Tailscale) |
| Tailnet membership | `playbooks/tailscale/tailscale.yml` | `tailscale_join` | on demand (OAuth secret from 1Password; designated tags via `tailscale_tags` host var) |

The `vps-converge-e2e` workflow chains baseline → packages → cert → quadlets →
website in dependency order (node_exporter fans out after packages); every
node is idempotent, so it is both the fresh-host rebuild runbook and a
one-button drift fix.

## Website model

The site (github.com/david-igou/igou.io, Hugo) is built **on the VPS**:
`deploy_static_site.yaml` checks the repo out as the rootless `containers`
user, runs a one-shot `hugomods/hugo` container (the same build
`deploy/build.sh` in the site repo does by hand), and writes the output into
`~containers/containers/igou-io/html`. The `igou-io` quadlet (stock
`nginx:stable-alpine`) bind-mounts that directory read-only and is proxied at
the apex `igou.io` by the TLS-terminating reverse proxy — the cert already
carries the apex as a SAN. nginx serves files live, so publishing new content
never restarts a container.

## Workload image updates

Containers opt in with `AutoUpdate=registry` in their quadlet spec;
`podman_auto_update` then pulls newer images and restarts only updated units,
rolling back a unit that fails to come up, and prunes dangling images. It is
intentionally unscheduled — `:latest` rolls are a deliberate action. Schedule
it later if unattended rolls become acceptable.
