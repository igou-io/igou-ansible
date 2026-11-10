# Kubernetes playbooks

Bring up and bootstrap the **`rk8s`** cluster — five ARM SBCs running on
the lab netboot fleet. Two install paths are supported; pick one per
cluster lifetime, don't mix.

| File                              | Purpose                                                        |
| --------------------------------- | -------------------------------------------------------------- |
| `install-k3s-cluster.yml`         | **Primary.** Install k3s via `xanmanning.k3s`.                 |
| `upgrade-k3s.yaml` | Converge one complete existing SQLite cluster to its inventory pin, server first and workers serially, with a recovery archive and health gates. |
| `prepare-k3s-datastore.yml`       | ZFS pool + data-dir mounts on the control plane (run first).   |
| `install-kubernetes-cluster.yml`  | Alternative. Install kubeadm-based kubernetes via `geerlingguy.kubernetes` (+ `geerlingguy.containerd`). |
| `bootstrap-gitops.yaml`           | Post-install: seeds the ESO Connect token, installs argocd, applies the igou-kubernetes app-of-apps root. Runs against `localhost`, talks to whatever `KUBECONFIG` points at. |
| `bootstrap-vps.yaml` | Standalone Debian VPS: public API firewall guard, host Tailscale, and lean pinned k3s; node-exporter is a GitOps-managed pod. |
| `publish-vps-access.yaml` | Seeds only the `argocd` namespace and cluster-admin Argo identity through the Kubernetes API using system:admin, then publishes its generated token, CA and administrator kubeconfig to the management vault. Argo's wave-0 `bootstrap` Application creates the other namespaces; adding a workload namespace needs no AAP run. Seeds no pods or separate ESO credential. |

## Pinned k3s upgrades

`upgrade-k3s.yaml` targets `ansible_limit` (default `none`); select all of `rk8s`
or the standalone `igou.io` host. It supports the current systemd, amd64/arm64,
single-server SQLite installations at `/usr/local/bin` and `/var/lib/rancher/k3s`.
It leaves bootstrap-owned service configuration, firewall and Tailscale alone.
The bundled `k3s kubectl` client uses the server's native kubeconfig over SSH;
no cluster credentials are fetched into the controller or AAP inventory.

Inventory supplies `k3s_release_version`, optional `k3s_upgrade_workloads`
(`namespace` and `resource` such as `deployment/argocd-server`), and
`k3s_upgrade_delete_emptydir_data` (default `false`). Worker drain respects PDBs
and does not force-delete unmanaged pods. Enabling emptyDir deletion permits
eviction of disposable node-local data; PVC data is not deleted by that flag.
Failed drain or health gates stop the remaining nodes; a failed worker may
remain cordoned for investigation. Failed upgrades do not trigger an automatic
rollback.

Before a server upgrade, k3s stops briefly to archive its server data, token,
configuration and old binary into root-only
`/var/backups/k3s/<target-version>/recovery.tgz`. This on-host archive is not an
off-host backup or a backup of workload volumes. Both clusters have one server,
so allow for API interruption during backup and restart. Matching versions skip
download, backup, drain and restart. Check mode is explicitly unsupported.

AAP uses `k3s_upgrade_cluster` behind the `k3s_upgrade_rk8s` and
`k3s_upgrade_vps` workflows. The companion inventory defines their weekly
maintenance schedules. Operational configuration, recovery and verification belong in
`igou-docs/kubernetes/rk8s Node Lifecycle - Adding, Upgrading, and Retiring Boards.md`
and `igou-docs/kubernetes/VPS Kubernetes Architecture and Migration.md`.

## Standalone VPS bootstrap

The VPS is cluster `vps`, inventory host `igou.io`. Target both VPS playbooks
explicitly with `ansible_limit=igou.io`; their default target is `none`.
AAP templates are `vps_bootstrap_host` and `vps_publish_access`.
Host settings and all secret lookup expressions belong in inventory.
Bootstrap installs Debian `python3-kubernetes` and `python3-jsonpatch` for the
API modules. The publisher reads the native admin kubeconfig into memory and
uses `kubernetes.core.k8s` over localhost. It stages no manifests or Secret files
under `/etc/rancher/k3s`; k3s owns its native admin kubeconfig there.
These Python libraries add no running daemon.

VPS Kubernetes manifests live in `igou-kubernetes/clusters/vps/`. OpenShift-side
Argo/ESO management resources live in
`igou-openshift/clusters/ocp/{vps-gitops,vps,vps-secrets}/`. A dedicated Argo CD
instance runs on OpenShift; one ESO controller on the VPS distributes mirrored
Secrets through `ClusterSecretStore/local-mirrors`. Do not use the rk8s install/bootstrap path unchanged:
it disables a firewall, installs homelab storage clients, and installs local
controllers. The public firewall guard must be active before the first k3s
startup and remain a service dependency. Node-exporter runs as a host-network
DaemonSet in `node-exporter`, rendered from the Prometheus community Helm chart;
the guard blocks public TCP 9100. GitOps installs it after the AAP seed. The shared
host exporter playbook refuses `cluster_name=vps`. Operational guidance is in
`igou-docs/kubernetes/VPS Kubernetes Architecture and Migration.md`.

## Inventory

`rk8s` is the cluster group. It has two child groups:

```
rk8s
├── rk8s_control_plane    -> cm3588-nas-01
└── rk8s_workers          -> opi5pro-01, rock-5a-01, orange-pi-5-01, orange-pi-5-max-01
```

Each host carries both role flags so the same inventory works for
either install path:

- `k3s_control_node: true|false`           — read by `xanmanning.k3s`
- `kubernetes_role: control_plane|node`    — read by `geerlingguy.kubernetes`

Cluster-wide defaults (CIDRs, feature gates, `cluster_name`) live in
`igou-inventory/group_vars/rk8s.yml`.

## Install — k3s (primary)

```bash
ansible-navigator run playbooks/kubernetes/install-k3s-cluster.yml \
  -i igou-inventory/inventory.yaml \
  -e ansible_limit=rk8s
```

After install, the imported `publish-kubeconfig-1password.yaml` play
slurps the kubeconfig from the control-plane node
(`/etc/rancher/k3s/k3s.yaml`), rewrites the server URL to the control
node's FQDN, and upserts it to `op://lab_rk8s/<cluster>-kubeconfig` (the
`kubeconfig` field is base64 — decode on use). The devcontainer loads
it with `use rk8s` (igou-devenv `envs/rk8s.env`). Needs
`OP_CONNECT_HOST` and `OP_CONNECT_TOKEN` in the env (AAP injects them
via the "Onepassword Connect" credential) and a token with write access
to the lab_rk8s vault; without the env the publish step skips with a
warning. Re-publish on demand via the `k3s_publish_kubeconfig` AAP
template or by running the publish playbook directly.

## Install — kubernetes (alternative)

```bash
ansible-navigator run playbooks/kubernetes/install-kubernetes-cluster.yml \
  -i igou-inventory/inventory.yaml \
  -e ansible_limit=rk8s
```

The kubeadm path requires container runtime + kernel sysctls; the
playbook handles both via `geerlingguy.containerd` and an
`net.ipv4.ip_forward` pre-task.

## Bootstrap (either distro)

`bootstrap-gitops.yaml` bootstraps a cluster from igou-kubernetes'
app-of-apps layout (see `igou-docs/kubernetes/Bootstrapping the rk8s Cluster.md` there). It seeds the
`external-secrets` namespace + Connect token secret backing the
`onepassword` ClusterSecretStore, installs argocd, and applies
`clusters/<cluster>` — after which argocd self-manages the cluster. The
two kustomize stages build straight from the remote repo (no local
clone). It needs two env vars:

- `KUBECONFIG` — points the `kubernetes.core.*` modules at the target
  cluster (e.g. `use rk8s` in the devcontainer).
- `OP_SERVICE_ACCOUNT_TOKEN` — the `ocp-bootstrap` SA token (`ops_...`);
  the `community.general.onepassword` lookup reads the rk8s **entity**
  Connect token from the `ocp-connect-bootstrap` vault via its env
  fallback (same pattern as `openshift/bootstrap_gitops.yaml`). AAP
  injects it via a credential.

```bash
KUBECONFIG=~/.kube/rk8s OP_SERVICE_ACCOUNT_TOKEN=ops_... \
  ansible-playbook playbooks/kubernetes/bootstrap-gitops.yaml \
  -e gitops_cluster=internal
```

Application CRs track `targetRevision: HEAD` — only bootstrap from a
ref whose content matches the repo's default branch. All cluster
interaction runs through `kubernetes.core.*` (no shell/kubectl),
mirroring `playbooks/openshift/bootstrap_gitops.yaml`. The
`kubernetes_bootstrap_gitops` AAP template runs this playbook.

## Notes

- No load balancer playbook. The cluster fronts itself via the control
  plane's `tls-san`; external traffic comes in through ingress and the
  router, not a dedicated HAProxy box.
- No serviceaccount-creation playbook. Robot tokens are provisioned
  by the cluster's GitOps overlays, not by Ansible.

## ZFS datastore (control plane)

`prepare-k3s-datastore.yml` (AAP: `k3s_prepare_datastore`) builds or
imports the `rk8s` raidz1 pool over cm3588-nas-01's four NVMe drives
and mounts `/var/lib/rancher/k3s` (+ `server/db`) as legacy-mountpoint
ZFS datasets with systemd `.mount` units. `k3s.service` hard-requires
`var-lib-rancher-k3s.mount` via `k3s_service_requires` in inventory, so
a failed pool import stops k3s instead of letting it write cluster
state into the empty eMMC directory. Run it after every reimage of the
control plane (the pool imports; the mount units re-render) and before
`install-k3s-cluster.yml`. First-ever creation needs
`-e zfs_pool_wipe=true`, which wipes all four NVMe drives.
