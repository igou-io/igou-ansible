# kubevirt_vm_provision

Idempotently provision (or rebuild / deprovision) a KubeVirt `VirtualMachine`
from a cloud-image `DataSource` clone, via `kubevirt.core.kubevirt_vm`. Generic
and reusable — the Hermes VM is one caller; point it at any
`openshift-virtualization-os-images` DataSource for other VMs.

It produces a **burstable**, **pod-networked (masquerade)** VM with BIOS or EFI
firmware and optional cloud-init — the safe default shape where OVN
NetworkPolicy / EgressFirewall apply. Auth is ambient: the `kubevirt.core`
module reads `K8S_AUTH_*` (AAP/EE) or your kubeconfig.

## What it does

- `vm_state: present` → converges the VM in place (patch-on-diff, idempotent;
  re-runs report `changed: false`).
- `vm_rebuild: true` → **destructive reprovision**: deletes the VM (+ root
  DataVolume) first, then converges a fresh one. Attached data PVCs are kept.
- `vm_destroy_data: true` → **fully destructive reprovision**: as `vm_rebuild`,
  but also deletes and recreates-blank the attached `vm_extra_pvcs` data disks
  for a clean slate. (Implies a rebuild. Don't point it at externally-owned
  PVCs — manage their destruction at that layer.)
- `vm_state: absent` → deletes the VM. Root DataVolume (a `dataVolumeTemplate`)
  is removed with it; **PVCs attached via `vm_extra_pvcs` survive** (they are
  plain `persistentVolumeClaim` volumes, not templates).

## Variables

| Var | Default | Meaning |
|-----|---------|---------|
| `vm_name` | `""` (required) | VirtualMachine name |
| `vm_namespace` | `""` (required) | Target namespace |
| `vm_state` | `present` | `present` \| `absent` |
| `vm_rebuild` | `false` | **Destructive reprovision**: if the VM exists, delete it (+ root DataVolume) and rebuild. Attached data PVCs are preserved |
| `vm_destroy_data` | `false` | **Fully destructive**: also wipe the attached `vm_extra_pvcs` data disks (delete + recreate blank) for a clean slate. Implies a rebuild. Not for externally-owned (Argo/CDI) PVCs |
| `vm_run_strategy` | `Always` | KubeVirt runStrategy |
| `vm_labels` | `{}` | Labels on the VM metadata |
| `vm_os_datasource` | `""` (required to provision) | DataSource to clone for the root disk |
| `vm_os_datasource_namespace` | `openshift-virtualization-os-images` | DataSource namespace |
| `vm_root_size` | `30Gi` | Root clone size (match the golden size — no resize) |
| `vm_storage_class` | `""` | StorageClass for the root clone (empty → cluster default) |
| `vm_cpu_cores` / `vm_cpu_sockets` / `vm_cpu_threads` | `2` / `1` / `1` | Guest CPU topology |
| `vm_memory` | `4Gi` | Guest memory (`domain.memory.guest`; Burstable QoS) |
| `vm_firmware` | `bios` | `bios` \| `efi` |
| `vm_machine_type` | `q35` | Machine type |
| `vm_log_serial_console` | `true` | Enable serial-console logging (a virtio-rng device is always attached) |
| `vm_extra_pvcs` | `[]` | Existing PVCs to attach as data disks: `[{name, claim}]` |
| `vm_cloud_init` | `""` | cloud-init NoCloud userData string (empty → no cloud-init disk) |
| `vm_wait` / `vm_wait_timeout` | `true` / `600` | Wait for the VM to be Ready |

The role is **re-entrant** (accumulators reset each call) — safe to invoke in a
loop to provision several VMs in one play.

## Example

```yaml
- name: Provision a CentOS Stream 10 VM
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    vm_name: demo
    vm_namespace: demo
    vm_os_datasource: centos-stream10
    vm_storage_class: freenas-nvmeof-ssd-csi
    vm_cpu_cores: 2
    vm_memory: 4Gi
    vm_extra_pvcs:
      - name: data
        claim: demo-data
    vm_cloud_init: "{{ lookup('template', 'templates/cloudinit.yaml.j2') }}"
  roles:
    - role: kubevirt_vm_provision
```

> The role emits a single masquerade interface on the pod network and no
> resources block (Burstable QoS). For secondary/Multus networks, GPUs, host
> devices, or Guaranteed QoS, drive `kubevirt.core.kubevirt_vm` directly — and
> note that admission policies (e.g. a VAP) may forbid those for hardened VMs.
