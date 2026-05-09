# Headless verification for `test_netboot_pxe` — design

**Date:** 2026-05-09
**Status:** approved (brainstorming complete)
**Scope:** extend `playbooks/kubevirt/test_netboot_pxe/` so each smoke-test VM verifies, without console scraping, that the netbootxyz HTTP server served the *expected* file (per-host pin or main menu) to the VM's leased IP. Today the playbook only confirms the iPXE binary fetched from rb5009 (TFTP hits) — the comment block at the top of `test_netboot_pxe.yml` explicitly defers the iPXE → TrueNAS chainload check because "that needs console scraping." This design closes that gap.

## Goals

- Headlessly assert that a pinned-MAC VM causes netbootxyz to serve `/menus/host/MAC-<hexraw>.ipxe` with HTTP 200, and that a random-MAC VM causes the same path to be requested but answered with HTTP 404 (which is the proof-of-fall-through to the in-binary main menu).
- Detect three regression classes the current playbook misses: (a) the deployed pin file is stale or wrong, (b) iPXE fetched the binary but never chained to netbootxyz, (c) a stale per-host file is being served for what should be an unpinned MAC.
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
pre  = nginx access log line count (snapshot for slice-after diff)        │
apply VM, wait for Ready                                                  │
vm_ip, vm_mac = lookup rb5009 DHCP lease by MAC (poll up to 30s)          │
pause for boot wait window                                                │
post = TFTP hits on rb5009                                                │
post = new nginx access log lines from netbootxyz                         ┘
assert TFTP hits incremented (existing)
classify case: vm_mac in netboot_host_pins → expected_status = 200
                                       else → expected_status = 404
assert exactly one nginx access line shows
  GET /menus/host/MAC-<vm_mac_hexraw>.ipxe  status=<expected_status>  src=vm_ip
```

The crucial deployment fact (verified by the Task 1 spike): netbootxyz's `menu.ipxe` is served via **TFTP** (port 69, by the container's built-in dnsmasq), not HTTP. The HTTP root only hosts `/assets/` and `/menus/`. So when iPXE fetches the boot binary from rb5009 and chains into menu.ipxe via TFTP, the only HTTP request a smoke VM makes is the per-host `chain http://<self>/menus/host/MAC-<hexraw>.ipxe`. That single request is what we assert on:

- pinned MAC → file exists → nginx returns 200 → iPXE follows the pin fragment.
- random MAC → file does not exist → nginx returns 404 → iPXE falls through to the in-binary `:main_menu` (no further HTTP request).

The status code IS the discriminator; we don't need a separate "did anything else get fetched" assertion.

Preflight (once per playbook run, before any VM is applied) does a separate static check:

1. Build a set of pinned MACs from `netboot_host_pins`.
2. HTTP GET `{{ netbootxyz_self_url }}/` → 200. Catches netbootxyz HTTP server being down before any VM is wasted. (`/menu.ipxe` is NOT HTTP-served — see the deployment-fact note above.)
3. For every pinned MAC referenced by `pxe_test_arches`, HTTP GET `{{ netbootxyz_self_url }}/menus/host/MAC-<hexraw>.ipxe` → 200 + body contains the pin-specific substring (default `=== pxe-test smoke pin:`). Catches `deploy_assets.yml` being stale, separately from the per-VM dynamic check.

## File layout

```
playbooks/kubevirt/test_netboot_pxe/
  test_netboot_pxe.yml      # extended: include _preflight.yml, lease lookups + log slicing in parallel mode
  _arch_test.yml            # extended: include lease lookup + _verify_http.yml inside the per-case block
  _preflight.yml            # NEW: build pinned-MAC set, http-probe netbootxyz root, pin-content substring check
  _dhcp_lease_lookup.yml    # NEW: poll rb5009 DHCP, set _vm_ip + _vm_mac (used by serial AND parallel)
  _verify_http.yml          # NEW: nginx access log slice + per-case status assertion
  vm.yaml.j2                # unchanged
```

`_verify_http.yml` and `_dhcp_lease_lookup.yml` are each their own file because both the serial path (`_arch_test.yml`) and the parallel path (the inline block in `test_netboot_pxe.yml`) call the same logic. Splitting them is the only way to avoid a copy-paste fork.

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

### 4. `read_nbxyz_access_lines_since(pre_line_count)`

`delegate_to: {{ netbootxyz_host }}` (truenas). Two-shot: snapshot the access-log line count before the case starts, then read everything appended after.

Pre-snapshot:
```
docker exec ix-netbootxyz-netbootxyz-1 wc -l /config/log/nginx/access.log
```

Post-read (returns only lines after the snapshot index):
```
docker exec ix-netbootxyz-netbootxyz-1 sh -c 'tail -n +<pre_count+1> /config/log/nginx/access.log'
```

Each line is then parsed with a regex into `{src_ip, method, path, status}`. Filtering and assertion happens in `_verify_http.yml`.

Spike findings (recorded as a header comment in `_verify_http.yml`):

```
runtime         = docker (TrueNAS SCALE uses Docker, not podman)
container_name  = ix-netbootxyz-netbootxyz-1   (TrueCharts naming)
access_log_path = /config/log/nginx/access.log (inside the container; nginx
                  is configured to write to a file, not stdout — `docker logs`
                  only carries the dnsmasq-tftp output)
read_strategy   = wc -l snapshot before, tail -n +<N> after
```

Slice-by-line-count (instead of `--since=<seconds>`) survives clock skew and is robust regardless of log driver.

## Per-case assertion logic (`_verify_http.yml`)

Inputs (as task vars): `vm_name`, `vm_mac`, `vm_ip`, `expected_status`, `pre_log_line_count`. Path is derived inline from `vm_mac`.

Steps:

1. Read access lines appended after `pre_log_line_count` (primitive 4 above).
2. Parse each line into `{src_ip, method, path, status}` via regex.
3. Filter to lines where `src_ip == vm_ip`.
4. Compute `expected_path = '/menus/host/MAC-' + (vm_mac | regex_replace(':', '') | lower) + '.ipxe'`.
5. Assert exactly one filtered line has `method == 'GET'`, `path == expected_path`, `status | int == expected_status | int`.

Failure messages:

- No line at all from `vm_ip` → `VM {{ vm_name }} (IP {{ vm_ip }}) made no HTTP requests to netbootxyz. iPXE never reached the chainload step. Check the rb5009 TFTP server, the chain target inside the iPXE binary, or VLAN reachability between the VM CUDN and {{ netbootxyz_host }}.`
- Line for `vm_ip` exists but on a different path → `VM {{ vm_name }} fetched {{ <observed path> }} instead of {{ expected_path }}. The chain inside the menu.ipxe served by netbootxyz may be wrong.`
- Right path but wrong status → `VM {{ vm_name }} hit {{ expected_path }} but got {{ <observed status> }} (expected {{ expected_status }}). Pinned-MAC case = stale or missing host file; random-MAC case = a per-host file exists for what should be an unpinned MAC.`

## Orchestration changes

### Serial mode (`_arch_test.yml`)

Per-case flow:

1. Snapshot pre log line count (primitive 4 pre-shot).
2. Snapshot pre TFTP hits (existing).
3. Apply VM, wait Ready (existing).
4. Lookup DHCP lease → `_vm_ip` AND `_vm_mac` (primitive 3, augmented to extract both).
5. Pause (existing).
6. Snapshot post TFTP hits (existing).
7. TFTP-hits assert (existing).
8. Compute `_expected_status = 200 if _vm_mac in _pxe_pinned_macs else 404`.
9. `include_tasks: _verify_http.yml` with `{vm_name, vm_mac, vm_ip, expected_status, pre_log_line_count}`.

### Parallel mode (`test_netboot_pxe.yml`)

Same flow but batched: the pre log line count is captured once (shared); each VM's lease lookup yields its own `vm_ip`/`vm_mac`; the verify step is looped per case. Per-VM-IP filtering inside `_verify_http.yml` keeps the cases independent inside the shared log slice.

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
| netbootxyz container name or log layout differs from what this design assumes | Captured by Task 1 spike (recorded above). Container name `ix-netbootxyz-netbootxyz-1`, log at `/config/log/nginx/access.log`, runtime `docker`. |
| Clock skew | Line-count slicing, no timestamps. |
| Log rotation drops lines mid-test | Bounded by ≤180s pause; nginx rotates on size, not by minute; flagged in code comment. |
| nginx returns 200 for a wrong path (e.g. directory listing instead of the per-host file) | Preflight static pin-content substring check fails fast before any VM boots. |
| Smoke pin fragment in inventory drifts from what's served | Same preflight check — substring match catches drift. |
| iPXE retains the IP into a kernel that DHCPs again later (would generate extra log lines) | Smoke VMs are diskless; iPXE never hands off to a kernel. The pin fragments `exit 0` after the echo. Not a concern for the smoke cases. |
| Test runner can't reach `netbootxyz_self_url` (e.g. wrong VLAN) | Preflight HTTP probe of `/` fails fast with the relevant URL in the error. |
| `docker exec` requires elevated privileges on truenas | The truenas connection is already root for other playbooks (`playbooks/truenas/*`); verified at plan time. |

## Testing strategy

Manual ladder, run from the existing AWX EE / ansible-navigator container:

1. Run with current default `pxe_test_arches`. Confirm: 4 cases pass — 2 pinned MACs see `/menus/host/MAC-…` returning 200 (and the substring check at preflight passes), 2 random MACs see the same path returning 404.
2. Negative: temporarily rename `host/MAC-020000505801.ipxe` on truenas. Re-run. Expect: preflight catches it first (404 on the pre-fetch) before any VM boots. Restore the file.
3. Negative: temporarily corrupt the smoke pin fragment body (edit inventory, run `playbooks/netboot/deploy_assets.yml --tags render,push`). Re-run smoke test. Expect: preflight fails on the substring check before any VM boots.
4. Negative: stop the netbootxyz container. Re-run smoke test. Expect: preflight fails on the HTTP `/` probe.
5. Re-run twice in succession with no changes — both runs report identical pass/fail (no per-run state).
6. `ansible-lint --profile=production` and `yamllint` clean before commit.

No molecule scenario (consistent with the rest of `playbooks/kubevirt/`).

## Open items resolved during brainstorming

- Cases verified headlessly: pin-fetch (per-host short-circuit, expect 200 on the per-host path), menu-fetch (unpinned fall-through, expect 404 on the per-host path — the 404 IS the proof of fall-through). NOT pin-fragment-executed (rejected as "needs serial console scraping or phone-home — out of scope here").
- Verification mechanism: HTTP fetch by test runner at preflight (catches static config drift) + nginx access log slice via `docker exec ... tail` (catches dynamic dispatch breakage). No virtctl console; no probe URL.
- Test fixture location: `pxe_test_arches` stays in the playbook (test concern). Inventory `netboot_host_pins` is read-only from the test's perspective.
- Inventory schema: unchanged. No `smoke_test:` flag added.
- Real-host pins (helpernode/p330/hpg5/etc.): NOT booted by the test. Only the two `02:00:00:50:58:0*` smoke pins.
- Per-case discriminator: nginx HTTP **status code** on the per-host path (200 vs 404). Single observable, two interpretations. Replaces the original "menu.ipxe vs host/MAC-* path" framing — `menu.ipxe` is served via TFTP, not HTTP, in the actual deployment (Task 1 spike).
- Substring check on served pin body: preflight only (test-side default substring map keyed by MAC). Per-case substring assertion dropped because preflight already validates the body.
- Boot-flip helper playbook: out of scope; deferred to a follow-up brainstorm. The four primitives this design produces are the foundation it will reuse.
- IP **and MAC** source for log correlation: rb5009 DHCP lease table — query yields both the IP and the MAC (so KubeVirt-generated random MACs are observable without reading VMI status).
- Container name / log layout: confirmed by Task 1 spike. Container `ix-netbootxyz-netbootxyz-1` (TrueCharts naming on TrueNAS SCALE), runtime `docker`, nginx access log at `/config/log/nginx/access.log` inside the container, read via `docker exec ... tail` since nginx is configured to log to a file rather than stdout.
