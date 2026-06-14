# kubevirt_vm_snapshot

Manage the lifecycle of KubeVirt `VirtualMachineSnapshot` and
`VirtualMachineRestore` objects for any VM via the OpenShift API.

The role exposes a single `snapshot_action` switch that selects one of four
operations — `create`, `restore`, `list`, or `prune` — against a target VM
identified by `vm_name` in `vm_namespace`. It is designed to be called from
higher-level playbooks (for example, the Hermes VM lifecycle automation) so
the same snapshot/restore/retention logic is reused everywhere.

## Requirements

- An execution environment with the `kubernetes.core` collection and the
  KubeVirt CRDs (`snapshot.kubevirt.io`) present on the target cluster.
- Cluster credentials supplied the usual way (`KUBECONFIG`, or the
  `K8S_AUTH_HOST` / `K8S_AUTH_API_KEY` environment variables).
- For an online (guest-agent-quiesced) snapshot, the guest agent must be
  running inside the VM. CNV decides online vs offline automatically from the
  VM's running-state and guest-agent presence — `VirtualMachineSnapshot` has no
  online/offline flag, so the role does not expose one.

## Role variables

Every variable below is defined in `defaults/main.yml`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `vm_name` | `""` | Name of the target `VirtualMachine`. Required for every action. |
| `vm_namespace` | `""` | Namespace of the target VM. Required for every action. |
| `snapshot_action` | `list` | Operation to run: `create`, `restore`, `list`, or `prune`. |
| `snapshot_name` | `""` | For `create`, optional — defaults to `<vm_name>-<UTC ts>`. For `restore`, required — selects which snapshot to restore. |
| `snapshot_prune_after_create` | `false` | After a successful `create`, prune the VM's snapshots down to `snapshot_retention_count`. Off by default so a manual one-off `create` never silently deletes older snapshots; the nightly AAP schedule sets this to `true` to enforce retention. |
| `snapshot_retention_count` | `7` | Number of newest snapshots to keep for this VM when pruning. |
| `snapshot_restore_confirm` | `false` | Safety guard — must be `true` for a `restore` to proceed. |
| `snapshot_restore_restart` | `true` | After a restore, restore the VM's prior `runStrategy`. |
| `snapshot_wait_timeout` | `600` | Seconds to wait for a snapshot or restore to reach a ready/completed state. |
| `snapshot_labels` | `app.kubernetes.io/managed-by: ansible` | Labels applied to objects the role creates. |

## Actions

| Action | Required vars | Effect |
| --- | --- | --- |
| `create` | `vm_name`, `vm_namespace` | Creates a `VirtualMachineSnapshot` (named `snapshot_name`, or `<vm_name>-<UTC ts>` if unset). CNV auto-selects online vs offline. When `snapshot_prune_after_create` is `true` (off by default), prunes to `snapshot_retention_count` afterward. |
| `restore` | `vm_name`, `vm_namespace`, `snapshot_name`, `snapshot_restore_confirm: true` | Creates a `VirtualMachineRestore` from `snapshot_name`. **Double-guarded:** both `snapshot_name` and `snapshot_restore_confirm: true` are required, or the action refuses to run. Restores the prior `runStrategy` when `snapshot_restore_restart` is `true`. |
| `list` | `vm_name`, `vm_namespace` | Read-only. Lists the `VirtualMachineSnapshot` objects for the VM, newest first. Default action. |
| `prune` | `vm_name`, `vm_namespace` | Deletes the oldest snapshots for the VM, keeping the newest `snapshot_retention_count`. |

Notes:

- `restore` is intentionally hard to trigger by accident: it requires an
  explicit `snapshot_name` **and** `snapshot_restore_confirm: true`.
- `create` does **not** prune by default (`snapshot_prune_after_create: false`),
  so a manual one-off `create` never silently deletes older snapshots. The
  nightly AAP schedule sets `snapshot_prune_after_create: true` so a scheduled
  "create" keeps retention bounded without a separate `prune` run.

## Example invocations

Both examples assume a playbook `playbooks/kubevirt/snapshot.yml` that includes
this role and that cluster credentials are present in the environment.

Create a snapshot (CNV picks online vs offline; pass
`snapshot_prune_after_create=true` to also prune to retention):

```yaml
---
- name: Snapshot a KubeVirt VM
  hosts: localhost
  gather_facts: false
  roles:
    - role: kubevirt_vm_snapshot
      vars:
        vm_name: hermes
        vm_namespace: hermes-agent
        snapshot_action: create
```

```bash
ansible-navigator run playbooks/kubevirt/snapshot.yml \
  -e vm_name=hermes \
  -e vm_namespace=hermes-agent \
  -e snapshot_action=create
```

Restore from a specific snapshot (double-guarded):

```bash
ansible-navigator run playbooks/kubevirt/snapshot.yml \
  -e vm_name=hermes \
  -e vm_namespace=hermes-agent \
  -e snapshot_action=restore \
  -e snapshot_name=hermes-20260614T120000Z \
  -e snapshot_restore_confirm=true
```
