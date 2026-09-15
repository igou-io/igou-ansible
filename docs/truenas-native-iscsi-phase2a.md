# TrueNAS native-iSCSI Phase 2A operations

This runbook covers the manually managed native-iSCSI application-config
volumes from igou-inventory#827. The source of truth is
`igou-inventory/group_vars/truenas.yml`; reconciliation is
`playbooks/truenas/configure_iscsi_volumes.yml`.

The permanent portal is `10.10.9.243:3260` on the existing TrueNAS iSCSI
portal. The managed targets are `qbittorrent-config` and `prowlarr-config`,
each with a DISK extent and LUN 0. The actual generated target IQNs are
recorded in the non-secret mapping artifact in igou-inventory. Do not derive
an IQN from a target name when attaching a PV; use SendTargets discovery or
that recorded mapping.

## Safety boundary

These are native iSCSI LUNs, not democratic-csi volumes. Native iSCSI has no
CSI attachment fencing. The same ext4 LUN must never be writable from two
nodes at once. Before moving a PVC, prove that the old node has no session and
no matching `/dev/disk/by-path` device; only then allow the next node to mount.

Do not use this path for media data, `/mnt/cold/media/data`, NFS, a
StorageClass, or production migration work. The Phase 2A zvols must remain
empty until the separate OpenShift migration is approved.

TrueNAS 25.10.1 rejected the target-group `auth_networks` field during the
disposable test, so no source-network restriction is active. The future PV
must use the exact LAN portal destination documented here, while target
authorization is enforced by the exact measured initiator IQNs and dedicated
CHAP. Do not modify the shared wildcard portal to change this without a
separate change plan, because existing iSCSI consumers use it.

## Inspect sessions and identify the holder

On TrueNAS, list only the relevant sessions:

```bash
ssh truenas_admin@truenas.igou.systems \
  'midclt call iscsi.global.sessions | jq -c '\''[.[] | select(.target == "<full-target-iqn>")]'\'''
```

The result identifies the initiator IQN and source address. On an OpenShift
node, inspect the exact session and device:

```bash
oc debug -n default node/<node> -- chroot /host \
  iscsiadm -m session
oc debug -n default node/<node> -- chroot /host \
  find /dev/disk/by-path -maxdepth 1 -type l -name '*<target-iqn>*' -print
```

Match the returned initiator IQN against the eligible-node mapping. The
control-plane node is not eligible merely because it also carries a worker
label.

## OpenShift Secret and PV contract

The dedicated CHAP credential is stored in 1Password and is never committed
or printed. The future namespace Secret must contain exactly these keys:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: <iscsi-chap-secret>
  namespace: <workload-namespace>
type: Opaque
stringData:
  node.session.auth.username: qbittorrent-config
  node.session.auth.password: <1Password password, never logged>
```

The static native-iSCSI PV references that Secret by name and uses:

```yaml
iscsi:
  targetPortal: 10.10.9.243:3260
  iqn: <full-target-iqn-from-mapping>
  lun: 0
  fsType: ext4
  readOnly: false
  chapAuthSession: true
  chapAuthDiscovery: false
  secretRef:
    name: <iscsi-chap-secret>
```

Do not put the credential in a PV, Git, an issue, a log, or a command-line
argument. There is intentionally no StorageClass for these volumes.

## Clean logout and stale-session handling

Delete or stop the workload first and wait for kubelet to remove the mount.
The normal native plugin sequence is unmount, logout, delete the node record,
and detach the device. Verify both the old node and TrueNAS before a move:

```bash
oc debug -n default node/<old-node> -- chroot /host \
  iscsiadm -m session
oc debug -n default node/<old-node> -- chroot /host \
  find /dev/disk/by-path -maxdepth 1 -type l -name '*<target-iqn>*' -print
ssh truenas_admin@truenas.igou.systems \
  'midclt call iscsi.global.sessions'
```

If a session is stale, use the exact target and portal only:

```bash
oc debug -n default node/<node> -- chroot /host \
  iscsiadm -m node -T <full-target-iqn> -p 10.10.9.243:3260 --logout
oc debug -n default node/<node> -- chroot /host \
  iscsiadm -m node -o delete -T <full-target-iqn> -p 10.10.9.243:3260
```

Recheck the session, device, kubelet log, and TrueNAS session list. Do not use
`--logoutall=all` on a production node: it can affect unrelated iSCSI
consumers.

## Node failure and fencing

If an eligible node fails while holding a LUN, do not immediately mount that
LUN on another node. First fence the failed node using the appropriate
physical/VM power and network controls, then confirm from TrueNAS that the
target session is gone. If TrueNAS still reports a session, treat the LUN as
in use. Only after the old session and device are absent may the replacement
node mount the ext4 filesystem. Native iSCSI does not provide a CSI controller
to perform this fencing for us.

## Snapshots, expansion, and recovery

`ssd/trash` is recursively snapshotted hourly, retained for 24 hours, and
daily, retained for 14 days. These settings are declared in inventory and
converged by `configure_snapshots.yml`; they are intentional for small
application-config/SQLite workloads, not inherited media settings.

For an operational snapshot, first stop writes and verify the LUN is not
mounted from any node, then use the normal TrueNAS snapshot workflow. Do not
rollback a snapshot while a node has the ext4 filesystem mounted.

Expansion is grow-only. Increase the declared `volsize` in inventory and run
the iSCSI volume playbook, verify the new LUN size, then perform the future
OpenShift-side block/filesystem expansion while the workload is stopped or
using its documented online expansion procedure. Never shrink a live zvol or
ext4 filesystem.

For recovery, fence every possible writer, verify sessions are gone, preserve
the current state with a snapshot, and restore/rollback only after the LUN is
unmounted everywhere. Run `fsck` from one maintenance node before remounting;
then verify the application-specific SQLite/config recovery. Do not copy the
current production qBittorrent config into these empty Phase 2A zvols.

## Ordered destructive cleanup

Destruction requires a deliberate maintenance window and an empty/no-session
assertion:

1. Stop the future workload and delete its mount consumer.
2. Verify no eligible node has a session or matching device.
3. Delete the target/extent association.
4. Delete the target.
5. Delete the DISK extent.
6. Delete the dedicated auth and initiator policies only when no other
   managed target references them.
7. Preserve or delete snapshots according to the recovery decision.
8. Delete the zvol through the declarative playbook; delete `ssd/trash` only
   after all children are absent.

Never destroy the shared portal or the existing democratic-csi target groups
as part of this cleanup.
