# playbook-windows-configure_firewall

Live, on-cluster scenario that exercises `playbooks/windows/configure_firewall.yaml`
against a real **Windows 11** VM. It provisions one `win11` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), runs the firewall
playbook unmodified with a rule list that exercises both directions plus a
present/absent transition, then proves the result with independent in-guest
reads and a negative external probe — not by trusting the modules' own returns.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount.

## Win11 client-SKU note

Windows 11 client SKUs disable the built-in Administrator, so the shared sysprep
unattend runs in **`local_account`** mode (`mp.kubevirt.unattend_admin_mode:
local_account`): it creates a `molecule` LocalAccounts admin and sets
`LocalAccountTokenFilterPolicy=1` so that non-builtin local admin gets a full
NTLM network token (psrp would otherwise 401 on privileged operations). See
`molecule/shared/templates/windows-unattend.xml.j2`.

## Architecture (shared vs. scenario-specific)

- **Shared plumbing** (`molecule/shared/`): the specialization unattend template
  and the `<host>-sysprep` Secret play (imported by `create.yml`/`destroy.yml`).
- **Scenario-specific** (this directory): the VM (`inventory/`), the converge
  rule list (`converge.yml`), the seed of the to-be-removed rule (`prepare.yml`),
  and the independent asserts + negative probe (`verify.yml`).

## Prerequisites

- The live `ocp.igou.systems` cluster reachable, with the `ocp-ansible-molecule`
  SA token/host exported, its cross-namespace CDI clone grant for `win11` in
  `openshift-virtualization-os-images`, and a `win11` golden PVC/DataSource there.
- **`pypsrp`** on the controller/EE (runtime lib for the `psrp` connection).
- Collections from `collections.yml` (installed by the galaxy dependency step).

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s playbook-windows-configure_firewall
```

Budget ~20–30 min end to end (smart-clone + OOBE specialize dominate).

## What verify proves

1. **Lockout invariant** — verify runs over the SAME psrp connection that
   survived converge; that it connects at all proves enabling the Public profile
   last did not sever WinRM.
2. All three firewall **profiles are enabled** (independent `Get-NetFirewallProfile`).
3. The custom **TCP 7777 rules (inbound + outbound) exist and are enabled**, and
   the **TCP 7779 rule was removed** (prepare had created it — a real transition).
4. **Negative external probe**: a NodePort to guest **7778** (no allow rule) is
   **unreachable** from the controller (wait_for times out) — the firewall blocks
   unallowed inbound traffic.

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner), the
`<host>-sysprep` Secret, and — belt-and-braces — the `winfw-test-fwprobe`
Service verify creates (verify also deletes it in an `always` block). `state:
absent` is idempotent, so re-running destroy is a safe no-op; the `molecule`
namespace ends empty.
