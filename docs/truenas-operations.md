# TrueNAS operations runbook

Day-2 operations on `truenas.igou.systems` (the homelab's primary TrueNAS
SCALE host). Covers Docker container management, users, NFS netboot, API
sanity, and the deprecated playbooks.

## Connection facts

| Item | Value |
|---|---|
| Inventory group | `truenas` (single host: `truenas.igou.systems`) |
| Connection | SSH; `become: true` works |
| Container runtime | **Docker** (TrueNAS SCALE; not podman) |
| Container naming | TrueCharts: `ix-<app>-<service>-<n>` (e.g. `ix-netbootxyz-netbootxyz-1`) |
| Bind-mount root | `/mnt/<pool>/containers/<service>/` |
| Pool | `ssd` for warm storage; `tank` for cold (verify in inventory before assuming) |

## Playbooks

| Playbook | What it does | Run frequency |
|---|---|---|
| [`configure_docker_containers.yml`](#docker-containers) | Provisions ZFS datasets, Docker networks, deploys compose files | On every container add/update |
| [`configure_users.yml`](#users) | Manages local TrueNAS users via `arensb.truenas.user` | Rare; run after adding/removing users |
| [`configure_netboot_nfs.yml`](#nfs-netboot) | NFS export + service for armbian rootfs hosting | Once for setup; rare after |
| [`api_test.yml`](#api-smoke-test) | Smoke-test `midclt` against the TrueNAS middleware | When debugging connection issues |
| `configure_netbootxyz.yml` | **DEPRECATED** — replaced by `playbooks/netboot/deploy_assets.yml` | Don't run; left for reference |
| `sync_boot_files.yml` | **DEPRECATED** — folded into `deploy_assets.yml` | Don't run |

## Docker containers

`configure_docker_containers.yml` is the single playbook for adding /
updating containers on TrueNAS.

### What it does (in order)

1. Reads `truenas_docker_networks` from group_vars and creates each via
   `community.docker.docker_network`.
2. Reads `truenas_docker_containers` (a list of dicts: name, dataset paths,
   compose file template). For each:
   - Creates the ZFS dataset(s) under `/mnt/<pool>/containers/<name>/`.
   - Sets owner/group (typically `1000:1000` for linuxserver-style images).
   - Renders the compose file from a Jinja template into the dataset.
   - `docker compose up -d` against the rendered file.

### Add a new container

1. Edit `igou-inventory/group_vars/truenas.yml`. Add an entry to
   `truenas_docker_containers`:
   ```yaml
   truenas_docker_containers:
     - name: my-app
       compose_template: my-app/compose.yaml.j2     # under playbooks/truenas/templates/
       datasets:
         - "{{ truenas_pool_default }}/containers/my-app/config"
         - "{{ truenas_pool_default }}/containers/my-app/data"
       owner: 1000
       group: 1000
       network: my-app-net
   ```
2. Drop the compose template at `playbooks/truenas/templates/my-app/compose.yaml.j2`.
3. Optionally add the network to `truenas_docker_networks`.
4. Deploy:
   ```bash
   ansible-playbook playbooks/truenas/configure_docker_containers.yml \
     -i igou-inventory/inventory.yaml
   ```

### Update a container's image

Renovate-bot opens PRs against compose templates when image tags publish a
new digest. Merge the PR and re-run the playbook. The compose `up -d` pulls
the new image and recreates the container.

### Remove a container

The playbook is **add-only**. To remove:
1. Delete the entry from `truenas_docker_containers` and the compose template.
2. Manually on TrueNAS:
   ```bash
   ssh truenas 'docker compose -f /mnt/ssd/containers/<name>/compose.yaml down -v'
   ssh truenas 'sudo zfs destroy -r ssd/containers/<name>'
   ```
3. Commit the inventory removal.

## Users

`configure_users.yml` uses `arensb.truenas.user` (non-deprecated parameters
only — TrueNAS API renamed several around 24.04).

### Add / change a user

Edit `igou-inventory/group_vars/truenas.yml`:

```yaml
truenas_users:
  - name: alice
    full_name: Alice
    uid: 3001
    home: /mnt/ssd/home/alice
    shell: /usr/bin/zsh
    password_disabled: false
    sudo_commands_nopasswd: []
    ssh_authorized_keys:
      - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5..."
```

Run:
```bash
ansible-playbook playbooks/truenas/configure_users.yml \
  -i igou-inventory/inventory.yaml
```

The role is idempotent. Removing a user from the list does NOT delete it
from TrueNAS (use the UI for deletes, or set `state: absent` on the entry).

### Privileges (role-based access) and user-linked API keys

`configure_users.yml` also manages TrueNAS RBAC privileges and user-linked
API keys. The `arensb.truenas` collection has no modules for `privilege.*` /
`api_key.*`, so these are `midclt call` tasks
(`playbooks/truenas/tasks/truenas_privilege.yml` and `truenas_api_key.yml`)
with query-first idempotency.

Driven by two group vars in `igou-inventory/group_vars/truenas.yml`:

```yaml
truenas_privileges:
  - name: democratic-csi   # bound to local groups, grants roles
    web_shell: false
    local_groups:          # group names; resolved to gids at runtime
      - csi
    roles:                 # catalog: midclt call privilege.roles
      - DATASET_WRITE
      # ...

truenas_api_keys:
  - name: democratic-csi-ocp
    username: csi          # key inherits this user's roles
    # expires_at: <ISO-8601>  # optional
```

Notes:
- Privileges are fully reconciled (create + update on role/group/web_shell
  drift). Removing an entry does NOT delete the privilege on TrueNAS.
- **API key material is printed exactly once, at creation** — store it in
  1Password immediately; `api_key.query` never returns it again. To rotate,
  delete the key (`midclt call api_key.delete <id>`) and re-run.
- A user-linked key inherits the roles of its user's groups' privileges, so
  scoping a key = scoping the user. Prefer one key per consuming
  cluster/system so each can be revoked independently.
- `--check` mode works: queries still run, mutations are skipped. On a
  first run the privilege's group-existence assert is skipped too (the
  group hasn't been created yet in a dry-run).

## NFS netboot

`configure_netboot_nfs.yml` provisions ZFS datasets, an NFS export, and the
NFS service for the `armbian_netboot` collection's `nfs_content` role to
write per-board rootfs trees over SSH.

Driven by `truenas_netboot_*` group vars (already defined in
`igou-inventory/group_vars/truenas.yml`):
- `truenas_netboot_pool` (default `ssd`)
- `truenas_netboot_datasets` (parent + per-purpose datasets, with optional
  per-dataset `atime`/`sync`/`compression`/`recordsize`)
- `truenas_netboot_share_networks` (CIDRs allowed to mount the export)
- `truenas_netboot_required_tools` (CLI tools nfs_content depends on)

Run once during initial setup; re-run if dataset list or networks change.
The NFS service must already be running with NFSv4 + NFSv3 ownership model
enabled (configure manually under Services → NFS in the UI).

```bash
ansible-playbook playbooks/truenas/configure_netboot_nfs.yml \
  -i igou-inventory/inventory.yaml
```

This is part of the broader Armbian SD-card flow (orange-pi-5-pro and
similar boards). The Ansible side is in place; the operator-facing
end-to-end Armbian runbook isn't written yet.

## API smoke test

`api_test.yml` runs a few `midclt call <method>` commands to verify the
TrueNAS middleware is responsive. Run after a TrueNAS upgrade, or when an
Ansible run starts failing at the API layer.

```bash
ansible-playbook playbooks/truenas/api_test.yml \
  -i igou-inventory/inventory.yaml
```

`midclt` is the TrueNAS middleware CLI client (`truenas/api_client`). It
uses the local Unix socket; no network credentials needed. Ansible runs
it via SSH + `become: true`.

## Snapshots and backups

Not driven by Ansible. Configured in the TrueNAS UI under Periodic Snapshot
Tasks and Replication Tasks. **Currently (2026-05-10) not documented here**
— if you find yourself needing to recover, check the UI directly.

Common datasets to verify retention on:
- `ssd/containers/*` (compose state, persistent volumes)
- `ssd/home/*` (per-user homes if any)
- `ssd/<service>` for important services

## Cross-references

- netboot.xyz container ops → [`netboot-operations.md`](netboot-operations.md)
- TrueNAS rebuild from scratch → [`disaster-recovery.md`](disaster-recovery.md)
- "Container running but service unreachable" → [`troubleshooting.md`](troubleshooting.md)
