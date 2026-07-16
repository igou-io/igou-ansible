# Troubleshooting

Symptom-keyed cross-cutting issues. For component-specific runbooks see
the topic docs. Each entry: symptom → quick diagnostic → likely cause(s)
→ fix.

## PXE / netboot

### A VM or host PXE-boots into the fallback menu instead of its custom pin

```bash
# Check whether rb5009's /ip tftp row for the MAC saw a hit
SSH_AUTH_SOCK= ssh -i ~/.ssh/id_ed25519 igou@rb5009.igou.systems -p 3480 \
  "/ip tftp print detail without-paging where req-filename=\"MAC-<hex>.ipxe\""
```

- No matching row → the MAC isn't in `netboot_host_pins`, or `deploy_assets.yml`
  hasn't run since the inventory edit. Fix:
  ```bash
  ansible-playbook playbooks/netboot/deploy_assets.yml \
    -i 'localhost ansible_connection=local,' \
    -i igou-inventory/inventory.yaml --tags render,push,verify
  ```
- Row exists but `hits=N` didn't increment after a real boot → the binary's
  `:tftpmenu` chain isn't reaching rb5009. Check that the new iPXE binaries
  are deployed (`/file print where name~"netboot/netboot.xyz"`) and that
  DHCP next-server points at rb5009 (`/ip dhcp-server network print`).
- Row exists and hits++ but boot drops to fallback menu → the pin's
  fragment runs to completion (no `boot` line, or `boot` failed). Read the
  pin body:
  ```
  /file print value-list where name="netboot/per-host/MAC-<hex>.ipxe"
  ```

### A VM/host doesn't PXE-boot at all (no logs anywhere)

- DHCP not reaching it: check the rb5009 DHCP lease table (`/ip dhcp-server
  lease print`) — if no lease entry exists, the VM isn't sending DHCP
  discovers (NIC issue) or rb5009 isn't on the same VLAN as the client.
- DHCP working but no TFTP attempt:
  ```bash
  ansible rb5009.igou.systems -m ansible.builtin.command \
    -a '/ip dhcp-server matcher print' -i igou-inventory/inventory.yaml
  ```
  Confirms the option-93 → boot-file matcher table is intact. If empty,
  re-run `playbooks/routeros/deploy_netboot_binaries.yml --tags dhcp,verify`.

### Worker boots OCP add-node ISO but cluster never sees it

```bash
# From the cluster:
oc get csr
oc adm wait-for ... # or oc get nodes -w
```

- Stuck CSRs → approve them (see openshift-operations.md).
- No CSRs at all after 5 min → worker can't reach the cluster API. Check
  worker IP, gateway, DNS.
- `oc adm node-image monitor --ip-addresses <ip>` (the `--tags monitor`
  mode of `add_node_iso.yml`) shows the bootstrap progress, but skips
  CSR-readiness if reverse DNS isn't available — that's documented and
  fine.

### Smoke test fails on `/ip tftp` hit-counter assertion

`test_netboot_pxe.yml` asserts:
- Pinned MAC: row exists AND `hits` incremented from pre-snapshot to post-snapshot.
- Random MAC: no row exists for the auto-generated MAC.

Common failures:
- Pinned: `hits` unchanged → VM never reached rb5009 with the pin lookup.
  Check rb5009 `/log print where topics~"dhcp"` for the VM's MAC; if no
  DHCP offer, check the matcher table (`/ip dhcp-server matcher print`).
- Random: row unexpectedly exists for the random MAC → some prior pin in
  inventory matches the auto-generated MAC. (`pxe_test_pinned_macs` should
  be the only set with rows in `flash:/netboot/per-host/`.) Check
  `/file print where name~"^netboot/per-host/MAC-"` and reconcile with
  inventory.

## DHCP / RouterOS

### Locked out of rb5009

- SSH from the same VLAN (RouterOS's local management default).
- Winbox via MAC address (not IP) — works even with broken IP config.
- Last resort: `/system reset-configuration no-defaults=yes`. Has to be
  followed by full restore from `playbooks/routeros/backup.yml` artifact.

### `community.routeros.command` returns "auth failed" or hangs

- Connection vars in `igou-inventory/group_vars/routeros.yml`: user is
  `igou+cet1024w` (the `+cet1024w` suffix hints to RouterOS the client
  supports specific terminal sizes / encodings — required for the API).
- The connection uses a non-default SSH port set in `group_vars/routeros.yml::ansible_port` — not the default 22 and not the RouterOS API default 8728.
- If the command hangs: the device may have an active interactive session
  blocking new ones. Close Winbox, retry.

### `community.routeros.api` instead of `command` — when?

`api` uses RouterOS API protocol; `command` runs SSH-shell-equivalent
commands. Most playbooks here use `command` because it's easier to compose
and the API's CRUD semantics are awkward for ad-hoc commands. Don't switch
without a reason.

## TrueNAS / containers

### Container running but service unreachable

```bash
ssh truenas 'docker ps --filter name=<svc> --format "{{.Names}}\t{{.Status}}\t{{.Ports}}"'
ssh truenas 'docker inspect <name> | jq ".[0].NetworkSettings.Networks"'
```

- Container in a Docker network that the LAN can't reach → check
  `truenas_docker_networks` definition; the network needs to be `host` mode
  or have explicit ports published.
- Health check failing → `docker logs <name>` for application errors.
- Bind-mount path mismatch → inspect that the dataset path matches what
  the compose file expects (typical: `/mnt/ssd/containers/<svc>/config`
  vs `/config` inside the container).

### `configure_docker_containers.yml` fails on `docker compose up`

- Compose template syntax error → render with `--check --diff` to see the
  output before applying.
- Image pull denied → manually `docker login <registry>` on truenas; the
  playbook doesn't manage registry creds.
- Port conflict → another container already on that port; check `docker ps`.

### `configure_users.yml` errors on `arensb.truenas.user`

- Deprecated parameter → the role's API surface changed in TrueNAS 24.04.
  Check the role's `defaults/main.yml` for the current parameter set.
- "user already exists" → the role is idempotent on adds but not deletes.
  Remove via the UI, then re-run.

## Secrets / 1Password

### `OP_SERVICE_ACCOUNT_TOKEN` not set

```bash
op read "op://awx/onepassword-sdk-claude-container-token/credential" \
  | head -1
```

- If `op` says "no token" → `op signin` first, or use a service account
  token directly: `export OP_SERVICE_ACCOUNT_TOKEN=ops_...`.
- The token rotates; if it's expired, generate a new one in the 1Password
  UI under Service Accounts.

### `community.general.onepassword` lookup hangs

- The `op` CLI is doing biometric / password prompt. For headless contexts
  always use a service account token (`OP_SERVICE_ACCOUNT_TOKEN` env).
- Vault name typo → `vault='awx'` is the canonical homelab vault. If the
  lookup says "item not found," double-check the field name (`credential`
  vs `password` — depends on the item type).

### Token appears empty after lookup

The `community.general.onepassword` lookup returns a string, not a list.
Common YAML quoting bug: `contents: "{{ ... }}"` works; `contents: '{{
... }}'` (with no field accessor and using single-quotes) returns the
literal Jinja expression because Ansible parsed the YAML wrong.

## CI / lint

### `ansible-lint` fails on `name[template]`

Task names with `{{ }}` templates not at the end of the string. Move the
template to the trailing position:
```yaml
- name: "Apply VM"  # bad if you wanted to include {{ vm_name }}
- name: "Apply VM ({{ vm_name }})"  # good
```

### `yamllint` "line too long" on legacy iPXE fragments

Pre-existing in inventory `netboot_host_pins` — the p330/hpg5 fragments
have long URLs. Out of scope to fix; ignored project-wide.

### GitHub `syntax-check` workflow always failing

Pre-existing. Workflow runs `ansible-playbook --syntax-check` against every
`.yml` under `playbooks/`, which fails on task-include files (`_*.yml`,
`tasks/*.yml`). One-line fix outstanding:
```yaml
# .github/workflows/syntax-check.yml — find one-liner change:
for playbook in $(find playbooks -name '*.yml' -o -name '*.yaml' \
  | grep -vE '/(_|tasks/)' | sort); do
```

`--admin` flag on `gh pr merge` bypasses this check until it's fixed.

## Playbook execution

### `target_cluster | default('all')` runs against every host

This guard exists in `add_node_iso.yml` and `agent-install/deploy_pxe_assets.yml`
to keep ansible-lint happy at parse time. The pre_tasks fail-fast if
`target_cluster` is undefined. **Always pass `-e target_cluster=<name>`**.

### Playbook hangs on `Wait for VM Ready`

KubeVirt/OCP issue, not the playbook. Check the VMI status:
```bash
oc get vmi -n netboot-pxe-test
oc describe vmi <name> -n netboot-pxe-test
```

Common: virt-launcher pod stuck in `ContainerCreating` because the VM
image isn't pullable or the CUDN isn't synced into the cluster.

### Idempotent playbook reports `changed=N` on every run

Some playbooks are intentionally not idempotent:
- `add_node_iso.yml` — `oc adm node-image create --pxe` always re-bakes
  the artifacts.
- `deploy_pxe_assets.yml` — wipes the work-dir at the top.
- `deploy_assets.yml` render stage — same.

For real idempotency check, run `--tags push,verify` on `deploy_assets.yml`
after a full run; should report `changed=0`.

## Network

### Worker can't reach `https://public.igou.systems/boot-files/` from its boot environment

The public nginx serves both HTTP (`:80`) and HTTPS (`:443`) on `10.10.45.241`
(macvlan vlan45). If a worker is on a VLAN where firewall rules block those
ports, iPXE's HTTPS fallback chain (or the kernel-stage rootfs fetch) fails.
Check rb5009 firewall: `/ip firewall filter print` and look for
src-address-list entries blocking `10.10.45.241`.

If the cert validation fails (iPXE error chain ending in "certificate not
trusted"), check the cert subject + issuer on truenas:
```bash
sudo openssl x509 -in /etc/certificates/_public_igou_systems.crt -noout -issuer -subject
```
A Let's Encrypt R3+ chain is in iPXE's built-in CA bundle. Self-signed or
private-CA certs would need to be embedded into the iPXE build — fall back
to HTTP by setting `netboot_public_scheme: http` in inventory and re-running
`deploy_assets --tags push` (no iPXE rebuild needed; the binary tries
HTTPS-then-HTTP automatically and the change only affects rendered scripts).

### A VLAN's CUDN isn't synced into the cluster

```bash
oc get clusteruserdefinednetworks.k8s.ovn.org
```

If the expected CUDN (e.g. `vlan9-no-ipam`) isn't there, the test playbook's
preflight catches this:
> ClusterUserDefinedNetwork vlan9-no-ipam is not present in the current cluster.

Sync via the GitOps tree at `clusters/ocp/udn/` in `igou-openshift`.

## Cross-references

- netboot deep-dive → [`netboot-operations.md`](netboot-operations.md)
- TrueNAS specifics → [`truenas-operations.md`](truenas-operations.md)
- OpenShift specifics → [`openshift-operations.md`](openshift-operations.md)
- DR procedures → [`disaster-recovery.md`](disaster-recovery.md)
