# Fedora Codex desktop

`codex-desktop` is a KubeVirt VM whose definition is owned by Argo CD and whose
OS provisioning, guest configuration, and power operations are owned by AAP.
It uses the latest stable official Fedora Cloud Base qcow2 image when a root
disk is created. There is no maintained desktop image or image builder.

## Ownership and scheduling

GitOps source: `igou-openshift/applications/codex-desktop/`. The
VM manifest prefers casval when it is schedulable, then other workers without
the control-plane/master labels, with control-plane fallback. It tolerates the
casval `workload=burst:NoSchedule` taint and the control-plane/master
`NoSchedule` taints. All manifests live in that one application directory;
there are no placement overlays or required casval selectors. Desktop jobs
never provision, scale, lease, start, or stop casval. If it is absent or cannot
accept the VM, the scheduler chooses another eligible host. A running VM is
not automatically moved when casval appears or disappears; placement is
evaluated when an instance starts.

The VM always declares `runStrategy: Manual`. AAP uses KubeVirt's start/stop
endpoints, which preserve that strategy. CPU/RAM, networking, and placement
remain in Git. The default VM has 4 cores and 8Gi RAM and boots through UEFI.

`codex-desktop-state` is a 50Gi independent block PVC. Both it and its namespace
are protected against Argo pruning/deletion. AAP creates the independent 50Gi `codex-desktop-root`
DataVolume; its PVC is managed by CDI. Neither disk has a VM ownerReference.
The daily-apps OADP schedule includes the new namespace. Read-only
VolumeAttachment permissions let AAP prove detachment before replacing root.

The Kubernetes `codex-desktop-operation` Lease serializes every desktop
rollout, setup, rebuild, and power job. AAP initializes it when absent; Argo
excludes coordination Leases. Argo preserves only generated VM MAC/firmware
identity, PCI topology annotation, and machine default, while placement,
CPU/RAM, disks, and power strategy remain declarative.
Acquisition uses a resourceVersion test; release tests the owning job ID.
No expiry automatically steals a lease from a long-running installation.
The AAP namespace also permits egress to this guest's SSH Service; allowing
ingress only in the guest namespace is insufficient under AAP's default deny.

## First rollout

1. Adopt the GitOps, inventory/AAP, and Ansible changes. Build and publish the
   updated `igou-awx-ee` through the normal delivery path; it includes `gpgv`
   for upstream signature verification. Apply AAP config-as-code through
   `playbooks/aap/configure-aap-templates.yml` in the AAP EE.
2. Sync the `codex-desktop` Argo application. A stopped VM without a root disk
   is expected until provisioning runs. The retained PVC may wait for first
   consumer scheduling depending on storage binding policy.
3. Launch AAP `codex_desktop_rollout`. Its managed credentials provide the
   cluster token, the existing `igou` SSH key, and 1Password Connect. Fedora
   metadata is filtered to stable Cloud Base Generic x86_64 qcow2, excluding
   preview/UKI variants. The signed checksum and downloaded artifact are
   verified before CDI import. The source release, URL, and SHA256 are recorded
   on the DataVolume; temporary image/credential files are removed.
4. The same job starts the VM and discovers its in-cluster SSH Service. It
   installs GNOME/GDM, the signed official OpenAI desktop RPM, Codex CLI, T3,
   pinned Node.js, native build prerequisites, Firefox, guest agent, time
   synchronization, Tailscale, and node exporter. Chrome is optional through
   `fedora_desktop_chrome`.
5. Open the VM's console in the OpenShift UI. Complete app sign-in and keyring
   enrollment/unlock as the unprivileged `codex` desktop user. Pair the T3
   client with the Tailscale Serve HTTPS endpoint. Enable native Computer Use
   when the app provides it; this groundwork does not implement a replacement.

The GNOME session uses normal distribution defaults including Wayland.
Autologin, no idle locking/blanking, and masked sleep targets keep this dedicated
desktop available. SELinux stays enabled. GDM refuses startup if required
state/home mounts cannot be mounted. `t3 serve` runs as the user's service on
loopback, with HTTPS supplied by the existing Tailscale Serve role.

The desktop account has UID/GID 1100. Its home at `/home/codex` is a bind mount
from `/var/lib/codex-desktop/home`; workspaces, browser/app/keyring state,
`.codex`, and T3 state therefore survive root replacement. Tailscale identity
is retained separately under root-only persistent storage and bind-mounted to
`/var/lib/tailscale`. OAuth credentials are resolved only by the controller,
which passes a single-use, one-hour join key to the guest through a temporary
root-only file. An already enrolled guest is not joined again.

## Day-two entry points

| AAP template | Operation |
|---|---|
| `codex_desktop_rollout` | Create root when absent, start, and configure; reuse existing root on reruns |
| `codex_desktop_setup` | Converge the running guest without replacing its root |
| `codex_desktop_start` / `codex_desktop_stop` | Power operations preserving disks and enrollment |
| `codex_desktop_rebuild` | Authenticate/download the newest stable image, stop/detach, replace only root, start/configure around retained state |
| `codex_desktop_retire_windows` | Stop/orphan the legacy Windows VM, remove its Services/sysprep Secret, and retain all Windows disks |

Use the same VM start/stop jobs regardless of host. Casval capacity is managed
separately from this desktop. Stop the VM and allow disk detachment before
removing a host that runs it. Casval remains an RHCOS host; Fedora runs inside
the VM. There is no GPU passthrough or promised live migration.

Normal stop/start and setup do not select a different Fedora release or replace
root. App package updates use the signed repository configured by the official
RPM installer. OS major-version changes happen only on explicit root rebuilding.
There is no operation that destroys the retained state disk.

## Inspection and recovery

Verify the cluster identity before read-only inspection:

```bash
oc whoami --show-server
oc whoami
oc get vm,vmi,dv,pvc -n codex-desktop
oc get lease codex-desktop-operation -n codex-desktop -o yaml
oc get dv codex-desktop-root -n codex-desktop -o yaml
```

Inside the guest, check the required mounts and services:

```bash
findmnt /var/lib/codex-desktop
findmnt /home/codex
findmnt /var/lib/tailscale
systemctl status gdm qemu-guest-agent tailscaled
sudo -u codex XDG_RUNTIME_DIR=/run/user/1100 systemctl --user status t3
tailscale serve status
```

- An incomplete root import is rejected on a normal rollout. Inspect the
  DataVolume and explicitly rebuild after resolving the error.
- A failed or canceled job can leave an operation lease. Inspect the owning
  AAP job and confirm it has stopped before explicitly clearing the lease's
  holderIdentity; normal automation never steals it.
- A mount failure blocks graphical login. Repair the existing disk/mount;
  never format a filesystem to bypass an error.
- A local EE can override `codex_desktop_ssh_host` and
  `codex_desktop_ssh_port` with a reachable guest endpoint or port forward.
  AAP's Service endpoint avoids a stale inventory discovery race.
  SSH tracks host keys by the root DataVolume UID. An explicit rebuild gets a
  fresh identity; a changed key on the same root still fails verification.
- The legacy retirement job is separate from rollout. Run it after reviewing
  the replacement. Shared Windows roles, test scenarios, image library,
  `windows-administrator` item, and boot/firmware PVCs remain intact.

## Verification

Verification on 2026-10-08 passed the full OpenShift GitOps gate,
production Ansible lint, playbook syntax checks, and the updated EE build.
Fedora's current signed checksum was verified inside that EE. The QEMU scenario
passed on upstream Fedora 44: GNOME and app autostart, Codex CLI, T3, metrics,
Firefox defaults, guest agent, SELinux enforcement, zero-change reruns before
and after root replacement, retained workspace/service state, and protected
Tailscale storage.

Live OCP validation used local Ansible Navigator and the built EE. Initial
rollout and a full root rebuild completed; the rebuild used the production
`virtualmachine-ops` service account, including CDI upload with token-only
credentials. The state PVC, workspace, protected service state, and real
Tailscale device identity survived root replacement. Setup reruns made no guest
configuration changes. GNOME and the app autostarted after clean stop/start on
both casval and a worker. T3 returned HTTPS 200 with certificate validation,
Prometheus scraped the guest successfully, and the existing AAP execution pod
reached SSH through the internal Service.

Argo remained `Synced / Healthy` during operation lease ownership and kept a
manually stopped VM off. Overlapping desktop operations were rejected. The
worker test used nested KVM: TrueNAS VM `truenasw1` backs the RHCOS node
`truenas-w1.igou.systems`, confirmed through a matching hypervisor/node UUID.
The casval test used a bare-metal host. Casval capacity management was removed
from the final implementation; its active reservation was not cycled down/up.
The Windows retirement job was
not run, and its VM/disks remain. OADP schedule adoption and a completed backup
remain pending. Interactive app sign-in, T3 pairing, native Computer Use, and
cross-client dispatch remain outside these tests.

The live desktop is at `https://codex-desktop.weasel-alioth.ts.net`. The
validation Argo Application follows the feature branch until the GitOps PR is
merged; normal main-based adoption, EE publication, and AAP template convergence
are separate rollout steps.

```bash
molecule test -s logic-codex-desktop
molecule test -s role-fedora-desktop
```

The logic scenario checks production release selection without a cluster.
The role scenario uses the pinned
`david_igou.molecule_provisioners` QEMU backend, an upstream cloud image, and an
independent temporary block disk. It checks T3/Codex/GNOME/guest-agent behavior,
SELinux, idempotency, and retained workspace data after replacing the OS.
It needs local KVM/QEMU, qemu-img, genisoimage, network access, and 8Gi free RAM.
The default SSH forward is 23222; lifecycle resources are isolated to Molecule's
ephemeral directory. Native Computer Use and cross-client dispatch remain
outside acceptance tests until platform support is available.

References: [Fedora Cloud](https://fedoraproject.org/cloud/download/),
[official OpenAI Linux app](https://learn.chatgpt.com/docs/linux/linux-app),
[KubeVirt run strategies](https://kubevirt.io/user-guide/compute/run_strategies/).
