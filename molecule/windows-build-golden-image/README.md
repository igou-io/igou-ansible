# windows-build-golden-image

Live, on-cluster molecule scenario that exercises
`playbooks/openshift_virtualization/build_windows_golden.yml`: it installs
Windows Server 2025 from an installer ISO, sysprep `/generalize`s it, and
publishes a bootable **golden DataSource** — then proves that image boots
hands-free.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ansible-molecule`**
ServiceAccount. The golden DataSource is published in `molecule`, never in
`openshift-virtualization-os-images` (the SA has no access there by design).

## Prerequisites

- The live `ocp.igou.systems` cluster reachable, with the `ansible-molecule`
  SA token exported (TLS verification works — public cert):

  ```bash
  export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
  export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
  ```

- A **preexisting**, **remastered no-prompt** Windows installer ISO DataVolume
  in the `windows-images` namespace — `iso-winserver2025-eval-noprompt`, phase
  `Succeeded`. This scenario reads it and gates on it; it never creates or
  deletes anything in `windows-images`. (The build requires a `-noprompt` ISO —
  the CD auto-boots with no keypress, and the flow is pure kubernetes.core, so
  **no `virtctl` is needed**.)
- Optional: `WINDOWS_GOLDEN_ADMIN_PASSWORD` to pin the throwaway build password
  (otherwise one is generated; sysprep wipes it from the published image).

## How to run

Full pipeline (create → converge → verify → destroy):

```bash
molecule test -s windows-build-golden-image
```

Step-wise (useful because converge is long-running):

```bash
molecule create  -s windows-build-golden-image   # preflight gates only
molecule converge -s windows-build-golden-image  # ~60–90 min build + publish
molecule verify   -s windows-build-golden-image  # boot-test proof
molecule destroy  -s windows-build-golden-image  # cleanup
```

## Expected duration

~60–90 minutes for converge (unattended Windows install + sysprep + publish);
verify adds ~5–15 minutes (clone + boot + guest-agent handshake). There is **no
idempotence step** — a multi-hour Windows install is not a re-runnable no-op.

## What verify proves

1. The golden **DataSource** `golden-win2k25` exists and its `Ready` condition
   is `True`.
2. Its backing **PVC** `golden-win2k25` is `Bound`.
3. A throwaway VM `golden-verify` cloned from the golden DataSource — with **no
   ISO and no unattend volume** — boots and its **guest agent connects**
   (`AgentConnected == True`). That is the hands-free proof: the generalized
   disk is self-sufficient with virtio drivers + `qemu-guest-agent` intact. The
   VM sits at interactive OOBE (cloudbase-init lands in P2), which is expected;
   the guest agent runs as a Windows service regardless.

## Cleanup semantics

`destroy` removes only the named resources this scenario owns in the `molecule`
namespace: the build/verify VMs, the installer-CD and golden DataVolumes, the
unattend Secret, and the golden DataSource. The golden DataSource is treated as
a **test artifact** here — production publishing is a separate concern in a
separate namespace. `destroy` never touches `windows-images`, and the SA cannot
delete namespaces.
