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

1. An independent `win_updates state=searched` pass over the same
   `Definition Updates` category returns cleanly: it did not crash,
   `found_update_count` is an exposed integer `>= 0`, and nothing failed.
   `found_update_count == 0` is a valid PASS — it means the bounded converge
   already installed everything applicable.
2. The `reboot_required` flag is **exposed** (regardless of value). The contract
   is that a pending reboot is surfaced so the playbook can warn on it — not
   that no reboot is ever pending.
3. `C:\molecule-specialize-done.txt` exists — the sysprep unattend
   FirstLogonCommands ran, so the whole specialize/WinRM path executed.

## Watch items (feed to Phase 5 — not pre-solved)

- **Definition-feed idempotence race.** The `idempotence` step runs converge a
  second time and fails if any task reports `changed`. `Definition Updates` is
  an EXTERNAL feed: Microsoft can publish a new Defender signature between the
  two converge passes, so the second pass legitimately installs it and reports
  `changed` — a real-world flake, not a playbook defect. There is **no
  scenario-side mechanism** to exempt a task inside an *imported, unmodified*
  playbook from molecule's idempotence grep (that would require editing the
  playbook, which is out of scope here). If `idempotence` fails and the changed
  task is the `win_updates` "Search for and install updates" task with a newly
  published definition, the Phase-5 validator should **re-run** the scenario;
  the failure signature is a single `changed` on the update task with a nonzero
  `installed_update_count` for a fresh KB/definition.

## Cleanup semantics

`destroy` removes the VM + its NodePort Service (provisioner) and the
`<host>-sysprep` Secret. `state: absent` is idempotent, so re-running destroy —
or destroying after a failed create — is a safe no-op. The `molecule` namespace
ends empty of scenario resources.
