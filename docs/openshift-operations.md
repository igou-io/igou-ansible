# OpenShift operations runbook

End-to-end lifecycle for the OpenShift cluster `ocp` (and any future
clusters) — initial install via agent-based, GitOps bootstrap, add-node,
TrueNAS VM worker lifecycle, etcd maintenance, and CSR approval.

## What's in this homelab

| Cluster | Type | Inventory host | Notes |
|---|---|---|---|
| `ocp` | Multi-node (started from agent-based) | `ocp` (in `openshift_clusters`) | The current cluster. `host_vars/ocp.yml` has the agent-install + add-node config |

`openshift_workers_<cluster>` is the per-cluster worker group. `ocp`'s
worker group is `hpg5.igou.systems`, `p330.igou.systems`, and
`truenas-w1.igou.systems` (a KVM guest on TrueNAS, not bare metal — see
[VM worker lifecycle](#vm-worker-lifecycle)).

## Playbooks

| Playbook | What it does |
|---|---|
| [`agent-install/deploy_pxe_assets.yml`](#initial-cluster-agent-install) | Render the agent-based install ISO/PXE kit + push to TrueNAS |
| [`add_node_iso.yml`](#add-a-worker) | Generate `oc adm node-image create --pxe` artifacts for a new worker (links to `netboot-operations.md`) |
| [`bootstrap_gitops.yaml`](#gitops-bootstrap) | Install OpenShift GitOps + external-secrets + 1Password Connect, apply the `root-applications` app-of-apps |
| [`vm_worker_reprovision.yaml`](#vm-worker-lifecycle) | Rebuild the TrueNAS VM worker (`truenas-w1`) end to end |
| [`vm_worker_destroy.yaml`](#vm-worker-lifecycle) | Drain, remove, and delete the TrueNAS VM worker |
| [`etcd_defrag.yaml`](#etcd-maintenance) | Defragment etcd members (daily `openshift_etcd_defrag` job template) |

## Initial cluster (agent-install)

Brings up a brand-new cluster via the agent-based installer. The `ocp` cluster
was bootstrapped this way; the same flow is used for any new cluster.

### What `host_vars/<cluster>.yml` must define

Reference: see `igou-inventory/host_vars/ocp.yml` for a working example.
Required fields (the role `david-igou.openshift_agent_install` consumes):

```yaml
cluster_name: ocp
ansible_connection: local
ansible_python_interpreter: "{{ ansible_playbook_python }}"

# Agent-install
openshift_agent_install_boot_mac: "58:47:ca:77:09:8a"   # rendezvous host MAC
openshift_agent_install_version: "4.21.9"
openshift_agent_install_config:
  apiVersion: v1
  baseDomain: igou.systems
  metadata:
    name: ocp
  compute:
    - architecture: amd64
      hyperthreading: Enabled
      name: worker
      replicas: 0       # workers join later via add_node_iso
  controlPlane:
    architecture: amd64
    hyperthreading: Enabled
    name: master
    replicas: 1         # SNO; bump to 3 for HA
  networking:
    clusterNetwork: ...
    serviceNetwork: ...
    machineNetwork: ...
```

`host_vars/ocp.yml` still carries an `onepassword_tokens` list from the
pre-Connect secret model. **No playbook or role in this repo reads it any
more** — the bootstrap playbook seeds 1Password Connect from the
`ocp-connect-bootstrap` vault instead (see
[GitOps bootstrap](#gitops-bootstrap)). Do not copy it into a new cluster's
host_vars.

### Initial install flow

1. Make sure inventory has the cluster + boot MAC. Add a `netboot_host_pins`
   entry pointing at the agent-install rendezvous URL (similar to how hpg5
   has a menu — see [`netboot-operations.md`](netboot-operations.md)).
2. Render PXE assets:
   ```bash
   ansible-playbook playbooks/openshift/agent-install/deploy_pxe_assets.yml \
     -i igou-inventory/inventory.yaml \
     -e target_cluster=<cluster>
   ```
   This runs the `david-igou.openshift_agent_install` role to generate
   `agent.x86_64-{vmlinuz,initrd.img,rootfs.img}`, then rsyncs them to the
   public nginx share on TrueNAS at `/mnt/ssd/public/boot-files/<cluster>/`
   (served as `https://public.igou.systems/boot-files/<cluster>/`) and
   HEAD-checks all three over HTTPS. The retired netbootxyz container paths
   (`/mnt/ssd/containers/netbootxyz/…`) are no longer used. The iPXE script
   is deliberately **not** published — the boot flow is the rb5009 per-host
   pin, which carries its own kernel/initrd/rootfs URLs.
3. Save the cluster auth files to 1Password — the `op-save` block does this
   automatically when `OP_SERVICE_ACCOUNT_TOKEN` is set (rerun with
   `--tags op-save` if needed). It creates a **new, timestamped** item
   `<cluster>-kubeconfig-<YYYYMMDDHHMM>` in the **`ansible-push`** vault,
   with fields `kubeconfig`, `kubeconfig_self_signed` (both base64),
   `client_certificate_data`, `client_key_data`, `api_url`. The
   kubeadmin password is **not** saved — copy it out of the auth dir
   yourself if you want it kept.
4. PXE-boot the rendezvous host (e.g. `5847ca77098a` for `ocp`).
5. Watch progress: `oc adm wait-for install-complete --dir <work_dir>` from
   the cluster host.
6. Once the cluster reports ready, kubeconfig + kubeadmin password live in
   `~/openshift-agent-install/<cluster>/cluster-manifests/auth/`.

### Re-run / refresh

Re-running `deploy_pxe_assets.yml` regenerates the ISO and re-pushes the
assets. Useful when the install token rotates or the version pin bumps.

## Add a worker

Covered fully in [`netboot-operations.md`](netboot-operations.md#openshift-add-a-worker-via-pxe).

Short version:
1. Worker MAC into `openshift_workers_<cluster>` + `netboot_host_pins`.
2. `playbooks/netboot/deploy_assets.yml --tags render,push,verify` (one-time).
3. `playbooks/openshift/add_node_iso.yml -e target_cluster=<cluster>`.
4. PXE-boot the worker.
5. `oc get csr` / `oc adm certificate approve <name>`.

## VM worker lifecycle

`truenas-w1.igou.systems` is a KVM guest on TrueNAS, not a PXE-booted bare
metal host, so it does **not** go through `add_node_iso.yml` + a netboot pin.
Its lifecycle is two dedicated playbooks, both gated by
`vm_worker_state` (`create` | `rebuild` | `destroy`, default `rebuild`) so
they can sit in the linear `ocp-truenas-worker-node-manage` AAP workflow:

```bash
export KUBECONFIG=<cluster kubeconfig>

# Drain + remove the Node, power off and delete the VM (and its zvol)
ansible-playbook playbooks/openshift/vm_worker_destroy.yaml \
  -i igou-inventory/inventory.yaml -e vm_worker=truenas-w1.igou.systems

# Recreate the VM, build a full add-node ISO, boot it, approve CSRs
ansible-playbook playbooks/openshift/vm_worker_reprovision.yaml \
  -i igou-inventory/inventory.yaml -e vm_worker=truenas-w1.igou.systems
```

In `rebuild` mode the reprovision playbook asserts the node is absent — run
the destroy playbook first (or use the `ocp-truenasw1-rebuild` workflow,
which chains both). The VM itself is declared in `truenas_vms`
(`igou-inventory/group_vars/truenas.yml`) and created by
`playbooks/truenas/configure_vms.yaml`.

## etcd maintenance

The cluster-etcd-operator's `DefragController` skips SingleReplica
topologies, so this SNO cluster never self-defrags.
`playbooks/openshift/etcd_defrag.yaml` is the scheduled substitute (a no-op
below the configured fragmentation percent AND db >=100MB). The playbook
default is the operator's 45%; the daily AAP schedule overrides to 25%
via extra_vars. It runs daily via the `openshift_etcd_defrag` job template
(`openshift_etcd_defrag_daily` schedule).

Backups are separate and live in `igou-openshift`: a nightly `etcd-backup`
CronJob (namespace `etcd-backup`) ships snapshots to
`s3://etcd-backups/<z-stream>/<timestamp>/` on rustfs-cold. Restore
procedure: `igou-openshift` `docs/runbooks/etcd-backup-restore.md`.

## GitOps bootstrap

After a fresh cluster is up, install OpenShift GitOps + external-secrets +
1Password Connect, then hand the cluster over to the GitOps tree.
`playbooks/openshift/bootstrap_gitops.yaml` is the **only** bootstrap
playbook — the former `bootstrap_openshift_gitops.yaml` and the
`hub-cluster/` variant were consolidated into it (#329).

### Required env

- `KUBECONFIG` pointing at the new cluster.
- `OP_SERVICE_ACCOUNT_TOKEN` — the read-only `ocp-bootstrap` 1Password
  service-account token (`ops_…`), scoped to the `ocp-connect-bootstrap`
  vault. It is a break-glass credential held in your **personal/admin**
  vault; there is deliberately no automation-readable 1Password item for it,
  so it cannot be fetched with `op read`. The playbook's `vars_prompt` was
  removed on purpose (it returns empty under ansible-navigator/AAP), so the
  env var must be exported before the run.

### Run

```bash
export OP_SERVICE_ACCOUNT_TOKEN=ops_...
ansible-playbook playbooks/openshift/bootstrap_gitops.yaml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=<cluster>
```

What it does:
1. Asserts `OP_SERVICE_ACCOUNT_TOKEN` is set.
2. Creates `openshift-gitops-operator` namespace + OperatorGroup +
   Subscription, and the `external-secrets-operator` **namespace only**
   (it exists to receive the seeded Connect token Secret — ESO itself is
   installed by GitOps at sync-wave 0, not by this playbook).
3. Creates the `onepassword-connect` namespace and seeds two secrets from
   the `ocp-connect-bootstrap` vault: the Connect server credentials
   (`ocp-connect-credentials`) and the Connect access token
   (`ocp-connect-token`, used by ESO).
4. Binds `cluster-admin` to the ArgoCD application controller
   (`gitops-cluster-admin` ClusterRoleBinding).
5. Creates the `ArgoCD` CR. **This CR carries the recovery-critical tuning
   folded in after the 2026-07-03 DR** — repo-server `ARGOCD_EXEC_TIMEOUT=3m`
   and `cpu: 2` limits, without which heavy helm-in-kustomize renders trip
   `ComparisonError: DeadlineExceeded` and stall the whole app-of-apps. Do
   not hand-patch these live; they live in this playbook so a re-run cannot
   revert them.
6. Creates the `setenv-cmp-plugin` and `environment-variables` ConfigMaps
   (cluster name + base domains, discovered from the `Ingress` CR).
7. Applies the top-level `Application` **`root-applications`**, pointing at
   `clusters/<target_cluster>` in `igou-openshift`.

### Tags

`--tags create-objects` is the only tag on this playbook (it covers the whole
task block).

## CSR approval

OpenShift workers issue 2 CSRs each (one node-bootstrapper, one node-client).
Approve them both:

```bash
oc get csr -o name | xargs oc adm certificate approve
# or just the pending ones:
oc get csr | awk '/Pending/{print $1}' | xargs oc adm certificate approve
```

`add_node_iso.yml`'s `--tags monitor` mode watches for the worker to report
in but does NOT approve CSRs (the docs note reverse-DNS gating; just do them
manually).

## Common breaks

- **`OP_SERVICE_ACCOUNT_TOKEN` not set** → `bootstrap_gitops.yaml` fails at
  `Assert the bootstrap service-account token is present`. This is the
  `ocp-bootstrap` SA token from your personal/admin vault; it is not
  readable by `op read` from automation (see
  [Required env](#required-env)).
- **`KUBECONFIG` not set** → multiple playbooks `assert` it; export the
  path from the cluster's auth dir.
- **Old install lingering in work dir** → `deploy_pxe_assets.yml` wipes the
  work dir at the start, which is intentional. If you wanted to preserve a
  prior install, copy the auth dir aside before re-running.
- **Pull secret RBAC denied** → `add_node_iso.yml` extracts the cluster
  pull-secret via `oc -n openshift-config get secret pull-secret`. The
  kubeconfig must have `get` on secrets in `openshift-config` (cluster-admin
  does by default).
- **GitOps Application stuck in `Unknown` state** → check the matching
  `igou-openshift` repo for the cluster-config; the `bootstrap_gitops`
  playbook creates the Application but the SOURCE_OF_TRUTH for what it
  reconciles lives in that repo.
- **Add-node worker boots stock netbootxyz menu instead of the OCP joiner**
  → see [`troubleshooting.md`](troubleshooting.md) — usually means the
  `netboot_host_pins` entry isn't deployed yet (`deploy_assets.yml` not
  run after the inventory edit).

## Cross-references

- PXE asset details, dnsmasq, host pins → [`netboot-operations.md`](netboot-operations.md)
- Cluster rebuild from scratch → [`disaster-recovery.md`](disaster-recovery.md)
- Symptom-keyed debugging → [`troubleshooting.md`](troubleshooting.md)
