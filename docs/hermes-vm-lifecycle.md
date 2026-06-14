# Hermes VM lifecycle

Operator runbook for provisioning and snapshotting the **Hermes** KubeVirt VM
on `ocp.igou.systems`. Phase 2a ships the VM object and its snapshot/restore
lifecycle; **guest convergence (Phase 2b) is out of scope** — the VM boots
SSH-ready via cloud-init and nothing more.

- Playbooks: `playbooks/hermes/provision-vm.yml`, `playbooks/hermes/snapshot-vm.yml`
- VM spec vars: `playbooks/hermes/vars/hermes-vm.yml`
- Snapshot/restore logic: `roles/kubevirt_vm_snapshot/` (see its `README.md` for
  the full variable contract)
- All commands assume CWD `/workspace/igou-ansible` with `KUBECONFIG` (or
  `K8S_AUTH_HOST` / `K8S_AUTH_API_KEY`) pointed at `ocp.igou.systems`.
  `ansible-navigator run …` and `ansible-playbook …` are interchangeable.

## At a glance

| VM | `hermes` in namespace `hermes` |
|---|---|
| OS image | `centos-stream10` DataSource clone, 30Gi, **no resize** |
| Compute | 4 vCPU / 8Gi, Burstable QoS (no `resources` block) |
| Boot | BIOS bootloader, `q35`, runStrategy `Always` |
| Networking | single masquerade interface on the pod network (**VAP-conforming**) |
| Root disk | `hermes-root` DataVolume (in `dataVolumeTemplates` → deleted with the VM) |
| State disk | `hermes-state` PVC (plain PVC → **survives VM teardown**) |

## Provision / converge

Idempotent. Re-running reconciles the live `VirtualMachine` to
`vars/hermes-vm.yml`. Seed the operator SSH key at launch (non-secret public
key; the matching private key lives in 1Password for Phase 2b):

```bash
ansible-navigator run playbooks/hermes/provision-vm.yml \
  -i igou-inventory/inventory.yaml \
  -e vm_ssh_authorized_key='ssh-ed25519 AAAA... operator@host'
```

### Rebuild (delete + recreate)

`rebuild: true` deletes the existing VM first (waits for removal), then
converges a fresh one. Use this when the VM spec changed in a way that is not
reconcilable in place (e.g. firmware/bus changes).

```bash
ansible-navigator run playbooks/hermes/provision-vm.yml \
  -i igou-inventory/inventory.yaml \
  -e rebuild=true \
  -e vm_ssh_authorized_key='ssh-ed25519 AAAA... operator@host'
```

> **`hermes-root` is destroyed on rebuild** — it is a `dataVolumeTemplate`. The
> **`hermes-state` PVC survives**, because it is attached as a plain
> `persistentVolumeClaim` (Argo-owned, *not* in `dataVolumeTemplates`).

### Deprovision

`vm_state: absent` tears the VM down (`rebuild` is unnecessary):

```bash
ansible-navigator run playbooks/hermes/provision-vm.yml \
  -i igou-inventory/inventory.yaml \
  -e vm_state=absent
```

> **What survives teardown:** the **`hermes-state` PVC is retained** — it is
> attached as a plain PVC, not a `dataVolumeTemplate`, so deleting the VM does
> not delete it. The `hermes-root` DataVolume is removed with the VM. To
> reclaim state storage you must delete the `hermes-state` PVC by hand.

## Snapshot lifecycle

All snapshot operations go through `playbooks/hermes/snapshot-vm.yml`, which
applies the `kubevirt_vm_snapshot` role against `hermes`/`hermes`. Pick the
operation with `-e snapshot_action=<create|list|prune|restore>` (default
`list`). Snapshots use the cluster's `snapshot.kubevirt.io` CRDs.

### `list` (default, read-only)

```bash
ansible-navigator run playbooks/hermes/snapshot-vm.yml \
  -i igou-inventory/inventory.yaml \
  -e snapshot_action=list
```

Prints the VM's `VirtualMachineSnapshot` names, newest first.

### `create` (auto-prunes)

```bash
ansible-navigator run playbooks/hermes/snapshot-vm.yml \
  -i igou-inventory/inventory.yaml \
  -e snapshot_action=create
```

Creates `hermes-<UTC ts>` (override with `-e snapshot_name=<name>`), waits for
it to become `readyToUse`, then **auto-prunes to `snapshot_retention_count`**
(default 7) because `snapshot_prune_after_create: true`. One `create` therefore
both snapshots and bounds retention — no separate prune run is needed.

### `prune`

```bash
ansible-navigator run playbooks/hermes/snapshot-vm.yml \
  -i igou-inventory/inventory.yaml \
  -e snapshot_action=prune \
  -e snapshot_retention_count=7
```

Keeps the `snapshot_retention_count` newest snapshots **for this VM** and
deletes the rest. Selection is by `spec.source.name == hermes` sorted on
`creationTimestamp` (newest first), so other VMs' snapshots are never touched.

### `restore` (DOUBLE-GUARDED — destructive)

Restore refuses to run unless you pass **both** an explicit `snapshot_name`
**and** `snapshot_restore_confirm=true`:

```bash
ansible-navigator run playbooks/hermes/snapshot-vm.yml \
  -i igou-inventory/inventory.yaml \
  -e snapshot_action=restore \
  -e snapshot_name=hermes-20260614-013000 \
  -e snapshot_restore_confirm=true
```

The restore flow: reads the current `runStrategy` → **stops the VM**
(`runStrategy: Halted`, waits for the VMI to disappear) → creates a
`VirtualMachineRestore` from `snapshot_name` and waits for `status.complete` →
**restarts the VM** to its prior `runStrategy` (unless
`-e snapshot_restore_restart=false`). Run `list` first to get the exact
snapshot name.

## AAP wiring (igou-inventory, config-as-code)

The Hermes job templates and schedule live in `igou-inventory`
(`group_vars/aap/job_templates.yml`, `group_vars/aap/schedules.yml`), reusing
the `virtualmachine-deployer-token` credential and the `igou-awx-ee` EE. The
simple per-VM values (`vm_name: hermes`, `vm_namespace: hermes`, SSH pubkey,
retention) are job-template `extra_vars`; the complex VM spec stays in this repo
(`vars/hermes-vm.yml`).

| AAP object | Purpose |
|---|---|
| `hermes_vm_provision` (job template) | Runs `provision-vm.yml`. Survey exposes `rebuild` / `vm_state`. |
| `hermes_vm_snapshot` (job template) | Runs `snapshot-vm.yml`. Survey exposes `snapshot_action` / `snapshot_name`. |
| `hermes_vm_snapshot_nightly` (schedule) | Nightly `snapshot_action=create` on `hermes_vm_snapshot` (01:30 America/New_York), auto-pruned to 7 newest. Provides the pre-compromise restore points the security design calls for. |

Because `create` auto-prunes, the one nightly job both snapshots and enforces
7-deep retention.

## Notes / guardrails

- The VM is driven by **`spec.runStrategy`** (`Always`), not the legacy
  `spec.running` field — restore stop/start patches `runStrategy` accordingly.
- The VM is **VAP-conforming**: a single masquerade interface on the pod
  network, no secondary/Multus NICs. Keep it that way or the validating
  admission policy on `ocp.igou.systems` will reject the object.
- **Phase 2b (guest convergence) is out of scope.** cloud-init only sets the
  hostname, seeds the operator SSH key, and enables `qemu-guest-agent`. No
  secrets, no in-guest configuration.

## Regression test (no cluster required)

The cluster-independent logic — input validation and the prune
selection/retention math — is covered by `molecule/kubevirt-vm-snapshot/`:

```bash
cd /workspace/igou-ansible
molecule test -s kubevirt-vm-snapshot
# or, without molecule's driver layer:
ansible-playbook molecule/kubevirt-vm-snapshot/verify.yml
```

Live snapshot/restore against the real cluster is validated separately by the
on-cluster smoke test, not by molecule.
