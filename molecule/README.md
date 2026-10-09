# Molecule scenarios

This scaffold is under structure review. Local production-profile ansible-lint and yamllint pass, and Molecule discovers
all 15 scenarios through the shared configuration. VM execution remains deferred. No scenario has runtime certification
from this refactor yet.

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

Every guest tolerates `workload=burst:NoSchedule` and **prefers**, rather than
requires, hostname `casval.igou.systems` (weight 100). Other workers remain eligible
when Casval is unavailable; avoiding control-plane nodes is a second preference.
Scheduling still depends on resources, architecture, storage and other taints.

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
The `molecule` namespace must exist and grant the provisioner's documented
VM/CDI/Service permissions. Windows scenarios need the `win11` and `win2k25`
golden PVCs in `openshift-virtualization-os-images`; devenv needs its CentOS
DataSource. Image builds additionally need the source ISO clone permission.
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
