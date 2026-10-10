# Molecule scenarios

Molecule discovers all 15 scenarios through the shared configuration. Linux smoke
and node-exporter lifecycles have passed on Casval; Windows guests have booted and
authenticated with local snapshot clones. Cold reinstall/cache recovery passed;
three complete mixed-OS batches remain in validation. These results do not
certify every scenario in the table below.

All disposable guests use `david_igou.molecule_provisioners` **0.0.6-alpha** with
KubeVirt on `https://api.ocp.igou.systems:6443`. Scenario names describe use cases;
Linux and logging domains use flat names so discovery and `-s` remain simple.

## Layout

```text
.config/molecule/config.yml       common Molecule configuration
requirements-molecule.yml         one pinned test dependency manifest
molecule/
  shared/
    inventory/                   defaults only; introduces no guests
    create.yml                   preflight, sysprep, provisioner create
    prepare.yml                  provisioner prepare
    destroy.yml                  provisioner destroy, sysprep cleanup
    preflight.yml                cluster identity and run ownership
    windows_sysprep.yml           one Windows specialization implementation
    templates/                   shared Windows answer file
    tasks/                       reused functional metrics probes
    reboot_linux.yml             shared boot persistence stimulus
  <use-case>/
    molecule.yml                 name and genuine lifecycle differences
    overrides.yml                extra vars for production playbook defaults
    inventory/                   only this scenario's guests and inputs
    converge.yml                 real playbooks or public roles
    verify.yml                   independent outcome checks
    prepare.yml                  optional contextual fixture setup
    side_effect.yml              optional reboot or update stimulus
    checks/                      optional topic-specific assertions
```

The project base is discovered automatically by Molecule 26.9.0. Static shared
inventory loads first, scenario inventory second, and provisioner-written
connection inventory last. Group vars named `molecule` live in directories so
bare recursive discovery cannot mistake them for scenario configs. Make targets
also set `MOLECULE_GLOB=molecule/*/molecule.yml`.

Use `molecule_profile_overrides` for scenario-wide VM differences and `mp.kubevirt`
for host differences. KubeVirt merges specifications shallowly: a host mapping
replaces the matching default mapping. Shared placement lives outside guest
profiles and is combined into `mp_defaults.kubevirt`; a partial scenario
`mp_defaults` mapping can erase it and must be avoided.

Every guest tolerates `workload=burst:NoSchedule` and prefers hostname
`casval.igou.systems` (weight 100). `lvms-casval` disks require Casval through
storage topology; preflight fails if it is unavailable or cordoned. Container-disk
guests can use other eligible workers. Avoiding control-plane nodes remains a
second preference. Scheduling also depends on resources, architecture and taints.

The provisioner owns isolated VM, disk, Service and runtime-inventory lifecycle.
Consumer-owned sysprep Secrets and golden-build artifacts include a persisted
run ID. Cleanup uses exact owned names and never deletes the namespace. Keep
Molecule's ephemeral directory until cleanup succeeds; changing a scenario's host
set during an isolated lifecycle requires destroying that run first.

## Coverage

| Scenario | Guests and intended outcomes |
| --- | --- |
| `default` | Stream 10 SSH/Python/sudo smoke; retained snapshot input and retention regressions. |
| `codex-desktop` | Upstream Fedora desktop, latest Codex CLI/T3/desktop app, state disk, desktop behavior; retained image resolver regressions. |
| `devenv` | Stream 10 devcontainer bootstrap, tools, authenticated external code-server, Docker storage and reboot persistence. |
| `devenv-restore` | Stream 10, disposable versioned S3 store, real backup/restore, rollback archive and live container bind-mount refresh. |
| `grafana-kiosk` | Debian x64 Cog and Chromium guests; dashboard navigation and kiosk behavior against the existing HTTP fixture. |
| `linux-node-exporter` | Stream 10, external CPU/memory/identity metrics after reboot. |
| `linux-maintenance` | Stream 10 baseline, packages, exclusive SSH keys, update and reboot; fresh admin SSH and passwordless sudo. |
| `linux-podman-quadlets` | Stream 10 rootless service account, TLS proxy, routing/redirects/auth/denials, Hugo revisions, config restart, update invocation, reboot and teardown/migration cleanup. |
| `alloy-logging` | Stream 10 journal sender and real Loki receiver; a unique post-reboot message must arrive at Loki. |
| `upsmonitor` | Debian x64, two NUT dummy UPS drivers, real NUT/exporter stack and per-UPS external metrics after reboot. |
| `windows-general` | Windows 11 and Server 2025; accounts, apps, services, firewall, scheduled tasks, bounded Defender updates, facts, RDP, server IIS, client debloat/readiness/power/visual effects. |
| `windows-domain` | Independent disposable AD controller and member; retained domain-join and authenticated domain behavior. |
| `windows-image-build` | Real Server 2025 unattended installation and generalization; collection-provisioned clone must accept PSRP and run its guest agent. |
| `ghapp` | Retained live GitHub App client/broker integration on independent Stream 10 guests. Requires the existing integration credential profile. |
| `truenas-apps` | Retained middleware simulation on Stream 10; config/environment drift, app recreation and payload behavior. No live TrueNAS changes. |

`windows-general` excludes OHMGraphite and domain membership; AD remains a separate
lifecycle. IIS runs only on Server; desktop roles run only on the client. Windows
specialization uses the same helper for built-in Administrator and client local
administrator modes. A disposable password is persisted in Molecule state unless
`MOLECULE_WINDOWS_ADMIN_PASSWORD` supplies one.

General x86 Linux guests use **CentOS Stream 10**. `codex-desktop` retains
Fedora because the production desktop role and image resolver require it;
porting that role is separate work. Pi appliance software uses **x64 Debian 13**. The kiosk and UPS
scenarios do not claim USB hardware, Pi graphics/firmware, EEPROM or ARM boot
coverage. `rpi-image` is omitted: native ARM image/chroot/flashing behavior cannot
be represented faithfully by these x64 guests.

The VPS scenario uses generated self-signed TLS and a per-run fixture password
persisted only in Molecule ephemeral state.
It never imports ACME/certificate issuance or joins the production tailnet. Its
update call covers the entry point; a controlled registry image replacement and
rollback are deferred. Migration coverage removes a populated user's unit set
at cleanup; moving persistent production data between users is outside this
fixture. Published private VPS applications are represented by public backends.

The restore scenario overrides production endpoint/credential defaults through
`overrides.yml`, uses only synthetic agent state and lowers the archive-size gate
for that small fixture. Version-pinned restore, guard failure injection, UID drift
and extraction-failure recovery remain later extensions. It omits idempotence
because backup and restore deliberately write archives and state.

Image construction is the content under test, so its installer VM is created by
the real golden-image playbook during `create`; the collection owns the resulting
image's test clone. The production playbook accepts optional scheduling preferences and tolerations,
while retaining its required nested-worker exclusion. Build resources are named
per run in `molecule`; the pre-existing no-prompt ISO comes from `windows-images`.
Allow roughly 45–90 minutes. No idempotence action rebuilds an image.

TrueNAS simulation skips `datasets`: its host cannot provide the ZFS API. This
scenario intentionally replaces the executor argument list to add that CLI flag,
while retaining the shared inventory chain. Other scenarios inherit it unchanged.

## Execution after review

Use the local managed toolchain with Molecule 26.9.0, compatible ansible-core,
`oc`, Kubernetes Python dependencies, PSRP dependencies and boto3/botocore for
restore. Activate the scoped `ocp-ansible-molecule` profile and verify identity.
Install local controller dependencies in a project virtual environment so the
Ansible process can import them:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-molecule.txt
source .venv/bin/activate
```

Windows preflight checks PSRP before creating guests; a missing controller
dependency must not consume the full guest connection timeout.
Shared prepare clamps Windows Ethernet MTU to `molecule_windows_guest_mtu`
(1400 for this cluster) before package downloads. Windows ignores the
DHCP-advertised pod MTU; leaving it at 1500 can stall external CDN requests.
Windows test guests use `u1.xlarge` (four vCPUs, 16 GiB) on Casval to give
specialization and feature installation more CPU than the former two-core preset.
`windows-general` runs its existing client/server reboot after converge and
before idempotence. This settles pending Windows changes before testing a no-op
second pass; `win_feature.reboot_required` can reflect server state even when
the feature itself is unchanged. Final verification still runs after the reboot.
The `molecule` namespace must exist and grant the provisioner's documented
VM/CDI/Service permissions. CentOS and Windows scenarios need Ready local
`centos-stream10-casval`, `win11-casval` and `win2k25-casval` DataSources in
`openshift-virtualization-os-images`, plus read access to those DataSources/PVCs
and source clone authorization. Image builds additionally need the source ISO clone permission.
The controller must reach node InternalIPs/NodePorts and guests need package,
image and release download access.

```bash
make molecule-matrix MOLECULE_SCENARIO=linux-node-exporter
make molecule-test MOLECULE_SCENARIO=linux-node-exporter
make molecule-verify MOLECULE_SCENARIO=linux-node-exporter
make molecule-destroy MOLECULE_SCENARIO=linux-node-exporter
```

Bare `molecule test -s <use-case>` uses the same base. `default` remains usable for
bare commands. `make molecule-test-all` includes costly image builds and
credentialed integration tests; individual scenarios are the intended starting
point. Functional execution stays outside GitHub Actions. Actions install the
same test manifest for static linting only.

## Fast batches on Casval

Deploy the companion GitOps `components/molecule` image cache and service-account
read grants before running this branch. The same GitOps change enables Casval's
Metal3 metadata cleaning between installations; leaving it disabled lets the
provisioning ramdisk activate the old LVMS pool and block CoreOS installation.
Cleaning affects every recognized local disk, so keep durable images off Casval
and recheck its hardware inventory before adding disks.
The cache keeps one seed for each of
CentOS Stream 10, Windows 11 and Server 2025 on `lvms-casval`; tests snapshot-clone
the same class using RWO Block volumes. Windows requires a first TrueNAS-to-local
copy per golden PVC generation. CentOS imports directly from its registry feed.
Hourly polling updates stable DataSources after the new seed succeeds. Windows
refresh detects a replacement PVC UID, not changes to disk bytes in place.

Use `molecule_boot_sources` for complete host boot mappings: the provisioner
replaces nested mappings rather than recursively merging them. Shared preflight
checks seed readiness, binding and matching class/modes. Shared prepare verifies
Casval placement, normal consumer binding and CDI's actual `cloneType: snapshot`;
a copy fallback fails the run. It prints selected disk information without clone
tokens. No test lifecycle mounts or deletes shared seeds.

Acquire or renew through `casval_scale` after deploying the feeds. Acquisition
warms all three seeds and marks the current node UID cache-ready. A fresh
installation resets only cache claims that predate the node; retries preserve
imports already created on that installation. Molecule rejects a missing or
stale cache marker. Reboots reuse seeds without copying them again.

Hold one Casval lease for the batch and cleanup; extend it before expiry if
needed. A reboot preserves local seeds. Destructive reprovisioning loses them
and requires reseeding; a stale Bound PVC alone does not prove its data survived.
Operational recovery is documented in the companion `igou-docs` runbook.

```bash
make molecule-test-batch \
  MOLECULE_SCENARIOS="default linux-node-exporter windows-general" \
  MOLECULE_WORKERS=3
```

This installs the shared pinned dependencies once, then uses `xargs -P` to run
independent Molecule processes with dependency installation disabled. Molecule
26.9's native `--workers` requires collection mode and cannot run this repository.
`molecule/dependency.sh` installs roles and collections from the one requirements
file into the same project-local paths used by test execution. This prevents
Galaxy from skipping globally installed collections that tests cannot see.
The default batch
contains `default`, `linux-node-exporter` and `linux-maintenance`. A worker limit
bounds **scenarios**, not VMs: `windows-general` creates two guests. Start at
three workers and increase only after measuring CPU/RAM, thin-pool data/metadata
and total wall time. Keep independent scenario lifecycles and ephemeral directories;
do not run another process against the same scenario's state concurrently.
If cleanup fails, retain its state and run the matching `molecule-destroy` target.

The common generated Ansible configuration enables SSH pipelining, 16 forks and
task timing. Forks parallelize guest tasks; they do not cap VM or scenario count.
For repeated development on an already-created guest, use `molecule-converge`
and `molecule-verify`, then destroy when done. Full certification still uses
`molecule-test` with a fresh clone, idempotence and teardown.

Debian container-disk guests keep their existing node image-cache path. Fedora
desktop still resolves/downloads the current upstream cloud image on each fresh
run, now onto LVMS; its resolver behavior remains covered. Windows image-build
still runs a real installer (45–90 minutes), with its root disk and resulting
test clone on LVMS. Its standalone blank build disk uses the builder's existing
immediate-binding annotation; test VM clone templates do not. Neither expensive
scenario is included in the default batch.

Earlier storage validation measured nine local clones at 6–32 seconds per disk.
That excludes guest boot, Windows specialization and Ansible phases; it is not
a runtime certification or timing guarantee for this Molecule refactor.

The local storage checks cover boot disks. Windows preferences also persist EFI
and vTPM state in small KubeVirt-managed claims using the cluster's RWX state
storage (`freenas-nfs-ssd-csi` in current validation). Those claims are owned by
the disposable VM and must disappear during teardown alongside its boot disk.
