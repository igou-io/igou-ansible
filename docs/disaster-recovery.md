# Disaster recovery runbook

"X is dead, what do I run?" Ordered by component, with dependencies between
them noted up front.

## Dependency order (rebuild from total loss)

If the homelab burned to the ground, components must come back roughly in
this order — each depends on the ones above it being alive:

1. **`rb5009`** — DHCP, DNS, gateway, TFTP (iPXE binaries + per-host pin
   files). Without this nothing PXE-boots.
2. **`truenas`** — public nginx container (HTTPS for menu/entries/kickstart/
   cloud-init/ocp/ocp-add-node/images/iso), NFS, container datasets, ZFS
   storage. Most other services live here.
3. **public nginx container on truenas** — needed for unpinned-host menu
   fallback and for asset URLs that pin fragments reference.
4. **rb5009 iPXE binaries** — embedded HTTPS fallback URL points at public
   nginx (`public.igou.systems/boot-files`). Rebuild only after truenas is
   reachable.
5. **OCP cluster** — depends on rb5009 + truenas (PXE) and the rendezvous
   host being able to boot.
6. **Homelab pets** (helpernode, p330, hpg5 if not OCP) — PXE-driven; depend
   on rb5009 + truenas.

Backups for `rb5009` and the inventory itself are the only things you cannot
rebuild from elsewhere — protect those first.

---

## rb5009

**Single point of failure.** The homelab can't boot anything without it.

### Backup

`playbooks/routeros/backup.yml` exports a configuration backup to
`flash:/backup-<timestamp>.backup` and pulls it down to the control node:

```bash
ansible-playbook playbooks/routeros/backup.yml \
  -i igou-inventory/inventory.yaml
```

Backups land under `~/routeros-backups/<host>-<timestamp>.backup` on the
control node. **Move these off the homelab regularly** — to 1Password,
external git, USB, etc. The backup is the lifeline.

### Restore

A clean RouterOS install + the backup file:

1. Boot the rb5009 onto a known-good RouterOS version (matching what the
   backup was taken on).
2. Initial setup: enable Routeros, get an IP that lets you reach it.
3. Upload the backup via Winbox / FTP / SCP.
4. `/system backup load <name>=backup-<timestamp>.backup`.
5. Reboot.
6. Re-run `playbooks/routeros/baseline.yml` to confirm baseline drift is zero.

After restore: re-run `deploy_netboot_binaries.yml` if the binaries on
rb5009's flash got wiped (they live under `flash:/netboot/` and survive
config restores, but not factory resets).

### Upgrade

Two-step (download then apply on a maintenance window):

```bash
# 1. Stage the upgrade package (no reboot)
ansible-playbook playbooks/routeros/upgrade_download.yml \
  -i igou-inventory/inventory.yaml

# 2. During maintenance — reboots the device
ansible-playbook playbooks/routeros/upgrade_apply.yml \
  -i igou-inventory/inventory.yaml
```

Always take a backup first. Channel is `routeros_upgrade_channel: stable` per
inventory.

---

## truenas

The hardest to rebuild from scratch. Most homelab services live here.

### What's on truenas

- ZFS pools: `ssd` (warm/services), and possibly `tank` (cold/bulk).
- Datasets under `ssd/containers/<service>/` for every Docker container.
- The public nginx container (`ix-public-public-1`) and its bind-mount at
  `/mnt/ssd/public/` — serves netboot HTTPS assets (and other public-facing
  HTTPS content).
- Local user homes (if any).
- NFS exports (used by armbian netboot).

### Backup

Snapshot tasks configured in the TrueNAS UI. **As of 2026-05-10, the
retention policy and replication targets are not documented in this repo
— check the UI under Periodic Snapshot Tasks and Replication Tasks.**

Inventory side is also a "backup": `igou-inventory` repo has the desired
state of containers, users, NFS exports, etc. Re-running the configure_*
playbooks recreates the structure.

### Restore — TrueNAS itself

1. Reinstall TrueNAS SCALE on the same hardware (or a replacement).
2. Import the existing ZFS pool (`ssd`) — assuming the disks survived. If
   they didn't, restore from snapshot replica or rebuild empty.
3. Restore TrueNAS config from the UI's config backup file.
4. Re-run the inventory-driven setup playbooks (in order):
   ```bash
   ansible-playbook playbooks/truenas/configure_users.yml \
     -i igou-inventory/inventory.yaml
   ansible-playbook playbooks/truenas/configure_docker_containers.yml \
     -i igou-inventory/inventory.yaml
   ansible-playbook playbooks/truenas/configure_netboot_nfs.yml \
     -i igou-inventory/inventory.yaml   # if NFS netboot is in use
   ```
5. If the public nginx container didn't survive: see "netboot assets" below.
6. Verify dataset ownership: public nginx expects files readable by the
   nginx user (typically uid 101 inside the container; on the host,
   anything 0755 dirs + 0644 files works regardless of owner).

### Restore — just one container

```bash
# Bring the existing container down (preserves volumes)
ssh truenas 'docker compose -f /mnt/ssd/containers/<name>/compose.yaml down'

# If the container's config got corrupted: zfs rollback to a known-good snapshot
ssh truenas 'sudo zfs rollback ssd/containers/<name>@<snap>'

# Re-run the playbook to bring it back up
ansible-playbook playbooks/truenas/configure_docker_containers.yml \
  -i igou-inventory/inventory.yaml
```

---

## netboot assets

Two storage layers, both recoverable from inventory + playbooks:

- **Public nginx HTTPS assets** at `/mnt/ssd/public/boot-files/` on truenas
  (menu.ipxe, entries, fragments, kickstart, cloud-init, ocp/, ocp-add-node/,
  images/, iso/). Container = `ix-public-public-1` (TrueCharts/compose),
  declarative spec in `igou-inventory/group_vars/truenas.yml`.
- **rb5009 TFTP files** at `flash:/netboot/` (iPXE binaries + `per-host/`
  pin files), with matching `/ip tftp` rows. Declarative spec across
  `igou-inventory/group_vars/{routeros,all}/netboot.yml` and the routeros
  build playbook.

### Restore

```bash
# 1. Make sure the public nginx container itself is up
ansible-playbook playbooks/truenas/configure_docker_containers.yml \
  -i igou-inventory/inventory.yaml

# 2. Rebuild + upload iPXE binaries to rb5009 (also clones netboot.xyz source
#    if .cache/netboot-build/ was lost)
ansible-playbook playbooks/routeros/deploy_netboot_binaries.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml

# 3. Re-render and push menu.ipxe + entries + kickstart + cloud-init + ISOs
#    to public nginx, AND per-host pins to rb5009
ansible-playbook playbooks/netboot/deploy_assets.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml

# 4. Smoke-test
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml -e 'pxe_test_parallel=true'
```

### Big-asset trees (ocp/, ocp-add-node/, images/) are NOT in this repo

These are generated by other playbooks or by external workflows:
- `ocp/agent.x86_64-{vmlinuz,initrd,rootfs}.img` — written by
  `playbooks/openshift/agent-install/deploy_pxe_assets.yml` (the initial
  cluster install).
- `ocp-add-node/node.x86_64-{vmlinuz,initrd,rootfs}.img` — written by
  `playbooks/openshift/add_node_iso.yml --tags pxe-assets` per cluster
  (regenerate per cluster when restoring).
- `images/orangepi*/` — written by the Armbian build playbook.

If they're lost, re-run the source playbook for each. If the OCP cluster
itself is gone, you'll regenerate them as part of the cluster install
(see [`openshift-operations.md`](openshift-operations.md)).

---

## rb5009 iPXE binaries

```bash
ansible-playbook playbooks/routeros/deploy_netboot_binaries.yml \
  -i igou-inventory/inventory.yaml
```

Builds locally, uploads to `flash:/netboot/`, wires DHCP option-66/67, and
verifies. Run when:
- After a rb5009 factory reset (config restore alone doesn't restore
  flash content).
- The chainload URL changes.
- The upstream iPXE bundle gets a security fix.

See [`netboot-operations.md`](netboot-operations.md) for details.

---

## OCP cluster

### Save before you lose it

The cluster's auth files (`kubeconfig`, `kubeadmin-password`) live in
1Password under the `awx` vault, written by
`agent-install/deploy_pxe_assets.yml` at install time. Verify they're there
periodically:

```bash
op read "op://awx/<cluster>-kubeconfig/credential" | head -3
```

The `ansible` ServiceAccount token (used by AAP/AWX) is at
`onepassword-sdk-<cluster>-push-token` — written by
`bootstrap_openshift_gitops.yaml`.

### Rebuild from scratch

If the cluster is unrecoverable:

1. Ensure rb5009 + truenas + public nginx are healthy (see "netboot assets" above).
2. Edit `host_vars/<cluster>.yml` if anything's changed (version, network
   plan, rendezvous MAC).
3. Run the full agent-install flow — see
   [`openshift-operations.md`](openshift-operations.md#initial-cluster-agent-install).
4. After ready, re-bootstrap GitOps:
   ```bash
   export KUBECONFIG=~/openshift-agent-install/<cluster>/cluster-manifests/auth/kubeconfig
   ansible-playbook playbooks/openshift/bootstrap_openshift_gitops.yaml \
     -i igou-inventory/inventory.yaml -e target_cluster=<cluster>
   ```
5. Re-add workers via `add_node_iso.yml` for each one in
   `openshift_workers_<cluster>`.

### Lost a single worker

Just re-PXE-boot it. The `MAC-<hex>.ipxe` pin on rb5009 chains it into the
add-node ISO (served via HTTPS from public.igou.systems); CoreOS reinstalls;
CSR approval finishes the loop. Pre-existing pod tolerations / PVCs are
recreated by GitOps.

---

## Homelab pets (helpernode, p330, etc.)

Each is configured via a per-host `netboot_host_pins` entry in
`igou-inventory/group_vars/all/netboot.yml`. Most autoinstall CentOS Stream
10 with a kickstart from `playbooks/netboot/files/kickstart/<host>.cfg`.

To rebuild one:
1. Confirm its kickstart is current in `playbooks/netboot/files/kickstart/`.
2. PXE-boot the host.
3. Watch rb5009's TFTP hit counter for the pin file:
   ```bash
   SSH_AUTH_SOCK= ssh -i ~/.ssh/id_ed25519 igou@rb5009.igou.systems -p 3480 \
     "/ip tftp print detail without-paging where req-filename~\"MAC-\""
   ```
4. Wait for autoinstall to complete (10-30 min depending on hardware).
5. If the host should re-join a higher-level service (k3s, monitoring),
   trigger the corresponding playbook against it.

---

## Inventory itself

The `igou-inventory` repo IS the configuration database. Loss of inventory
means loss of "what should be running where."

- **Always pushed** — the working tree should never be ahead of `origin/main`
  for long. Push small atomic commits.
- **Mirror** — the GitHub repo is the canonical store. If GitHub is
  unavailable, the local checkout on the control node is the fallback.
- **Secrets are NOT in the repo** — they're 1Password references. Losing
  the 1Password account is a separate disaster scenario; recover from
  emergency-kit-recorded credentials.

---

## Test the recovery procedure

The headless smoke test exercises the rb5009 → public-nginx PXE path
end-to-end without risking real hardware:

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i 'localhost ansible_connection=local,' \
  -i igou-inventory/inventory.yaml -e 'pxe_test_parallel=true'
```

`failed=0` means rb5009 + truenas + public nginx + the iPXE chain are all
functional end-to-end. Run after any DR exercise to confirm the homelab is
back.
