# Molecule workflows

These scenarios run production Ansible entry points against disposable KubeVirt
guests. `david_igou.molecule_provisioners` owns their VM, disk, Service, and
connection inventory lifecycle. Shared setup lives in `.config/molecule/config.yml`
and `molecule/shared/`; each workflow keeps its own guests, inputs, and outcome
checks. Root `requirements.yml` pins the Galaxy dependencies.

## Run a scenario

Acquire a Casval lease and wait for the node and cached images to become Ready.
Use the `ocp-ansible-molecule` credential profile, then check the selected
cluster and identity before running tests:

```bash
use ocp-ansible-molecule
oc whoami --show-server
oc whoami
molecule test -s default
```

Casval is the default placement and storage profile. Its Stream 10 and Windows
guests clone from `*-casval` DataSources onto `lvms-casval`. Preflight requires a
Ready, schedulable Casval and current local image cache; verification requires
the guest to land on Casval and use snapshot cloning.

For a regular worker backed by shared TrueNAS storage, set one override:

```bash
MOLECULE_STORAGE_PROFILE=truenas molecule test -s default
```

This selects `freenas-nvmeof-ssd-csi` and the matching shared image sources.
Preflight validates the shared source claim, and verification checks the guest's
disk class and placement away from Casval and the TrueNAS VM worker, which
cannot host nested virtualization. The profile does not contact or simulate
the TrueNAS API. Container-disk guests have no clone source; they
still run on Casval by default and on regular workers with the shared-storage
profile.

`molecule test -s <name>` runs an isolated lifecycle and destroys only its own
resources. If a run fails, inspect its saved Molecule state and run
`molecule destroy -s <name>` after fixing the cause. Keep the ephemeral state
until cleanup succeeds. The default scenario is only an SSH, Python, and sudo
provisioner smoke test. Run expensive or credentialed scenarios explicitly.

## Scenario roster

| Scenario | Guest workflow and outcome |
| --- | --- |
| `default` | One Stream 10 guest; SSH, Python, and sudo work. |
| `devenv` | Production bootstrap, editor/service smoke, local-file backup and restore of synthetic agent state, and restored bind-mount visibility. |
| `codex-desktop` | Production Fedora guest configuration with controller/guest inventory groups, desktop session, app/CLIs, T3, metrics, and retained home after reboot. |
| `linux-maintenance` | Baseline hardening, packages, SSH keys, node exporter, update, reboot, admin access, and metrics. |
| `kubernetes-k3s` | Disposable control plane and worker, production k3s install, local API readiness, and Ready nodes. |
| `kubernetes-kubeadm` | Independent control plane and worker, production kubeadm install, local API readiness, and Ready nodes. |
| `kubernetes-vps` | Single Debian VPS install, local kubeconfig/API health, and Ready node. |
| `windows-general` | Windows client/server configuration and functional behavior after reboot. |
| `windows-domain` | Disposable controller/member join and authenticated domain behavior. |
| `windows-image-build` | Build, generalize, clone, boot, and guest management connection. |
| `grafana-kiosk` | Browser/kiosk deployment and dashboard navigation against a small HTTP fixture. |
| `upsmonitor` | NUT packages and configuration, running exporters, and host metrics without attached UPSes. |
| `ghapp` | Separate credentialed GitHub App client/broker integration. |

No Molecule scenario enrolls Tailscale. The three Kubernetes scenarios stop at
installation and API/node health; they do not install GitOps or applications.
The devenv file round trip does not verify S3 transport. `upsmonitor` does not
claim live USB UPS or device telemetry coverage. `ghapp` requires its existing
integration credential profile.
`linux-maintenance` verifies the post-reboot result; its repeat-run check is
omitted because the upstream hardening role changes system-account shells on
a second pass.

The `windows-image-build` lifecycle can take 45–90 minutes. Windows and desktop
guests need their current external package/image sources. `kubernetes-kubeadm`
imports a 20 GiB Debian cloud disk per guest because the small container disk
cannot hold the kubeadm runtime and images. The `molecule`
namespace and image DataSources are shared prerequisites, not scenario-owned
resources.
`windows-general` excludes the Windows Update install from its repeat-run check
because definition updates can arrive during a test; its post-reboot check
still verifies that an update search succeeds.

## Fedora image selection

`codex-desktop` and production provisioning use the same reviewed Fedora Cloud
release pin in `playbooks/codex_desktop/vars/image.yml`. The resolver requires
an official image for that release instead of falling back to a newer one.
Renovate tracks released Fedora versions and proposes reviewed updates on main;
prerelease retains its existing Fedora update policy. Validate the guest Python
against the pinned Ansible Core before accepting a release bump. Verification
checks the running release after reboot along with the desktop and CLI behavior.

## Windows user profiles

Windows connections use the builtin PSRP plugin. Shared `windows_server`
inventory sets `ansible_psrp_no_profile: true` for machine-level server and
domain-controller workflows. Desktop sessions retain normal profile loading.
The server visual-effects import explicitly loads its profile because it
configures user registry hives. Keep profile loading enabled for other
profile-dependent automation as well.

This uses the pinned Ansible/pypsrp option rather than a custom connection
plugin or task retry. Validate profile-dependent behavior when changing its
scope; skipping a user profile must not turn a configuration test into a no-op.
