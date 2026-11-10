# Armbian lab operations

One scenario exercises the operations in `playbooks/armbian/` against five
disposable KubeVirt guests on Casval: a builder, NFS/HTTP server, RouterOS CHR
and two virtual boards. All fixtures and assertions live in this directory.
It inherits the repository's Molecule base and dependency pins.

The lab playbooks use collection roles and their own orchestration and RouterOS
transport; they do not import the collection's published playbooks. This scenario
follows those same lab entry points.

## Run

Activate a local virtual environment containing `requirements-molecule.txt`.
The controller needs `oc`, `ssh`, `sshpass` and `rsync`; building
`ansible-pylibssh` from source also requires libssh development headers.
Acquire a current Casval lease and credentials that can manage VMs, Services,
DataVolumes/PVCs and UserDefinedNetworks in `molecule`, read the generated
NetworkAttachmentDefinitions and read/list Nodes. The existing
`ocp-ansible-molecule` profile lacks secondary-network permissions.

```bash
use ocp
oc whoami --show-server
oc whoami
molecule test -s armbian-netboot
```

Guests require `casval.igou.systems` and persistent disks use `lvms-casval`.
This scenario does not support the TrueNAS storage profile or production
inventory. It is run explicitly outside GitHub Actions.

Dependencies normally use root `requirements.yml`. To exercise a collection
checkout's production roles, including uncommitted fixes:

```bash
ARMBIAN_COLLECTION_SOURCE=/workspace/ansible-collection-armbian \
  molecule test -s armbian-netboot
```

The dependency step builds and installs that checkout into this repository's
`.ansible/collections`; it supplies no test assets. Until the fixes in
[ansible-collection-armbian#137](https://github.com/david-igou/ansible-collection-armbian/pull/137)
are released and pinned, use its branch for full acceptance. Configuration,
syntax and lint checks do not require `ARMBIAN_COLLECTION_SOURCE`.

Validate the composed workflow locally before creating guests:

```bash
molecule matrix -s armbian-netboot test
molecule syntax -s armbian-netboot
ansible-lint --offline
yamllint .
shellcheck molecule/armbian-netboot/dependency.sh molecule/armbian-netboot/files/make_fixture.sh
```

## Operations and outcomes

| Lab entry point | Functional coverage |
| --- | --- |
| `build_and_publish.yaml` | Repeat cached builds, publish over builder/controller/server SSH, fetch the actual manifest and verify the compressed image checksum. |
| `stage_netboot_assets.yaml` | Stage one source for two boards, reuse one download, retain modified content on cached staging, restore it on forced refresh, and retain separate host identities. Fetch actual kernel bytes and per-MAC boot configurations over RouterOS TFTP. |
| `tasks/image_sd.yml`, used by `tests/fleet_e2e.yaml` | Write the configured image to the disposable SD disk, compare its bytes with the source, then boot that disk through the lab's boot-mode operation. |
| `bootstrap.yaml` | Bootstrap the running NFS system, copy it to local storage and require automation-key access, passwordless sudo, disabled password/root SSH login and a locked root account on the running local system. |
| `provision_local_disk.yaml` | Copy the running NFS root onto the local disk, boot it and verify the actual root source, payload and mount table. |
| `reprovision_to_local.yaml` | Repeat the NFS-to-local workflow with a preserved data partition; read application state from its normal mountpoint after the second boot. |
| `set_boot_mode.yaml`, `converge_boot_mode.yaml`, `cycle_board.yaml` | Exercise NFS, SD, local and local-kernel modes. Require new kernel boot IDs and independently verify the running root. |
| `full_reimage.yaml` | Force a fresh image through staging, bootstrap, local provisioning and the declared steady-state boot on one canary; verify its new system and the other board's integrity. |

The excluded disk must remain byte-identical throughout. Its baseline is kept
on the controller so root filesystem changes cannot erase the evidence.
The scenario omits blanket idempotence because imaging and boots intentionally
change state; cached staging and retained application data have explicit
repeat-run checks. It tests the lab's SD imaging path rather than a generic
matrix of collection-role inputs. Standalone collection image retention is
outside this lab workflow.

## Fixture limits

The default builder seeds a bootable Debian x86 image and matching manifest to
exercise actual cached build/publication. Set `LAB_NETBOOT_BUILD=true` to request
uncached compilation through the same lab playbook. The builder has four CPUs,
8 GiB RAM and a 100 GiB boot disk. The optional `uefi-x86/current` build at
`v26.8.0-trunk.180` failed on upstream Apple-driver patch conflicts on 2026-11-10;
full compilation has not passed acceptance.

Kexec supplies the virtual power transport and boots real Linux kernels and
root filesystems. Rockchip BootROM, U-Boot, SPI, physical SD/eMMC controllers,
PHY behavior and PoE remain hardware acceptance. The DTB is a transport fixture.
The fixtures contain no k3s cluster, so full-reimage's optional stale-node
cleanup is disabled.

CHR uses e1000 interfaces and an unpacked raw image served to CDI by this run's
HTTP Service. A dedicated generated account avoids the factory admin's password
change prompt. TFTP downloads run sequentially because the free CHR license
limits each interface to 1 Mbps; allow several minutes for verification.

## Cleanup

```bash
molecule destroy -s armbian-netboot
```

Preserve the ephemeral directory until destroy succeeds. Public provisioner
dispatchers own guest, disk, connection-inventory and endpoint lifecycle. The
scenario records ownership of its private network and HTTP fixture Service,
then removes them after guest destruction. Successful teardown removes run
keys, credentials, disk baselines and caches. Never reset state or delete the
shared namespace to conceal a failed cleanup.
