# Headless verification for `test_netboot_pxe` — design

**Date:** 2026-05-09
**Status:** approved (brainstorming complete)
**Scope:** extend `playbooks/kubevirt/test_netboot_pxe/` so each smoke-test VM verifies, without console scraping, that the netbootxyz HTTP server served the *expected* file (per-host pin or main menu) to the VM's leased IP. Today the playbook only confirms the iPXE binary fetched from rb5009 (TFTP hits) — the comment block at the top of `test_netboot_pxe.yml` explicitly defers the iPXE → TrueNAS chainload check because "that needs console scraping." This design closes that gap.

## Goals

- Headlessly assert that a pinned-MAC VM causes the netbootxyz container's TFTP server (dnsmasq) to **send** `/config/menus/host/MAC-<hexraw>.ipxe`, and that a random-MAC VM causes the **same path to be requested but answered with `file ... not found`** — that "not found" is the proof of fall-through to the in-binary main menu in the current iPXE chain.
- Detect three regression classes the current playbook misses: (a) the deployed pin file is stale or wrong, (b) iPXE fetched the boot binary from rb5009 but never reached the netbootxyz chainload step, (c) a stale per-host file is being served for what should be an unpinned MAC.
- Leave behind reusable verification primitives (HTTP fetch a pin file, look up a DHCP lease IP, grep the netbootxyz access log) that a future operator-facing "set/clear default boot" playbook can consume — without designing or building that playbook here.
- Honour the existing block/always cleanup contract: a failed assertion still tears down its VM.
- Stay compatible with both serial and parallel modes already in `test_netboot_pxe.yml`.

## Non-goals

- The operator-facing boot-flip playbook (`set_host_boot.yml` / `clear_host_boot.yml`). Out of scope; deferred to a follow-up brainstorm. The primitives this design produces are the foundation it will sit on.
- Verifying that iPXE *executed* the fragment body (vs. merely fetched it). That still needs serial console scraping or a phone-home probe; both were explicitly rejected during brainstorming. The HTTP-side check covers the regressions the user actually hits.
- Booting real-host pins (helpernode, p330, hpg5, 5847ca77098a, 998877664433, 02:9f:47:58:1b:f7). Their fragments would attempt real OS installs. Test fixtures stay limited to the two smoke pins (`02:00:00:50:58:01`, `02:00:00:50:58:02`).
- Changing the inventory schema. `netboot_host_pins` is read-only from the test's perspective; no `smoke_test:` flag, no parallel test-case list in inventory.
- Touching the iPXE binary build (`playbooks/routeros/deploy_netboot_binaries.yml`) or the asset-management playbook (`playbooks/netboot/deploy_assets.yml`).

## Architecture

Each test case already runs through this skeleton (block/always, with snapshots before/after the VM boot):

```
pre  = TFTP hits on rb5009
apply VM, wait for Ready, pause for boot
post = TFTP hits on rb5009
assert post > pre
always: delete VM
```

The new check adds two more snapshot points and one more assertion to the same skeleton:

```
pre  = TFTP hits on rb5009                                                ┐
pre  = `docker logs` line count for ix-netbootxyz-netbootxyz-1            │
apply VM, wait for Ready                                                  │
vm_ip, vm_mac = lookup rb5009 DHCP lease (via VMI MAC, poll up to 30s)    │
pause for boot wait window                                                │
post = TFTP hits on rb5009                                                │
post = new `docker logs` lines for the container                          ┘
assert TFTP hits incremented (existing)
classify case: vm_mac in netboot_host_pins → expected = "sent"
                                       else → expected = "not_found"
assert exactly one dnsmasq-tftp log line for /config/menus/host/MAC-<hex>.ipxe
  with the expected outcome and the VM's leased IP.
```

The crucial deployment fact (verified by the Task 1 spike + post-Task-3 inspection): the netbootxyz container's nginx serves only `/assets/`. **All chainload — including per-host pins — goes via TFTP**, served by the container's built-in dnsmasq. The current `menu.ipxe` (TFTP-served) chains to `host/MAC-${mac:hexraw}.ipxe` via `chain` over TFTP, falling through to `stock-menu.ipxe` when the per-host file is absent. So when an iPXE binary fetches from rb5009 and chains into netbootxyz, the dispatch sequence visible in dnsmasq-tftp logs is:

- pinned MAC → `dnsmasq-tftp[…]: sent /config/menus/host/MAC-<hex>.ipxe to <ip>` (file existed and was served).
- random MAC → `dnsmasq-tftp[…]: file /config/menus/host/MAC-<hex>.ipxe not found for <ip>` (the existence-of-fall-through proof).

The "sent" vs "not found" outcome IS the discriminator. Both messages contain the full `/config/menus/host/MAC-<hex>.ipxe` path and the requesting IP — easy to filter and assert on.

Preflight (once per playbook run, before any VM is applied) does a separate setup + static check:

1. Build a set of pinned MACs from `netboot_host_pins`.
2. HTTP GET `{{ netbootxyz_self_url }}/` → 200. Catches the netbootxyz container's nginx being down before any VM is wasted.
3. For every pinned MAC referenced by `pxe_test_arches`, **deploy** the pin file from inventory's `netboot_host_pins[…].fragment` to `/config/menus/host/MAC-<hex>.ipxe` on TrueNAS (via `ansible.builtin.copy` to the bind-mount path `/mnt/ssd/containers/netbootxyz/config/menus/host/`). Then `docker exec cat` the deployed file back and assert its body contains the pin-specific substring (default `=== pxe-test smoke pin:`). Catches drift between inventory and what's actually on disk.

The deploy step is normally `playbooks/netboot/deploy_assets.yml`'s job (per `docs/superpowers/specs/2026-05-08-netboot-asset-management-design.md`). Until that playbook is implemented, this preflight inlines the minimum needed for the smoke pins. When `deploy_assets.yml` lands, this preflight may be simplified (or even reduced to a read-back check).

## File layout

```
playbooks/kubevirt/test_netboot_pxe/
  test_netboot_pxe.yml      # extended: include _preflight.yml, log slicing + lease lookups in parallel mode
  _arch_test.yml            # extended: include lease lookup + _verify_tftp.yml inside the per-case block
  _preflight.yml            # NEW: pinned-MAC set, http-probe nginx root, deploy + verify smoke pin files
  _dhcp_lease_lookup.yml    # NEW: poll rb5009 DHCP, set _vm_ip + _vm_mac (used by serial AND parallel)
  _verify_tftp.yml          # NEW: docker-logs slice + per-case dnsmasq-tftp outcome assertion
  vm.yaml.j2                # unchanged
```

`_verify_tftp.yml` and `_dhcp_lease_lookup.yml` are each their own file because both the serial path (`_arch_test.yml`) and the parallel path (the inline block in `test_netboot_pxe.yml`) call the same logic. Splitting them is the only way to avoid a copy-paste fork.

## Test-case schema (`pxe_test_arches`) — no new required fields

The existing entry shape is unchanged:

```yaml
pxe_test_arches:
  - name: pxe-test-bios-pinned
    arch: bios
    binary: netboot.xyz.kpxe
    mac: '02:00:00:50:58:01'
```

Per-case expected behaviour is fully derived at runtime, not declared per entry:

- **Path:** `/menus/host/MAC-<hexraw>.ipxe`, where `<hexraw>` comes from the VM's actual MAC observed in the rb5009 DHCP lease (the test fixture's declared `mac:` if pinned, or KubeVirt's auto-generated MAC if not).
- **Expected status:** 200 if that MAC is in `netboot_host_pins`, otherwise 404.
- **Expected substring (preflight only, pinned MACs):** the playbook ships a `pxe_test_substring_defaults` map keyed by lowercase MAC (default for both smoke pins: `=== pxe-test smoke pin:`). This drives only the preflight static check; per-case substring assertion is dropped because preflight already validated the body.

YAGNI: no escape-hatch override fields. If a non-default case is needed later, add the field then.

## Inventory schema

**No changes.** `netboot_host_pins` is read-only. Test-side concerns stay on the test side.

## Verification primitives

These four operations are the building blocks. Each is implemented as a small reusable include or a `set_fact`-style task. None are coupled to KubeVirt; all are usable by future playbooks (operator-facing boot-flip, ad-hoc audit) without modification.

### 1. `mac_to_pin_path(mac)` — pure jinja, no IO

```jinja
/menus/host/MAC-{{ mac | regex_replace(':', '') | lower }}.ipxe
```

iPXE's `${mac:hexraw}` produces the same form (lowercase, no separators), confirmed against `roles/netbootxyz/templates/disks/netboot.xyz.j2:101` in the upstream netboot.xyz Ansible build (already vetted in `2026-05-08-netboot-asset-management-design.md`).

### 2. `assert_pin_file_served(path, expected_substring=None)`

`ansible.builtin.uri` GET `{{ netbootxyz_self_url }}{{ path }}`:

- `status_code: 200`
- `return_content: true`
- If `expected_substring` non-empty: assert it appears in `result.content`.

Used twice: once during preflight per smoke pin (static check), and once during per-case verification (re-uses the body cached from preflight when path matches; otherwise fetches fresh).

### 3. `lookup_dhcp_lease(vm_name)`

`community.routeros.command`, `delegate_to: rb5009`:

```
/ip dhcp-server lease print detail without-paging
  where comment~"<vm_name>" or
        mac-address="<vm_mac_if_known>"
```

The lookup uses a pinned MAC if `pxe_test_arches` declared one, otherwise it queries by recently-seen MAC matching the VM's CUDN/IP range. In practice, the simplest approach is: read the VirtualMachineInstance's `.spec.domain.devices.interfaces[].macAddress` (KubeVirt fills this in even for auto-generated MACs by the time VMI is Ready), then query rb5009 by that MAC. The output line yields BOTH `address=<ip>` and `mac-address=<mac>` (uppercase from RouterOS; we lowercase it).

Wrapped in `until` retry — up to 30 seconds, 5-second interval — because the iPXE DHCP exchange can lag a few seconds behind VMI Ready. Fails the case if no lease appears with: `VM <name> (MAC <mac>) never received a DHCP lease from rb5009 within 30s. Check OVN bridging on the CUDN.` This message intentionally points at the network layer, not at netbootxyz — the failure mode is independent.

Outputs (set as facts on the caller): `_vm_ip`, `_vm_mac` (lowercase, with colons).

### 4. `read_dnsmasq_tftp_lines_since(pre_line_count)`

`delegate_to: {{ netbootxyz_host }}` (truenas). Two-shot: snapshot the docker-log line count before the case starts, then read everything appended after.

Pre-snapshot:
```
docker logs ix-netbootxyz-netbootxyz-1 2>&1 | wc -l
```

Post-read (returns only lines after the snapshot index, filtered to dnsmasq-tftp):
```
docker logs ix-netbootxyz-netbootxyz-1 2>&1 | tail -n +<pre_count+1> | grep dnsmasq-tftp
```

Each line is then parsed with a regex into `{outcome, path, ip}` where `outcome ∈ {sent, not_found}`. Filtering and assertion happen in `_verify_tftp.yml`.

Sample lines (verified empirically):

```
dnsmasq-tftp[23]: sent /config/menus/host/MAC-020000505801.ipxe to 10.10.9.40
dnsmasq-tftp[23]: file /config/menus/host/MAC-525400deadbe.ipxe not found for 10.10.9.41
```

Regex (single capture per line, two patterns):

```
^dnsmasq-tftp\[\d+\]: sent (\S+) to ([\d.]+)$         → outcome=sent
^dnsmasq-tftp\[\d+\]: file (\S+) not found for ([\d.]+)$  → outcome=not_found
```

Slice-by-line-count (instead of `--since=<seconds>`) survives clock skew and is robust regardless of log driver. Note: nginx access lines do NOT appear in `docker logs` (nginx in this image writes to a file at `/config/log/nginx/access.log`); the only log content reachable via `docker logs` is the dnsmasq-tftp output, which is exactly what we want.

## Per-case assertion logic (`_verify_tftp.yml`)

Inputs (as task vars): `vm_name`, `vm_mac`, `vm_ip`, `expected_outcome` (literal string `"sent"` or `"not_found"`), `pre_log_line_count`. Path is derived inline from `vm_mac`.

Steps:

1. Read docker-log lines appended after `pre_log_line_count`, filtered to `dnsmasq-tftp` (primitive 4 above).
2. Parse each line into `{outcome, path, ip}` via the two regex patterns.
3. Filter to lines where `ip == vm_ip`.
4. Compute `expected_path = '/config/menus/host/MAC-' + (vm_mac | regex_replace(':', '') | lower) + '.ipxe'`.
5. Filter to lines where `path == expected_path`.
6. Assert: `length == 1` and `outcome == expected_outcome`.

Failure messages:

- No dnsmasq-tftp lines at all from `vm_ip` → `VM {{ vm_name }} (IP {{ vm_ip }}) made no TFTP requests to netbootxyz. iPXE never reached the chainload step. Check the rb5009 TFTP server, the chain target inside the iPXE binary, or VLAN reachability between the VM CUDN and {{ netbootxyz_host }}.`
- Lines for `vm_ip` exist but none on the expected path → `VM {{ vm_name }} requested {{ <observed paths> }} instead of {{ expected_path }}. The chain inside the menu.ipxe served by netbootxyz may be wrong.`
- Right path but wrong outcome → `VM {{ vm_name }} hit {{ expected_path }} but dnsmasq returned {{ <observed outcome> }} (expected {{ expected_outcome }}). Pinned-MAC case where outcome=not_found means the deploy step in preflight failed silently or the file was removed; random-MAC case where outcome=sent means a per-host file exists for what should be an unpinned MAC.`

## Orchestration changes

### Serial mode (`_arch_test.yml`)

Per-case flow:

1. Snapshot pre `docker logs` line count (primitive 4 pre-shot).
2. Snapshot pre TFTP hits (existing).
3. Apply VM, wait Ready (existing).
4. Lookup DHCP lease → `_vm_ip` AND `_vm_mac` (primitive 3, augmented to extract both).
5. Pause (existing).
6. Snapshot post TFTP hits (existing).
7. TFTP-hits assert (existing).
8. Compute `_expected_outcome = 'sent' if _vm_mac in _pxe_pinned_macs else 'not_found'`.
9. `include_tasks: _verify_tftp.yml` with `{vm_name, vm_mac, vm_ip, expected_outcome, pre_log_line_count}`.

### Parallel mode (`test_netboot_pxe.yml`)

Same flow but batched: the pre log line count is captured once (shared); each VM's lease lookup yields its own `vm_ip`/`vm_mac`; the verify step is looped per case. Per-VM-IP filtering inside `_verify_tftp.yml` keeps the cases independent inside the shared log slice.

## Idempotency, retries, timing

| Concern | Handling |
|---|---|
| iPXE DHCP can lag VMI Ready by a few seconds | DHCP lease lookup retries for 30s, 5s interval. |
| Clock skew between test runner and TrueNAS | Slice-by-line-count (`wc -l` pre, `tail -n +<N>` post) — no timestamp arithmetic. |
| Log rotation between snapshots | nginx access log inside `ix-netbootxyz-netbootxyz-1` rotates on size, not by minute, at homelab volume. The slice-by-line-count window is bounded by one `pause` (≤180s default) — rotation inside that window is extremely unlikely. Flagged, not designed-around. |
| Parallel mode: log lines from N VMs interleave | Per-VM-IP filter inside the slice handles it. Two VMs sharing a dynamic IP would alias — impossible in practice with rb5009's DHCP pool. Flagged. |
| Re-running the playbook | Preflight is read-only. Per-case work is unchanged in idempotency: VM applied → asserted → deleted. No state persists between runs. |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| netbootxyz container name or log layout differs from what this design assumes | Captured by Task 1 spike + post-Task-3 inspection. Container `ix-netbootxyz-netbootxyz-1`, dnsmasq-tftp lines visible in `docker logs`, runtime `docker`. |
| Clock skew | Line-count slicing, no timestamps. |
| Log rotation drops lines mid-test | Bounded by ≤180s pause; `docker logs` retains tens of thousands of lines on default log driver; flagged in code comment. |
| dnsmasq-tftp message format changes between netbootxyz image versions | Two stable formats are matched by regex (`sent ... to <ip>` and `file ... not found for <ip>`). Image is pinned in the TrueCharts deployment; format change would surface as zero-match assertion failures with diagnostic line content. |
| Smoke pin file deployed but mode/owner is wrong → dnsmasq refuses to serve | Preflight `docker exec cat` reads the file back and asserts substring; if dnsmasq can't read it the read-back also fails or returns empty. |
| Smoke pin fragment in inventory drifts from what's deployed | Preflight read-back substring check catches drift. |
| iPXE retains the IP into a kernel that DHCPs again later (would generate extra log lines) | Smoke VMs are diskless; iPXE never hands off to a kernel. The pin fragments `exit 0` after the echo. Not a concern for the smoke cases. |
| Test runner can't reach `netbootxyz_self_url` (e.g. wrong VLAN) | Preflight HTTP probe of `/` fails fast with the relevant URL in the error. |
| `docker exec`/`docker logs` requires elevated privileges on truenas | The truenas connection is already root for other playbooks (`playbooks/truenas/*`); verified at plan time. |
| Smoke pin files deployed by this preflight collide with `playbooks/netboot/deploy_assets.yml` | `deploy_assets.yml` doesn't yet exist. When it lands, this preflight's deploy step is reviewed: either reduced to a read-back check, or kept (idempotent overwrite) — to be decided when that plan executes. |

## Testing strategy

Manual ladder, run from the existing AWX EE / ansible-navigator container:

1. Run with current default `pxe_test_arches`. Confirm: 4 cases pass — preflight deploys 2 smoke pins; 2 pinned-MAC cases see `dnsmasq-tftp ... sent .../host/MAC-…` for their IP, 2 random-MAC cases see `dnsmasq-tftp ... file .../host/MAC-… not found for ...` for their IP.
2. Negative: temporarily delete `/mnt/ssd/containers/netbootxyz/config/menus/host/MAC-020000505801.ipxe` and edit the inventory fragment so preflight redeploys with a different substring. Re-run. Expect: preflight read-back substring check fails fast before any VM boots.
3. Negative: temporarily corrupt the smoke pin fragment body in inventory (different substring). Re-run smoke test. Expect: preflight redeploys + read-back substring assertion fails before any VM boots.
4. Negative: stop the netbootxyz container. Re-run smoke test. Expect: preflight fails on the HTTP `/` probe.
5. Re-run twice in succession with no changes — both runs report identical pass/fail. Pin file deployment is idempotent.
6. `ansible-lint --profile=production` and `yamllint` clean before commit.

No molecule scenario (consistent with the rest of `playbooks/kubevirt/`).

## Open items resolved during brainstorming

- Cases verified headlessly: pin-fetch (per-host short-circuit, expect dnsmasq `sent` for the per-host path), menu-fetch (unpinned fall-through, expect dnsmasq `not found` for the per-host path — the not-found IS the proof of fall-through to `stock-menu.ipxe`). NOT pin-fragment-executed (rejected as "needs serial console scraping or phone-home — out of scope here").
- Verification mechanism: HTTP probe of nginx root at preflight (liveness only) + ansible.builtin.copy of smoke pin files into the container's `/config/menus/host/` mount + `docker exec cat` read-back substring check + `docker logs` slice for dnsmasq-tftp lines (catches dynamic dispatch breakage). No virtctl console; no probe URL.
- Test fixture location: `pxe_test_arches` stays in the playbook (test concern). Inventory `netboot_host_pins` is read-only from the test's perspective; the smoke pin file BODY comes from inventory's `netboot_host_pins[…].fragment` field — the test playbook copies that text into the container's menus directory.
- Inventory schema: unchanged. No `smoke_test:` flag added.
- Real-host pins (helpernode/p330/hpg5/etc.): NOT booted by the test. Only the two `02:00:00:50:58:0*` smoke pins.
- Per-case discriminator: dnsmasq-tftp **outcome** (`sent` vs `not found`) on the per-host path. Single observable, two interpretations. Replaces the original "menu.ipxe vs host/MAC-* path" framing — the actual deployment serves nothing through HTTP except `/assets/`; all chainload is TFTP.
- Substring check on deployed pin body: preflight only (test-side default substring map keyed by MAC). Per-case substring assertion dropped because preflight already validates the body via `docker exec cat` after deploy.
- Boot-flip helper playbook: out of scope; deferred to a follow-up brainstorm. The four primitives this design produces are the foundation it will reuse.
- IP **and MAC** source for log correlation: rb5009 DHCP lease table — query yields both the IP and the MAC (so KubeVirt-generated random MACs are observable). MAC is also read from the VirtualMachineInstance for the input to the rb5009 query.
- Container / log layout: confirmed empirically. Container `ix-netbootxyz-netbootxyz-1` (TrueCharts naming on TrueNAS SCALE), runtime `docker`, dnsmasq-tftp logs visible in `docker logs` (nginx access logs are NOT — they go to a file, but we don't need them in this pivoted design). Per-host pin files live at `/config/menus/host/MAC-<hex>.ipxe` inside the container; the bind mount on TrueNAS is at `/mnt/ssd/containers/netbootxyz/config/menus/host/`.
- Pivot note: an earlier iteration of this spec assumed an HTTP-served `/menus/` layout (per `docs/superpowers/specs/2026-05-08-netboot-asset-management-design.md`). That asset-management plan exists but is not yet implemented; the current deployment uses TFTP-only chaining. The smoke test deploys its own pin files into the live deployment until `deploy_assets.yml` lands.
