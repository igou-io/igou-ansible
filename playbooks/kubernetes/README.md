# Kubernetes playbooks

Bring up and bootstrap the **`rk8s`** cluster — five ARM SBCs running on
the lab netboot fleet. Two install paths are supported; pick one per
cluster lifetime, don't mix.

| File                              | Purpose                                                        |
| --------------------------------- | -------------------------------------------------------------- |
| `install-k3s-cluster.yml`         | **Primary.** Install k3s via `xanmanning.k3s`.                 |
| `install-kubernetes-cluster.yml`  | Alternative. Install kubeadm-based kubernetes via `geerlingguy.kubernetes` (+ `geerlingguy.containerd`). |
| `bootstrap-gitops.yaml`           | Post-install: seeds the ESO Connect token, installs argocd, applies the igou-kubernetes app-of-apps root. Runs against `localhost`, talks to whatever `KUBECONFIG` points at. |

## Inventory

`rk8s` is the cluster group. It has two child groups:

```
rk8s
├── rk8s_control_plane    -> rock-5b-01
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
app-of-apps layout (see `docs/bootstrap.md` there). It seeds the
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
