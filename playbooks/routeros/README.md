# RouterOS playbooks

Declarative management of the MikroTik fleet (rb5009 router + crs310/crs317/
crs328 switches) via `david_igou.routeros_configuration`. Device state lives
in `igou-inventory`: per-device `routeros_config` baselines in
`host_vars/<host>.yml`, shared policy (users, certs, passwords, upgrade
channel) in `group_vars/routeros.yml`.

## State ownership

| State | Owner | Notes |
|---|---|---|
| Config paths (L2/L3, firewall, DHCP, DNS, services, ...) | `configure.yaml` + host_vars `routeros_config` | Firewall chains + routing filters are ordered+purged (authoritative); everything else is additive (`content: ignore`) |
| Management TLS cert (`ssl-web-management`) | `manage_certificates.yaml` + `routeros_certificates` | Certs can't round-trip (no key material over the API) |
| SSH authorized keys (managed users) | `manage_users.yml` + `routeros_users` | Key material is write-only; dedup/prune by key-owner comment. `ansible-netboot`'s keys belong to the armbian_netboot collection |
| User passwords | `manage_user_passwords.yaml` + `routeros_password_specs` | Write-only: every run sets, never audits. igou's interactive password is deliberately unmanaged |
| RouterOS version | `upgrade_download.yml` (stage) → `upgrade_apply.yml` (install+reboot, maintenance window) | Channel pinned via `routeros_upgrade_channel` |
| Netboot (TFTP, flash binaries, pins) | netboot playbooks / armbian_netboot collection | Deliberately excluded from the declarative baseline |
| Backups | `backup.yml` / `backup_s3.yaml` | S3 tiers scheduled in AAP (daily/weekly/monthly, 02:30–02:40) |

## Drift loop

- `export_config.yaml` captures a device into `.cache/routeros-vars/`
  (gitignored). Factory-default/echo paths are excluded at capture time —
  see the curated list in the playbook.
- Graduation: slice proven paths from the capture into host_vars, then
  `configure.yaml --check` until `changed=0`, then apply
  (`-e routeros_pre_apply_backup=true` ships an S3 snapshot first).
- Nightly audit: AAP schedule `routeros_drift_audit_nightly` runs
  `routeros_configure_check` (check mode) at 03:00; the
  `slack-drift-summary` notification posts `host_status_counts` — a
  non-zero `changed` bucket is drift.

## Rebuilding a device from scratch

1. Factory device: set the mgmt IP/VLAN by hand, enable the plain `api`
   service (8728) temporarily.
2. `bootstrap_api_user.yaml` — creates the `ansible` API user (creds from
   the 1P `<short-host>-api` item).
3. `manage_certificates.yaml -e routeros_api_tls=false -e routeros_api_port=8728`
   — creates + signs `ssl-web-management` so api-ssl can come up.
4. `configure.yaml -e host=<device>` — applies the full `routeros_config`
   baseline (enables api-ssl/www-ssl, disables plain api, restores
   L2/L3/firewall/users/...).
5. Create any missing managed users by hand or with a one-off
   `community.routeros.api` add — RouterOS refuses `/user add` without a
   password, so the declarative `/user` path can only reconcile EXISTING
   users, never create them (api_modify has no password to send). Give the
   initial password from the user's 1P item; step 6 enforces it after.
6. `manage_user_passwords.yaml -e host=<device>` — sets managed user
   passwords from 1Password.
7. `manage_users.yml -e host=<device>` — installs SSH authorized keys.
8. rb5009 only: run the netboot playbooks to restore TFTP binaries + pins.

Connection notes: all API playbooks resolve creds once per host from the 1P
`<short-host>-api` item (vault `awx`) and talk api-ssl :8729 with
`validate_certs: false` (self-signed). SSH-based plays (backups, upgrades,
manage_users reads) use `network_cli` on port 3480.
