# Kubernetes playbooks

Bring up and bootstrap the **`rk8s`** cluster — five ARM SBCs running on
the lab netboot fleet. Two install paths are supported; pick one per
cluster lifetime, don't mix.

| File                              | Purpose                                                        |
| --------------------------------- | -------------------------------------------------------------- |
| `install-k3s-cluster.yml`         | **Primary.** Install k3s via `xanmanning.k3s`.                 |
| `install-kubernetes-cluster.yml`  | Alternative. Install kubeadm-based kubernetes via `geerlingguy.kubernetes` (+ `geerlingguy.containerd`). |
| `bootstrap-cluster.yml`           | Post-install: ESO + ArgoCD + 1Password ClusterSecretStore. Runs against `localhost`, talks to the cluster API. |

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
node's FQDN, and upserts it to `op://claude/<cluster>-kubeconfig` (the
`kubeconfig` field is base64 — decode on use). The devcontainer loads
it with `use rk8s` (igou-devenv `envs/rk8s.env`). Needs
`OP_CONNECT_HOST` and `OP_CONNECT_TOKEN` in the env (AAP injects them
via the "Onepassword Connect" credential) and a token with write access
to the claude vault; without the env the publish step skips with a
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
app-of-apps layout (see `docs/bootstrap.md` there). It pulls the
kubeconfig from `op://claude/<cluster>-kubeconfig` (published by the
install playbook above), installs argocd, seeds the 1Password Connect
token secret backing the `onepassword` ClusterSecretStore, and applies
`clusters/<cluster>` — after which argocd self-manages the cluster.
Needs `OP_CONNECT_HOST`/`OP_CONNECT_TOKEN` in the env (AAP injects
them via the "Onepassword Connect" credential).

```bash
ansible-playbook playbooks/kubernetes/bootstrap-gitops.yaml \
  -e gitops_cluster=internal \
  -e kubeconfig_op_item=rk8s-kubeconfig
```

Application CRs track `targetRevision: HEAD` — only bootstrap from a
ref whose content matches the repo's default branch.

`bootstrap-cluster.yml` is the legacy version targeting the old
`config/<overlay>` layout; it is retained until the
`kubernetes_bootstrap_gitops` AAP template repoints, then it goes.

## Notes

- No load balancer playbook. The cluster fronts itself via the control
  plane's `tls-san`; external traffic comes in through ingress and the
  router, not a dedicated HAProxy box.
- No serviceaccount-creation playbook. Robot tokens are provisioned
  by the cluster's GitOps overlays, not by Ansible.
