# RouterOS playbooks — design

**Date:** 2026-05-07
**Status:** approved (brainstorming complete)
**Scope:** four flat playbooks under `playbooks/routeros/` to manage the homelab MikroTik fleet (1 router, 3 switches), plus shared task snippets and inventory variable additions.

## Goals

- Take config + binary backups off every RouterOS device, on demand.
- Keep the `igou` SSH user's authorized keys in sync.
- Enforce a small idempotent baseline (NTP, timezone, disabled legacy services).
- Stage RouterOS package upgrades safely, then apply them in a controlled window.

## Non-goals

- Per-device firewall, VLAN, interface, bridge/bond configuration.
- SNMP and remote syslog (deferred — re-evaluate when monitoring lands).
- `admin` user lifecycle, password rotation, two-user separation.
- Backup encryption or off-host replication.
- Scheduled/automated upgrades. Apply is always operator-triggered.
- Molecule scenarios (RouterOS doesn't run in a container; fleet is 4 devices).

## Inventory & connection

Devices already inventoried in `igou-inventory/inventory.yaml`:

```
routeros:
  routeros_routers:  rb5009.igou.systems
  routeros_switches: crs310, crs317, crs328
```

Connection (`igou-inventory/group_vars/routeros.yml`):

- `ansible_connection: ansible.netcommon.network_cli`
- `ansible_network_os: community.routeros.routeros`
- `ansible_port: 3480`
- `ansible_user: igou+cet1024w` *(changed from `ansible-netboot+cet1024w`; the obsolete bootstrap comment block above the line will be pruned in the same edit)*

The `+cet1024w` terminal hint stays — it prevents network_cli timeouts on long output lines.

## File layout

```
playbooks/routeros/
  test_connection.yaml          # already there, untouched
  backup.yml                    # new
  manage_users.yml              # new
  baseline.yml                  # new
  upgrade_download.yml          # new (Phase A)
  upgrade_apply.yml             # new (Phase B)
  tasks/
    wait_for_routeros.yml       # shared: used by upgrade_apply.yml
    fetch_artifact.yml          # shared: used by backup.yml
```

Conventions for every new playbook:

- `hosts: "{{ host | default('routeros') }}"` (matches existing `test_connection.yaml`).
- `gather_facts: false` (no Python on RouterOS).
- `serial: 1` only on `upgrade_apply.yml`; the others run with default forks.
- Connection settings inherited from `group_vars/routeros.yml`.
- All device-side mutations use `community.routeros.command`. The `community.routeros.api*` modules are not used because the baseline disables the API service.

## New inventory variables

Added to `igou-inventory/group_vars/routeros.yml` with sensible defaults; overridable per host in `host_vars/`:

```yaml
routeros_backup_dir: "{{ playbook_dir }}/../../backups/routeros"
routeros_backup_retain: 30

routeros_timezone: "America/New_York"
routeros_ntp_servers:
  - 0.pool.ntp.org
  - 1.pool.ntp.org

routeros_disabled_services: [telnet, ftp, www, www-ssl, api, api-ssl]

routeros_upgrade_channel: stable

routeros_users:
  - name: igou
    group: full
    ssh_keys:
      - "ssh-ed25519 AAAA... igou@..."   # populate with real key(s)

routeros_prune_ssh_keys: false
```

`routeros_backup_dir` resolves to `<repo-root>/backups/routeros/`. The repo's `.gitignore` gets `/backups/` appended in the same change.

## Playbook designs

### `backup.yml`

Pull a binary backup + plaintext config (with sensitive values) off every RouterOS device, store on the control node, prune old copies.

Per host:

1. Generate a single timestamp at play start: `backup_ts = ansible_date_time.iso8601_basic_short` set as a fact, so all hosts share it (e.g. `20260507T143022`).
2. `/system backup save name=<host>-<ts> dont-encrypt=yes` → `<host>-<ts>.backup` on device flash.
3. `/export show-sensitive file=<host>-<ts>` → `<host>-<ts>.rsc` on device flash.
4. Fetch both via `ansible.netcommon.net_get` to `{{ routeros_backup_dir }}/<inventory_hostname>/`. (Uses the existing network_cli session — no second SSH.)
5. `chmod 0600` on the fetched files; `chmod 0700` on the directory (created on first run with that mode).
6. `/file remove` the two device-side files. `failed_when: false` so leftover artifacts from a failed previous run are cleaned up on the next success.
7. On the control node, prune: per host, keep the newest `routeros_backup_retain` `*.backup` and `*.rsc`; delete the rest.

Properties:

- **Not idempotent** by design — every run yields a fresh timestamped pair. `changed_when: true` on save/export; `changed_when: false` on fetch/chmod/prune so surprises stand out in the recap.
- **Sensitive output**: both files contain secrets. Directory `0700`, files `0600`, gitignored. The `show-sensitive` choice is deliberate so the `.rsc` file is restorable.
- **Failure isolation**: one host failing does not stop the others (`any_errors_fatal: false`, default).

Invocation:

```bash
ansible-navigator run playbooks/routeros/backup.yml -i igou-inventory/inventory.yaml
ansible-navigator run playbooks/routeros/backup.yml -i igou-inventory/inventory.yaml -e host=rb5009.igou.systems
```

### `manage_users.yml`

Keep the `igou` user's SSH keys in sync. The connection user *is* `igou`, so this hardens its own credential list.

Per host:

1. `/user print detail without-paging` → confirm `igou` exists with `group=full` (guard; should already be true on a working device).
2. `/user ssh-keys print detail without-paging` → register existing keys for `igou`.
3. For each key in `routeros_users[].ssh_keys` not already imported:
   - Write the key to `/tmp/<user>.pub` on the control node.
   - `net_put` to device flash.
   - `/user ssh-keys import public-key-file=<file> user=igou`.
   - `/file remove` on the device, delete the local tmp file.
4. **Optional prune** (`routeros_prune_ssh_keys: false` default): remove keys present on the device for `igou` that aren't in the configured list. Unmanaged users (e.g. `admin`) are never touched.

Idempotency: `community.routeros.command` returns terminal-formatted text. `community.routeros.facts` covers `/user print` (parsed into `ansible_net_users`); regex parsing of `/user ssh-keys print detail` covers the rest. Each `add`/`set`/`import` is gated by a `when:` on the prior register, so `changed: true` only fires on a real diff.

Invocation:

```bash
ansible-navigator run playbooks/routeros/manage_users.yml -i igou-inventory/inventory.yaml
```

### `baseline.yml`

Enforce a small idempotent config baseline. NTP/timezone correct; unused services disabled.

Per host:

1. **NTP client.** Read `/system ntp client print`. If `enabled=no` or server list differs from `routeros_ntp_servers`, run `/system ntp client set enabled=yes servers=<comma,list>`. RouterOS 7 syntax (your devices are current).
2. **Timezone.** Read `/system clock print`. If `time-zone-name` differs from `routeros_timezone`, run `/system clock set time-zone-name=<value>`.
3. **Disable unused services.** For each name in `routeros_disabled_services`:
   - Read `/ip service print where name=<name>`. If `disabled=no`, run `/ip service set <name> disabled=yes`.
   - `ssh` and `winbox` are explicitly excluded — never touched.
   - SSH port (3480) is not managed here — changing it mid-run would lock out the connection.
4. **Recap.** Final `community.routeros.command` reads `/system clock print` and `/ip service print`; debug-printed when run with `-v`.

Properties:

- Each `set` is gated by `when:` on the prior `print` register. Run twice → second run is all `ok`.
- An invalid service name in `routeros_disabled_services` causes RouterOS to error on the `set`. Fail loud — that's a config bug.
- We don't validate NTP sync; we only configure.

Invocation:

```bash
ansible-navigator run playbooks/routeros/baseline.yml -i igou-inventory/inventory.yaml
```

### `upgrade_download.yml` (Phase A)

Safe to run any time — stages updates without rebooting.

Per host:

1. `/system package update set channel={{ routeros_upgrade_channel }}` — only fires if current channel differs.
2. `/system package update check-for-updates once` — registers `installed-version` and `latest-version`.
3. If `installed-version == latest-version`: print "already current", end host.
4. Else: `/system package update download` — pulls the package onto the device. Does not reboot. RouterOS will apply on next reboot.
5. Final per-host report: `current → latest, downloaded: yes/no`.

Invocation:

```bash
ansible-navigator run playbooks/routeros/upgrade_download.yml -i igou-inventory/inventory.yaml
```

### `upgrade_apply.yml` (Phase B)

Operator-triggered, during a maintenance window.

Top of file: `import_playbook: backup.yml`. Guarantees a fresh backup pair exists before reboot, no exceptions.

Then: `serial: 1`. One device at a time.

Per host:

1. **Pre-flight:** `/system package update print`. Fail the host if `status` isn't "New version is available" or "System is already up to date" (no half-downloaded state). Warn (don't fail) if `latest-version > downloaded-version` — RouterOS may have shipped a newer release between Phase A and Phase B; this run will boot into the older downloaded version and Phase A is needed again.
2. Capture `pre_version = installed-version`.
3. `/system reboot` via `community.routeros.command` (the module handles RouterOS's `[y/N]` prompt). Connection drops; `failed_when: false`, `ignore_errors: true`.
4. `import_tasks: tasks/wait_for_routeros.yml` (see below).
5. Read `/system resource print`. Fail the host if `version == pre_version` (download was empty or boot rolled back).
6. **Routerboard firmware:** `/system routerboard print`. If `current-firmware != upgrade-firmware`:
   - `/system routerboard upgrade` (schedules the firmware upgrade for the next reboot).
   - Second `/system reboot`.
   - `import_tasks: tasks/wait_for_routeros.yml`.
   - Verify `current-firmware == upgrade-firmware`; fail the host if not.
7. Move on to the next host.

Properties:

- `serial: 1` plus a failed host stops downstream batches by default — no `any_errors_fatal` needed.
- Backups always exist (Phase A imported playbook), so a bricked device has a recent restore artifact.

Invocations:

```bash
ansible-navigator run playbooks/routeros/upgrade_apply.yml -i igou-inventory/inventory.yaml
ansible-navigator run playbooks/routeros/upgrade_apply.yml -i igou-inventory/inventory.yaml -e host=crs310.igou.systems
```

## Shared task files

### `playbooks/routeros/tasks/wait_for_routeros.yml`

Used only by `upgrade_apply.yml`.

1. `wait_for` (control-node side) on `inventory_hostname`, port 3480, `delay: 30, timeout: 600`. Waits for SSH to close, then come back.
2. `meta: reset_connection` to drop the stale network_cli session.
3. Smoke test: `community.routeros.command: /system identity print`.

### `playbooks/routeros/tasks/fetch_artifact.yml`

Used by `backup.yml`. Wraps:

1. `ansible.netcommon.net_get` (device → control node).
2. `ansible.builtin.file mode=0600` on the local copy (`delegate_to: localhost`).
3. `community.routeros.command: /file remove <name>` cleanup on the device.

Parameterized by remote filename, local destination directory.

## Secrets posture

- `backups/` contains `.backup` (binary, includes secrets) and `.rsc` (plaintext, includes secrets via `show-sensitive`). Directory `0700`, files `0600`. Gitignored. Documented in a top-of-file comment in `backup.yml`.
- SSH public keys in `routeros_users` are not secret — inline plaintext in `group_vars/routeros.yml` is fine.
- No new vault files needed.

## Testing strategy

No molecule. Manual verification per playbook, in this order:

1. `test_connection.yaml` — already works, sanity check.
2. `manage_users.yml` against one switch (`-e host=crs310.igou.systems`) → run twice; second run reports 0 changed.
3. `baseline.yml` against one switch → same idempotency check.
4. `backup.yml` against one switch → confirm files land at `backups/routeros/crs310.igou.systems/`, mode `0600`, directory `0700`.
5. `backup.yml` against the full group → all 4 hosts' files appear.
6. `upgrade_download.yml` against the full group (safe — only downloads).
7. `upgrade_apply.yml` against one switch during a maintenance window — verify reboot, version bump, firmware bump.

`ansible-lint --profile=production` and `yamllint` clean before commit. Pre-commit hook runs both.

## Repo changes outside `playbooks/routeros/`

- `.gitignore`: append `/backups/`.
- `igou-inventory/group_vars/routeros.yml`:
  - Change `ansible_user` from `ansible-netboot+cet1024w` to `igou+cet1024w`.
  - Prune the obsolete bootstrap comment block.
  - Append the `routeros_*` variable block listed under "New inventory variables".
- `igou-inventory/` is a separate repo (symlinked into the workspace). The change is committed there, not in `igou-ansible`.

## Documentation

No new README. Each playbook gets a short header comment block explaining purpose and any `-e` overrides. Matches the convention in `playbooks/openshift/`, `playbooks/truenas/`, etc.

## Open items resolved during brainstorming

- Backup destination: control node, in-repo gitignored path.
- Backup artifacts: binary `.backup` + `/export show-sensitive` `.rsc`. Both are sensitive.
- Retention: timestamped, keep N most recent per host.
- User scope: only `igou` (no separate `ansible-netboot` account; no `admin` lifecycle).
- Baseline scope: NTP + timezone + disabled legacy services. No SNMP, no syslog.
- Upgrade strategy: two-phase. Channel default: `stable`.
- File layout: flat playbooks (no role, no collection).
