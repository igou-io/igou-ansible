# playbook-windows-deploy_iis_website

Live, on-cluster scenario that exercises
`playbooks/windows/deploy_iis_website.yaml` against a real **Windows Server
2025** VM. It provisions one `win2k25` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), installs IIS and deploys
`demo-site` on port 8080, then proves the site is reachable **from outside the
guest** — the real value the playbook claims.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount. Copied from the PILOT `playbook-windows-manage_services`; see
that scenario's README for the shared-plumbing architecture.

## Prerequisites

- The live `ocp.igou.systems` cluster reachable, with the
  `ocp-ansible-molecule` SA token + API host exported, the SA's cross-namespace
  CDI clone grant for `win2k25`, a `win2k25` golden DataSource present, and the
  SA able to create Services + list Nodes in/across the cluster (the pilot
  already relies on the same Node-list grant for its NodePort connection IP).
- **`pypsrp`** on the controller/EE (runtime lib for the `psrp` connection).
- `ansible.windows` + `community.windows` + `microsoft.iis` (installed by the
  galaxy dependency step from `collections.yml`).

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s playbook-windows-deploy_iis_website
```

## What verify proves

1. **In-guest, independent:** `Get-IISSite -Name demo-site` reports the site
   exists — read with a raw IISAdministration cmdlet, NOT the `microsoft.iis`
   modules that created it.
2. **External, the real value:** verify creates its own NodePort Service
   (`winiis-test-iis`) selecting the VM pod (`kubevirt.io/domain=winiis-test`) on
   8080, resolves a node InternalIP + the assigned nodePort, and does an HTTP
   `GET` from the controller. It asserts **200** AND that the body contains
   `It works!`. The playbook's own `win_uri` only ever hit `localhost` inside
   the VM; this proves the site is reachable through the guest firewall (the
   firewall rule the playbook adds for 8080 is therefore doing its job).
3. `C:\molecule-specialize-done.txt` exists — the sysprep unattend
   FirstLogonCommands ran, so the whole specialize/WinRM path executed.

The verify Service is created and torn down within verify's own `always` block,
so it never leaks even if the GET assertion fails; `destroy.yml` deletes it
again (state=absent no-op) as a backstop.

## Watch items (feed to Phase 5 — not pre-solved)

- **`microsoft.iis.website` `ip: '*'` binding acceptance is unconfirmed live.**
  The playbook binds `ip: '*'` port 8080. If converge fails inside the "Create
  the website" task on the binding, the known fix is to **omit** `ip` from the
  binding (let it default). Do NOT pre-apply that here — the scenario runs the
  real, unmodified playbook; if it bites, fix the playbook under a separate
  review, not this scenario.
- **NodePort reachability + W3SVC warm-up.** The external GET retries 15×8s
  (~2 min) to absorb NodePort propagation and IIS warm-up. If it still times
  out, confirm from the controller that the node InternalIP:nodePort is
  routable (the pilot's psrp connection uses the same node-IP path, so a working
  pilot implies this route is open).

## Cleanup semantics

`destroy` removes the VM + the provisioner's SSH NodePort Service, the extra
`winiis-test-iis` site Service, and the `<host>-sysprep` Secret. `state: absent`
is idempotent, so re-running destroy — or destroying after a failed create — is
a safe no-op. The `molecule` namespace ends empty of scenario resources.
