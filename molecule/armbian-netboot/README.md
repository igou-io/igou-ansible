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
The controller needs `oc`, `virtctl`, `ssh`, `sshpass` and `rsync`; building
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
| `set_boot_mode.yaml`, `converge_boot_mode.yaml`, `cycle_board.yaml` | Firmware boots in all four modes: `nfs`, `sd`, `local`, `local_kernel`. Require CHR DHCP leases, per-MAC pin TFTP hits, new boot IDs, the selected root and expected kernel release. Network-kernel modes must fetch kernel and initrd; local-kernel mode must leave those counters unchanged. |
| `full_reimage.yaml` | Reimage the installed k3s worker through staging, bootstrap, provisioning and steady-state boot. Require the stale node and password-secret registration to disappear, reinstall k3s, require a new node UID and verify the existing workload recovers. |
| `kubernetes/install-k3s-cluster.yml` | Install the pinned k3s release on both booted local systems; require exactly the intended Ready nodes, containerd and a running pod on each. Test cluster DNS, Service HTTP and a checksum-verified 256 KiB HTTP response between pods on different nodes. |
| `system-update.yaml` | Update the control plane and then the worker from the Debian snapshot to current trixie packages. Reboot through the normal boot-mode/cycle operation. Require a newer apt-owned running kernel, loadable modules and the existing k3s workload to recover after each node. |

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

BIOS PXE loads Debian's Syslinux loader from CHR, then consumes the unmodified
per-MAC configuration rendered by the lab playbook. There is no kexec boot
helper. `localboot 0` hands off to GRUB on the persistent management carrier;
GRUB loads `/vmlinuz` and `/initrd.img` from the provisioned local root. This
stands in for U-Boot's `localcmd`, while exercising the real apt-managed kernel.
The helper initializes missing GRUB files after provisioning and leaves existing
ones intact; the update reboot does not regenerate or repair its boot menu.
Rockchip BootROM, U-Boot, SPI, physical SD/eMMC controllers, PHY behavior and
PoE remain hardware acceptance. The DTB is only a transport fixture on x86.

The image's packages and kernel come from the Debian `20260801T000000Z`
snapshot. The generated image records its actual kernel release; assertions
compare it with the running kernel before and after updates. The fixture
switches apt sources to current trixie only after creation. Preserved-data
reprovisioning runs before k3s installation. Full reimage then exercises the
installed worker's stale-node cleanup and rejoin while preserving the control
plane, before both nodes receive rolling OS updates.

`inventory/hosts.yml` describes OS releases, source images, board payload
snapshot and k3s version. The router's source is MikroTik's public, versioned
download URL. CDI cannot unpack ZIP: create adapts that source to a temporary
run-owned HTTP endpoint, and prepare downloads/unpacks the public archive there.
The internal URL is an import adapter, not an alternate image source.
CHR uses e1000 interfaces and a generated account. The free license limits an
interface to 1 Mbps; firmware TFTP downloads can take several minutes.
TFTP blocks are capped at 1024 bytes: larger negotiated blocks caused iPXE
downloads to time out on this private OVN network. The minimal initrd explicitly
includes `nfsv3`, required by the production pin's `nfsvers=3` root mount.

## DHCP and boot network

`pxe.yml` configures CHR's `lab-pxe` DHCP server on `ether2` only, with
MAC reservations from `inventory/hosts.yml`. Its `192.0.2.0/24` network advertises
`next-server=192.0.2.1` and `boot-file-name=pxelinux.0`. The router serves the
loader, matching Syslinux core, rendered per-MAC pins, kernels and initrds over
TFTP. The Debian server at `192.0.2.10` exports the running NFS roots. Both are
on the private Layer2 UDN; it has no OVN IPAM or connection to the physical LAN.

Board `enp2s0` gets its address from CHR in firmware, initramfs and the running
OS. The generated image stores its networkd DHCP policy in
`/etc/systemd/network/20-lab.network`; it does not assign a static board IP.
`enp1s0` uses separate KubeVirt management DHCP for controller SSH and internet
access. CHR does not provide management DHCP. DHCP is declaratively stored in
this scenario; its private lease advertises no default gateway, so initramfs
cannot displace the management interface's internet/SSH route. The inputs live in
the inventory and `pxe.yml`, then are applied to the disposable router;
production rb5009 configuration is never loaded.

Successful mode transitions leave temporary evidence under
`boot-evidence/<host>-<mode>.yml`: boot ID, root, kernel, bound DHCP lease and
before/after TFTP counters. Verify requires all eight host/mode results.

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
