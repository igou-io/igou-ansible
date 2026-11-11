# Armbian lab operations

One scenario exercises the operations in `playbooks/armbian/` against five
disposable KubeVirt guests on Casval: a builder, NFS/HTTP server, RouterOS CHR
and two virtual boards. All fixtures and assertions live in this directory.
It inherits the repository's Molecule base and dependency pins.

The lab playbooks use collection roles and their own orchestration and RouterOS
transport; they do not import the collection's published playbooks. This scenario
follows those same lab entry points.

## Layout and execution order

Each top-level lifecycle file imports `playbooks/<phase>/main.yml`. That file
shows the phase's plays and production imports in execution order. Supporting
playbooks and task lists use matching phase directories:

```text
armbian-netboot/
  molecule.yml
  create.yml
  prepare.yml
  converge.yml
  verify.yml
  side_effect.yml
  destroy.yml
  dependency.sh
  overrides.yml
  inventory/
    hosts.yml
    group_vars/
  playbooks/
    create/main.yml
    prepare/
      main.yml
      builder.yml
      pxe.yml
    converge/
      main.yml
      boot_matrix.yml
      datastore.yml
      image_sd.yml
      pin_refresh.yml
      preserve.yml
      refresh.yml
    verify/
      main.yml
      k3s.yml
      datastore.yml
      update.yml
    side_effect/
      main.yml
      worker_reimage.yml
    destroy/main.yml
    shared/
      boot_transition.yml
      router_transport.yml
  tasks/
    create/
    prepare/
    converge/
    verify/
    destroy/
    shared/
  files/
```

Worker-reimage checks stay beside their operations in
`playbooks/side_effect/worker_reimage.yml`.
`ansible.builtin.import_playbook` composes plays; `ansible.builtin.import_tasks`
and `include_tasks` load task lists. `shared/` holds only code used across
phases: boot transitions, RouterOS transport, cluster identity, local disk
layout and TFTP counters. `tasks/shared/boot_mode.yml` supplies the virtual
power-cycle hook used by the production playbook.

Host inputs and shared values live in `inventory/hosts.yml` and `group_vars/`.
The repository's shared Molecule config loads `overrides.yml` as extra vars;
its bootstrap inputs and virtual power-cycle hook override values in imported
production plays. Fixture scripts and manifests stay in `files/`, resolved
from `lab_scenario_directory`.

## Phase responsibilities and assertions

The default sequence is `dependency → syntax → create → prepare → converge →
verify → side_effect → verify → destroy`.

| Phase | What happens | What is tested |
| --- | --- | --- |
| `dependency` | `dependency.sh` installs root `requirements.yml`; an explicit `ARMBIAN_COLLECTION_SOURCE` optionally replaces only the production collection. | Dependency installation must succeed. This phase does not boot or exercise a board. |
| `syntax` | Molecule parses `converge.yml` and its static imports with Ansible. | Playbook syntax and imported roles resolve. Repository lint additionally checks the other entry points. |
| `create` | `playbooks/create/main.yml` checks the cluster/node, records ownership, creates the private UDN, imports the public provisioner and exposes the fixture HTTP Service. The CHR DataVolume points at the run-owned raw-image adapter. | Correct API/identity, exact five-host scope, Ready Casval, matching saved identity and refusal to adopt another run's network/Service. Guest services and firmware boot modes are checked later. |
| `prepare` | `playbooks/prepare/main.yml` waits for Linux management connections, installs tools, configures private networking and prepares NFS/HTTP exports. It builds the frozen boot image, unpacks the public CHR archive, initializes RouterOS and configures DHCP/TFTP. It seeds both excluded filesystems and saves their hashes, then prepares Docker and cached builder manifests/images. | CHR reservation supplies each board's private address; required disks exist; image creation and fixture downloads succeed. The nonzero excluded-disk baselines and original kernel release are retained on the controller for later comparisons. These are fixture/readiness checks, not the firmware boot matrix. |
| `converge` | `playbooks/converge/main.yml` invokes the lab's build/publication, staging, SD imaging, bootstrap, local provisioning and reprovisioning workflows. It firmware-boots both boards in all four modes, creates/exports/imports the ZFS datastore, installs k3s and deploys the test workload. | Cached publication/staging reuse, one shared download, force-refresh restoration, same-size PXE pin repair, exact SD image bytes, eight host/mode boot outcomes, CHR DHCP/TFTP evidence, running roots/kernels, preserved-data UUID/content and removal of the old root. ZFS member identities and original pool GUID/content are captured for later verification. |
| First `verify` | `playbooks/verify/main.yml` checks the converged systems and calls the reusable cluster/datastore verifiers. | Five guests on Casval; all eight boot records; original excluded-disk hashes; local root/payload/fstab; kernel bytes and per-MAC pins served by CHR; automation-user sudo and SSH/root hardening; published manifests/images and distinct host keys; two intended Ready k3s nodes, pinned version and containerd; effective pod user namespaces, DNS/Service and cross-node payload integrity; original ZFS GUID/content, healthy mirrors, live dataset mounts and k3s mount dependencies. |
| `side_effect` | `playbooks/side_effect/main.yml` fully reimages/rejoins the installed worker, then runs the production system update and local-kernel reboot for the control plane followed by the worker. | Before reinstall: old root/application state and data partition are gone, only the root partition remains, and stale Node/password registration is absent. After reinstall: a new node UID and recovered workload. After each update: a newer apt-owned running kernel, working kernel modules, recovered networking/workloads and retained healthy ZFS mounts/data/dependencies. |
| Second `verify` | Runs the same `verify.yml` against the reimaged worker and updated nodes. | Repeats the steady-state assertions, including both original nonzero excluded-disk hashes, cluster workload and datastore integrity. Updated-kernel assertions already ran after each reboot in `side_effect`; the local-kernel boot records now describe those updated boots. |
| `destroy` | `playbooks/destroy/main.yml` reads saved ownership, imports provisioner destroy and removes the owned private network/HTTP Service, credentials, baselines and caches. | Provisioner cleanup waits for managed guest/claim removal; consumer cleanup checks ownership and waits for the NAD to disappear. Full acceptance also repeats destroy and independently checks all captured PVs, run resources and local credentials/state are absent. |

Converge and side effects contain checks that must run before the next operation
replaces their evidence, such as cached payloads and the old worker identity.
The two verify actions check the resulting systems. The builder seed belongs
to prepare; converge starts with the production publication workflow.

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
`.ansible/collections`; it supplies no test assets. Normal runs use the immutable
collection commit in root `requirements.yml`, including the portable fixes in
[ansible-collection-armbian#137](https://github.com/david-igou/ansible-collection-armbian/pull/137).
The override is only needed to test other collection changes.

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
| `build_and_publish.yaml` | Repeat cached builds, publish over builder/controller/server SSH, resolve board-a's published manifest through the production resolver, and stage/boot that image on both boards. Fetch the manifest and verify the compressed image checksum. |
| `stage_netboot_assets.yaml` | Stage one source for two boards, reuse one download, retain modified content on cached staging, restore it on forced refresh, and retain separate host identities. Repair a same-size stale per-MAC pin through boot-mode convergence. Fetch kernel bytes and per-MAC boot configurations over RouterOS TFTP. |
| `tasks/image_sd.yml`, used by `tests/fleet_e2e.yaml` | Write the configured image to the disposable SD disk, compare its bytes with the source, then boot that disk through the lab's boot-mode operation. |
| `bootstrap.yaml` | Bootstrap the running NFS system, copy it to local storage and require automation-key access, passwordless sudo, disabled password/root SSH login and a locked root account on the running local system. |
| `provision_local_disk.yaml` | Copy the running NFS root onto the local disk, boot it and verify the running root source, payload and mount table. |
| `reprovision_to_local.yaml` | Repeat the NFS-to-local workflow on the worker canary; require a reset root, the exact data/root layout, and the same data filesystem UUID and application marker after the second boot. |
| `set_boot_mode.yaml`, `converge_boot_mode.yaml`, `cycle_board.yaml` | Firmware boots in all four modes: `nfs`, `sd`, `local`, `local_kernel`. Require CHR DHCP leases, per-MAC pin TFTP hits, new boot IDs, the selected root and expected kernel release. Network-kernel modes must fetch kernel and initrd; local-kernel mode must leave those counters unchanged. |
| `full_reimage.yaml` | Reimage the installed k3s worker through staging, bootstrap, provisioning and steady-state boot. Require old root/application state and the data partition to disappear, the sole root partition to boot, stale node/password registration to disappear, a new node UID after k3s reinstall and recovery of the existing workload. |
| `kubernetes/prepare-k3s-datastore.yml` | Create two ZFS mirrors on four dedicated control-plane disks, export the pool and remove its import cache, then import without creation permission. Require the same pool GUID, retained content, dataset mounts and k3s mount dependency after reboot and kernel updates. |
| `kubernetes/install-k3s-cluster.yml` | Install the pinned k3s release with the lab's hard links and user-namespace feature settings; require exactly the intended Ready nodes, containerd and a running pod on each. Verify unprivileged host UID mappings, cluster DNS, Service HTTP and a checksum-verified 256 KiB response between pods on different nodes. |
| `system-update.yaml` | Update the control plane and then the worker from the Debian snapshot to current trixie packages. Reboot through the normal boot-mode/cycle operation. Require a newer apt-owned running kernel, loadable modules and the existing k3s workload to recover after each node. |

The excluded disk contains an ext4 filesystem and application data before any
imaging or provisioning. It must remain byte-identical throughout, so zeroing
and signature wipes are detectable. Its baseline stays on the controller across
root filesystem changes.
The scenario omits blanket idempotence because imaging and boots intentionally
change state; cached staging and retained application data have explicit
repeat-run checks. It tests the lab's SD imaging path rather than a generic
matrix of collection-role inputs. Standalone collection image retention is
outside this lab workflow.

## Fixture limits

The default builder seeds a bootable Debian x86 image and matching manifest to
exercise cached build/publication. Set `LAB_NETBOOT_BUILD=true` to request
uncached compilation through the same lab playbook. The builder has four CPUs,
8 GiB RAM and a 100 GiB boot disk. The optional `uefi-x86/current` build at
`v26.8.0-trunk.180` failed on upstream Apple-driver patch conflicts on 2026-11-10;
full compilation has not passed acceptance.
Uncached mode validates its published artifact separately; its boot operations
still use the frozen Debian fixture.
The cached path checks GitHub egress for resolving the build ref; only uncached
compilation requires the Armbian package and build-container mirrors.

BIOS PXE loads Debian's Syslinux loader from CHR, then consumes the unmodified
per-MAC configuration rendered by the lab playbook. There is no kexec boot
helper. `localboot 0` hands off to GRUB on the persistent management carrier;
GRUB loads `/vmlinuz` and `/initrd.img` from the provisioned local root. This
stands in for U-Boot's `localcmd`, while exercising the apt-managed kernel.
The helper initializes missing GRUB files after provisioning and leaves existing
ones intact; the update reboot does not regenerate or repair its boot menu.
Rockchip BootROM, U-Boot, SPI, physical SD/eMMC controllers, PHY behavior and
PoE remain hardware acceptance. The DTB is only a transport fixture on x86.

The image's packages and kernel come from the Debian `20260801T000000Z`
snapshot. The generated image records its kernel release; assertions
compare it with the running kernel before and after updates. The fixture
switches apt sources to current trixie only after creation. Preserved-data
reprovisioning runs before k3s installation. Full reimage then exercises the
installed worker's stale-node cleanup and rejoin while preserving the control
plane, before both nodes receive rolling OS updates.

The control plane models the CM3588 datastore with four separate 2 GiB SCSI
disks in two ZFS mirrors and a 512 MiB ARC cap. It uses the production datastore
playbook and k3s mount dependencies. The image includes matching kernel headers
so DKMS must build ZFS for both the snapshot kernel and its replacement.
SCSI disks simulate pool membership; NVMe identity and replacement remain
hardware acceptance.

The fixture keeps a `debian` maintenance account for the boot harness;
verification uses `lab-automation` to check the production bootstrap separately.
Control-plane disaster recovery, application-volume recovery, and changed
upstream image bytes behind an unchanged URL require separate acceptance.

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

`playbooks/prepare/pxe.yml` configures CHR's `lab-pxe` DHCP server on `ether2` only, with
MAC reservations from `inventory/hosts.yml`. Its `192.0.2.0/24` network advertises
`next-server=192.0.2.1` and `boot-file-name=pxelinux.0`. The router serves the
loader, matching Syslinux core, rendered per-MAC pins, kernels and initrds over
TFTP. The Debian server at `192.0.2.10` exports the running NFS roots. Both are
on the private Layer2 UDN; it has no OVN IPAM or connection to the physical LAN.

Board `enp2s0` gets its address from CHR in firmware, initramfs and the running
OS. The generated image stores its networkd DHCP policy in
`/etc/systemd/network/20-lab.network`; it does not assign a static board IP.
`enp1s0` uses separate KubeVirt management DHCP for controller SSH and internet
access. CHR does not provide management DHCP. The private lease advertises no
default gateway, so initramfs
cannot displace the management interface's internet/SSH route. The inputs live in
the inventory and `playbooks/prepare/pxe.yml`, then are applied to the disposable router;
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
