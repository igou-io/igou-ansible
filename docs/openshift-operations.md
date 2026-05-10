# OpenShift operations runbook

End-to-end lifecycle for the OpenShift cluster `ocp` (and any future
clusters) — initial install via agent-based, GitOps bootstrap, secret
sync, add-node, SNO ISO, and CSR approval.

## What's in this homelab

| Cluster | Type | Inventory host | Notes |
|---|---|---|---|
| `ocp` | Multi-node (started from agent-based) | `ocp` (in `openshift_clusters`) | The current cluster. `host_vars/ocp.yml` has the agent-install + add-node config |

`openshift_workers_<cluster>` is the per-cluster worker group. `ocp`'s
worker group currently has `hpg5.igou.systems`.

## Playbooks

| Playbook | What it does |
|---|---|
| [`agent-install/deploy_pxe_assets.yml`](#initial-cluster-agent-install) | Render the agent-based install ISO/PXE kit + push to TrueNAS |
| [`add_node_iso.yml`](#add-a-worker) | Generate `oc adm node-image create --pxe` artifacts for a new worker (links to `netboot-operations.md`) |
| [`sno_iso_provision.yml`](#sno-iso) | Generate a single-node OpenShift ISO |
| [`bootstrap_openshift_gitops.yaml`](#gitops-bootstrap) | Install OpenShift GitOps + external-secrets, register `ansible` SA token in 1Password |
| [`hub-cluster/bootstrap_gitops.yaml`](#hub-cluster-bootstrap) | Same but for a hub-cluster pattern (separate config tree) |
| [`sync_1pasword_secrets.yml`](#sync-secrets-back-to-1password) | Pull serviceaccount tokens out of OCP and update 1Password records |

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

# 1Password tokens read into in-cluster secrets after bootstrap
onepassword_tokens:
  - name: onepassword-sdk-ocp-pull-token
    contents: "{{ lookup('community.general.onepassword', 'onepassword-sdk-ocp-pull-token', field='credential', vault='awx') }}"
  - name: onepassword-sdk-ocp-push-token
    contents: "{{ lookup('community.general.onepassword', 'onepassword-sdk-ocp-push-token', field='credential', vault='awx') }}"
```

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
   `agent.x86_64-{vmlinuz,initrd.img,rootfs.img}` plus the iPXE script,
   then pushes them to `/mnt/ssd/containers/netbootxyz/assets/<cluster>/`.
3. Save the cluster auth files to 1Password (the playbook does this
   automatically with `OP_SERVICE_ACCOUNT_TOKEN` set):
   ```bash
   export OP_SERVICE_ACCOUNT_TOKEN=$(op read "op://awx/onepassword-sdk-claude-container-token/credential")
   # already happens inside the playbook; rerun with --tags op-save if needed
   ```
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

## SNO ISO

`sno_iso_provision.yml` generates a single-node OpenShift install ISO without
the PXE flow — useful for offline or USB installs.

```bash
ansible-playbook playbooks/openshift/sno_iso_provision.yml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=<cluster>
```

The output ISO lands in the cluster's work dir
(`~/openshift-agent-install/<cluster>/`). Burn it to USB or attach to a
BMC virtual media device.

## GitOps bootstrap

After a fresh cluster is up, install OpenShift GitOps + external-secrets +
register the `ansible` ServiceAccount token in 1Password.

### Required env

- `KUBECONFIG` pointing at the new cluster.
- `OP_SERVICE_ACCOUNT_TOKEN` — 1Password Service Account token with write
  to the `awx` vault.

### Run

```bash
ansible-playbook playbooks/openshift/bootstrap_openshift_gitops.yaml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=<cluster>
```

What it does:
1. Creates `openshift-gitops-operator` namespace + OperatorGroup +
   Subscription. Waits for the operator to converge.
2. Creates the `external-secrets` namespace + Subscription. Waits.
3. Creates a `1password-credentials` secret from the SDK token in 1Password
   (`onepassword-sdk-<cluster>-pull-token`).
4. Creates an `ansible` ServiceAccount with cluster-admin, generates a token
   secret, and writes the token back to 1Password as
   `onepassword-sdk-<cluster>-push-token` (so AAP/AWX can re-read it later).
5. Applies a top-level `Application` named `cluster-config` that points at
   the GitOps tree (e.g. `igou-openshift` repo).

### Tags

- `--tags create-objects` — only creates namespaces/CRs (skip the 1Password
  read/write).
- `--tags op-save` — only saves the SA token to 1Password.

### Hub-cluster variant

`hub-cluster/bootstrap_gitops.yaml` is a near-identical playbook for a hub
cluster pattern (different `Application` target, different vault layout).
Use it instead when bringing up the hub.

## Sync secrets back to 1Password

`sync_1pasword_secrets.yml` walks `serviceaccount_token_secrets` (defined per
cluster in inventory) and updates the matching 1Password records with current
token values. Run after token rotation or after bootstrap_openshift_gitops if
you need to re-sync.

```bash
export OP_SERVICE_ACCOUNT_TOKEN=...
ansible-playbook playbooks/openshift/sync_1pasword_secrets.yml \
  -i igou-inventory/inventory.yaml
```

Note the typo in the filename (`1pasword` not `1password`); fix in a future
cleanup pass.

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

- **`OP_SERVICE_ACCOUNT_TOKEN` not set** → the bootstrap_gitops playbook
  fails at `Validate OP_SERVICE_ACCOUNT_TOKEN is set`. Fetch from 1Password:
  `op read "op://awx/onepassword-sdk-claude-container-token/credential"`.
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
