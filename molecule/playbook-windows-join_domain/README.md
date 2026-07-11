# playbook-windows-join_domain

The **hardest** Windows molecule scenario: two live Windows Server 2025 VMs — an
ephemeral Active Directory **domain controller** and a **member** — used to
exercise `playbooks/windows/join_domain.yaml` end to end: **join** a real domain
and **leave** back to a workgroup, each proved with independent in-guest
evidence.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount.

## Topology

| Host | Group(s) | Instance type | Role |
|---|---|---|---|
| `windc01` | `molecule` | `u1.large` (2 vCPU / 8Gi) | AD DC for `molecule.test`, promoted by `prepare.yml` (**test setup**, not the playbook under test) |
| `winmem01` | `molecule`, `windows` | `u1.medium` (1 vCPU / 4Gi) | Domain member — the **only** host the playbook targets |

The real playbook targets `{{ ansible_limit | default('windows') }}`. Only
`winmem01` is in the `windows` group, so converge joins **only** the member and
leaves the DC untouched by the playbook under test. `windc01` gets the larger
instance because promoting a brand-new forest (dcpromo) is the RAM-heaviest step
in the scenario.

## What each stage does

- **create** — preflight (auth + admin-password env) then render a `<host>-sysprep`
  Secret for **both** VMs (shared play loops `groups['molecule']`), then provision.
- **prepare** — wait for `psrp` on both VMs, then promote `windc01`:
  `win_feature: AD-Domain-Services` → `microsoft.ad.domain` (new forest
  `molecule.test`, NetBIOS `MOLECULE`, install DNS, module-managed reboot) →
  block until in-guest `Get-ADDomain` answers **and** the DC's own DNS serves the
  `_ldap._tcp.dc._msdcs.molecule.test` SRV locator. Budget ~10-15 min.
- **converge** — assert `windows` group non-empty; discover `windc01`'s pod IP
  from its Running **virt-launcher Pod** `status.podIP` (not the VMI's
  `status.interfaces[0].ipAddress`, which under masquerade reports the useless
  in-VM 10.0.2.x address); import `join_domain.yaml` unmodified with
  `windows_domain_state: domain`, `windows_domain_name: molecule.test`,
  `windows_domain_admin_user: MOLECULE\Administrator`,
  `windows_domain_dns_servers: [<DC pod IP>]`. The playbook points the member's
  DNS at the DC, then `microsoft.ad.membership` joins (module-managed reboot).
- **idempotence** — a second converge is `changed=0`: DNS already set, member
  already joined, and the pod-IP lookup uses only `k8s_info` + `set_fact`.
- **verify** — independent join proof (`Win32_ComputerSystem.PartOfDomain` +
  `nltest /dsgetdc`), then the **leave leg** (re-import the playbook with
  `windows_domain_state: workgroup`), then assert `PartOfDomain` is false.
- **destroy** — provisioner tears down both VMs + services; sysprep Secrets
  deleted. Namespace ends empty.

## Prerequisites

- The live `ocp.igou.systems` cluster reachable, with:
  - the `ocp-ansible-molecule` SA token and API host exported (see below),
  - the SA's cross-namespace CDI clone grant for `win2k25` in
    `openshift-virtualization-os-images`,
  - a `win2k25` golden PVC/DataSource present there.
- **`pypsrp`** on the controller/EE (runtime lib for the `psrp` connection).
- `ansible.windows` + `microsoft.ad` + `kubernetes.core` (installed by the
  galaxy dependency step from `collections.yml`).

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s playbook-windows-join_domain
```

Step-wise (recommended — promotion is slow):

```bash
molecule create   -s playbook-windows-join_domain   # 2 sysprep Secrets + 2 VMs
molecule prepare  -s playbook-windows-join_domain   # wait for psrp (both) + promote DC
molecule converge -s playbook-windows-join_domain   # join winmem01
molecule verify   -s playbook-windows-join_domain   # join proof + leave leg
molecule destroy  -s playbook-windows-join_domain   # cleanup (ns ends empty)
```

## Expected duration

Two smart-clones + two OOBE specializes run in parallel (~5-15 min to psrp),
then DC promotion + reboot (~10-15 min), then the member join + reboot and the
leave + reboot (~a few min each). **Budget ~35-50 min end to end** — the longest
of the Windows scenarios.

## The local-Administrator connection invariant

`winmem01` is reached as the **local Administrator** for the whole lifecycle —
including across the leave-path reboot in `verify.yml`. `microsoft.ad.membership`
requires a local reconnect account after an unjoin, so the scenario **never**
switches the connection to a domain/kerberos account. This is a review-flagged
requirement; keep it if you edit the scenario.

The connection user is the **bare** `Administrator` (from the provisioner's
runtime inventory), not `.\Administrator`. Post-join this is unambiguous because
the local Administrator and any same-named domain account share the one password
identity the psrp session already holds, so NTLM resolves it locally. This is
**intentional**. If it ever flakes after the join (e.g. the name resolving to a
domain principal you did not intend), qualify it as `.\Administrator` to force
the local SAM account — but first confirm the sysprep unattend/autologon does not
consume `admin_user` in a way a `.\ ` prefix would break (the shared template
feeds `windows_admin_user` into `LocalAccount`/`AutoLogon` where a `.\ ` prefix
is **not** valid), so the prefix belongs only on the runtime *connection* var,
never on the unattend account name.

## Phase-5 debug pointers

- **DNS is the classic join blocker.** If the member can't find the domain, the
  DC pod IP was wrong or `win_dns_client` didn't stick. Check the converge
  "windc01 pod IP" assert output and, in-guest on `winmem01`,
  `Resolve-DnsName molecule.test`.
- **DC promotion.** On `windc01`: `Get-ADDomain`, `dcdiag /v`,
  `Get-Service ADWS,NTDS,DNS`, and `Get-DnsServerResourceRecord -ZoneName
  _msdcs.molecule.test`. Promotion/DCPromo logs live at
  `C:\Windows\debug\dcpromo.log` and `C:\Windows\debug\dcpromoui.log`.
- **Promotion timing.** The `Get-ADDomain` / SRV `until` loops in `prepare.yml`
  give ~10 min after the reboot; if they exhaust, the DC is genuinely slow —
  raise `retries`, don't paper over a real failure.
- **VM-to-VM traffic** uses pod IPs (masquerade). If the join times out reaching
  the DC, confirm both VMIs are `Running` and their pod IPs are reachable
  in-cluster (AD ports are opened by the AD DS role install on the DC).
- **NIC profile.** Clone NICs classify as `Public`; the sysprep unattend opens
  WinRM 5986 on all profiles, and AD DS opens its own ports on install.
