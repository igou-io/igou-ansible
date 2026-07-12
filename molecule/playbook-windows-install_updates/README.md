# playbook-windows-install_updates

Live, on-cluster scenario that exercises
`playbooks/windows/install_updates.yaml` against a real **Windows Server 2025**
VM with a **bounded** update pass. It provisions one `win2k25` golden clone via
`david_igou.molecule_provisioners` (connection `psrp`), installs the
`Definition Updates` category with reboots off, then proves the playbook behaved
with an independent `win_updates state=searched` probe.

Everything happens inside the **`molecule`** namespace on
`https://api.ocp.igou.systems:6443` as the scoped **`ocp-ansible-molecule`**
ServiceAccount. Copied from the PILOT `playbook-windows-manage_services`; see
that scenario's README for the shared-plumbing architecture.

## Why a bounded pass

An open-ended `win_updates` install is inherently non-idempotent — Microsoft can
publish new updates between any two runs. The scenario pins the category to
`Definition Updates` (Defender signature updates): small, fast, and reboot-free,
so a full install completes inside the test window. Reboots are disabled so the
VM never surprise-restarts mid-test.

## Prerequisites

- The live `ocp.igou.systems` cluster reachable, with the
  `ocp-ansible-molecule` SA token + API host exported, the SA's cross-namespace
  CDI clone grant for `win2k25`, and a `win2k25` golden DataSource present.
- The VM must have **outbound internet** (or a WSUS/feed reachable) so Windows
  Update can search the definition feed. In an air-gapped run
  `found_update_count` will be 0 — still a clean pass, but the feed race below
  cannot occur.
- **`pypsrp`** on the controller/EE (runtime lib for the `psrp` connection).
- `ansible.windows` (installed by the galaxy dependency step from
  `collections.yml`).

## How to run

```bash
unset KUBECONFIG
export K8S_AUTH_HOST=https://api.ocp.igou.systems:6443
export K8S_AUTH_API_KEY=$(op read "op://lab_serviceaccounts/ocp-ansible-molecule/token")
export MOLECULE_WINDOWS_ADMIN_PASSWORD=$(op read "op://lab_agents/windows-administrator/password")

molecule test -s playbook-windows-install_updates
```

## What verify proves

1. A `win_updates state=searched` re-search over the same `Definition Updates`
   category comes back with **`found_update_count == 0`** — the substantive
   proof that converge installed the category to completion (a re-search after a
   full install must find nothing applicable). `failed_update_count == 0` too.
   The search is wrapped in `until` retries (3 attempts, ~60 s apart) so a
   momentary feed publish between converge and verify does not flake the assert.
2. `C:\molecule-specialize-done.txt` exists — the sysprep unattend
   FirstLogonCommands ran, so the whole specialize/WinRM path executed.

## No idempotence step (by design)

The `idempotence` step is deliberately **absent** from `test_sequence`.
`Definition Updates` is an UNPINNED external feed: Microsoft can publish a new
Defender signature between any two converge passes, so a second converge can
legitimately install it and report `changed` with zero playbook defect. There
is no scenario-side way to exempt a task inside an *imported, unmodified*
playbook from molecule's idempotence grep, and bounded idempotency for updates
is the **accept_list contract's** job, not this scenario's. Verify substitutes a
stronger check: it re-searches the category and asserts converge left it clean
(`found_update_count == 0`).

## KB4052623 pin (Phase-5 finding: chained applicability, not churn)

The `Definition Updates` category also carries **KB4052623** — the Defender
**antimalware platform** update (a self-servicing component, not a signature).
Its applicability **chains** on the definitions converge installs: Windows
Update only offers it in searches run *after* the fresh definitions are
installed. Reproduced deterministically on two clean golden clones (converge
found=2/installed=2 both times; every post-converge re-search found exactly
KB4052623, `Current Channel (Broad)`). A single reboot-free pass can therefore
never both see and install it — chained updates are what the playbook's
`windows_updates_reboot=true` re-check loop is for, and this scenario keeps
reboots off by design. Fix: converge passes
`windows_updates_reject_list: [KB4052623]` (a documented playbook var) and
verify's re-search mirrors the **exact** same `reject_list`, so rejected
matches land in `filtered_updates` and the `found_update_count == 0` contract
holds. Keep the two filters in sync.

## Watch item (feed to Phase 5 — churn rerun signature)

- **Definition-feed churn signature.** If verify's re-search still reports a
  nonzero `found_update_count` after all `until` retries, the external feed
  published a fresh definition after converge finished — a real-world race, not
  a playbook defect. The failure signature is the re-search assert failing with
  a small nonzero `found_update_count` for a just-published Defender definition;
  the Phase-5 validator should **re-run** the scenario. (A *repeating* KB across
  reruns is NOT churn — see the KB4052623 pin above.)

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner) and the
`<host>-sysprep` Secret. `state: absent` is idempotent, so re-running destroy —
or destroying after a failed create — is a safe no-op. The `molecule` namespace
ends empty of scenario resources.
