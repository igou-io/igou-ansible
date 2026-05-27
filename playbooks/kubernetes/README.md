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

After install, grab the kubeconfig from the control-plane node
(`/etc/rancher/k3s/k3s.yaml`), rewrite the server URL to point at
`rock-5b-01.igou.systems:6443`, and stash it where the bootstrap step
expects it (see below).

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

`bootstrap-cluster.yml` is distro-agnostic. It needs a working
kubeconfig and a 1Password service-account token, then it deploys:

1. `external-secrets` operator
2. `argocd` (overlay from `igou-io/igou-kubernetes`)
3. A `ClusterSecretStore` wired to the `awx` 1Password vault

```bash
ansible-navigator run playbooks/kubernetes/bootstrap-cluster.yml \
  -e overlay=internal \
  -e vault_name=awx \
  -e onepassword_token="$(op read 'op://awx/onepassword-token/credential')" \
  -e kubeconfig=/path/to/rk8s-kubeconfig.yaml
```

`overlay` selects the overlay directory under
`https://github.com/igou-io/igou-kubernetes/config/<overlay>/`.

## Notes

- No load balancer playbook. The cluster fronts itself via the control
  plane's `tls-san`; external traffic comes in through ingress and the
  router, not a dedicated HAProxy box.
- No serviceaccount-creation playbook. Robot tokens are provisioned
  by the cluster's GitOps overlays, not by Ansible.
