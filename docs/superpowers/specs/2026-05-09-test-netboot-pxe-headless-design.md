# Headless verification for `test_netboot_pxe` — design

**Date:** 2026-05-09
**Status:** approved (brainstorming complete)
**Scope:** extend `playbooks/kubevirt/test_netboot_pxe/` so each smoke-test VM verifies, without console scraping, that the netbootxyz HTTP server served the *expected* file (per-host pin or main menu) to the VM's leased IP. Today the playbook only confirms the iPXE binary fetched from rb5009 (TFTP hits) — the comment block at the top of `test_netboot_pxe.yml` explicitly defers the iPXE → TrueNAS chainload check because "that needs console scraping." This design closes that gap.

## Goals

- Headlessly assert that a pinned-MAC VM causes netbootxyz to serve `/menus/host/MAC-<hexraw>.ipxe`, and that an unpinned VM causes it to serve `/menu.ipxe` and *not* any per-host file.
- Detect three regression classes the current playbook misses: (a) the deployed pin file is stale or wrong, (b) iPXE fetched the binary but never chained to netbootxyz, (c) the menu fall-through is silently chaining to a leftover per-host file.
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
pre  = TFTP hits on rb5009                                    ┐
pre  = wall-clock marker for the netbootxyz log lookback      │
apply VM, wait for Ready                                      │
vm_ip = lookup rb5009 DHCP lease by MAC (poll up to 30s)      │
pause for boot wait window                                    │
post = TFTP hits on rb5009                                    │
post = netbootxyz container log lines since pre marker        ┘
assert TFTP hits incremented (existing)
assert exactly one HTTP GET for the expected path from vm_ip
if expected = menu: assert NO GET /menus/host/MAC-* from vm_ip
```

Preflight (once per playbook run, before any VM is applied) does a separate static check:

1. Build a `{mac → pin}` lookup from `netboot_host_pins`.
2. HTTP GET `{{ netbootxyz_self_url }}/menu.ipxe` → 200 + body starts with `#!ipxe`. Catches netbootxyz being down before any VM is wasted.
3. For every smoke pin in inventory (the `02:00:00:50:58:0*` ones), HTTP GET `{{ netbootxyz_self_url }}/menus/host/MAC-<hexraw>.ipxe` → 200 + body contains the pin-specific substring (default `=== pxe-test smoke pin:`). Catches `deploy_assets.yml` being stale, separately from the per-VM dynamic check.

## File layout

```
playbooks/kubevirt/test_netboot_pxe/
  test_netboot_pxe.yml      # extended: include _preflight.yml, snapshot/grep nbxyz log in parallel mode
  _arch_test.yml            # extended: include _verify_http.yml inside the per-case block
  _preflight.yml            # NEW: build pin lookup, http-probe netbootxyz root, static pin-content check
  _verify_http.yml          # NEW: shared HTTP-fetch + access-log assertion, callable per case
  vm.yaml.j2                # unchanged
```

`_verify_http.yml` is its own file because both the serial path (`_arch_test.yml`) and the parallel path (the inline block in `test_netboot_pxe.yml`) call the same logic. Splitting it is the only way to avoid a copy-paste fork.

## Test-case schema (`pxe_test_arches`) — two new optional fields

```yaml
pxe_test_arches:
  - name: pxe-test-bios-pinned
    arch: bios
    binary: netboot.xyz.kpxe
    mac: '02:00:00:50:58:01'
    # NEW (both optional, defaulted at preflight):
    expected_fetch: auto         # auto | host_pin | menu | <literal path under netbootxyz_self_url>
    expected_substring: ''       # optional grep on the served file body; empty = skip body check
```

`expected_fetch` resolution order:

| Value | Resolves to |
|---|---|
| `auto` (default, or unset) | If `mac` is set and is in `netboot_host_pins` → `/menus/host/MAC-<hexraw>.ipxe`. Otherwise → `/menu.ipxe`. |
| `host_pin` | Force `/menus/host/MAC-<hexraw>.ipxe`. Fails preflight if `mac` not set. |
| `menu` | Force `/menu.ipxe`. |
| `<literal path>` | Used verbatim. Caller is responsible for it being a valid path under `netbootxyz_self_url`. |

`expected_substring`:

- Empty (default) — substring check skipped.
- For the two smoke pins, the playbook ships defaults at the playbook-vars level (a `pxe_test_substring_defaults` map keyed by MAC); a per-entry `expected_substring` overrides. Default for both smoke pins: `=== pxe-test smoke pin:`.
- Body is fetched once by the preflight static check and re-used for the assertion (no second GET).

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

### 3. `lookup_dhcp_lease_ip(mac)`

`community.routeros.command`, `delegate_to: rb5009`:

```
/ip dhcp-server lease print detail without-paging where mac-address="<mac>"
```

Wrapped in `until` retry — up to 30 seconds, 5-second interval — because the iPXE DHCP exchange can lag a few seconds behind VMI Ready. Fails the case if no lease appears with a distinct error: `VM <name> (MAC <mac>) never received a DHCP lease from rb5009 within 30s. Check OVN bridging on the CUDN.` This message intentionally points at the network layer, not at netbootxyz — the failure mode is independent.

### 4. `grep_nbxyz_log_since(host_ip, since_seconds)`

`delegate_to: {{ netbootxyz_host }}` (truenas), runs:

```
podman logs --since={{ since_seconds }}s {{ netbootxyz_container_name }}
```

with the result piped through a regex filter for `host_ip`. Returns the list of fetched paths (one per nginx access-log line that matches the IP).

`since_seconds` is captured at the start of each case as `(now() - case_start_ts) + 5` (small slop) — relative seconds rather than absolute timestamps, so we don't depend on truenas/test-runner clock alignment.

`netbootxyz_container_name` defaults to `netbootxyz`. The existing TrueNAS deployment name needs to be confirmed in the implementation plan (Task 1 spike — `podman ps --filter name=netbootxyz` + `podman inspect` on the LogConfig). If the linuxserver image logs to a file rather than stdout, this primitive switches to `cat <log_path>` instead; same interface to the caller.

## Per-case assertion logic (`_verify_http.yml`)

Inputs (as task vars): `vm_name`, `vm_mac`, `vm_ip`, `expected_fetch_path`, `expected_substring`, `case_start_ts`.

Steps:

1. `fetched_paths = grep_nbxyz_log_since(vm_ip, now() - case_start_ts + 5)`.
2. Assert `expected_fetch_path in fetched_paths`. Fail msg: `VM {{ vm_name }} (IP {{ vm_ip }}) was expected to fetch {{ expected_fetch_path }} from netbootxyz but the access log shows: {{ fetched_paths }}. Either iPXE never reached the netbootxyz HTTP root (check the chain to {{ netbootxyz_self_url }} from the binary), or the matcher table is wrong.`
3. If `expected_fetch_path == '/menu.ipxe'`: assert no element of `fetched_paths` matches `^/menus/host/MAC-`. Fail msg: `Random-MAC VM {{ vm_name }} unexpectedly fetched a per-host file ({{ <matching path> }}). Either menu fall-through is broken or a stale host file is being served.`
4. If `expected_substring` is non-empty:
   - If `expected_fetch_path == /menu.ipxe` → skip; preflight already validated the body at startup.
   - Else if `expected_fetch_path` is in the preflight body cache (a per-host pin path covered by the static check) → re-use the cached body, assert substring present.
   - Else (literal path not pre-fetched) → fetch the URL fresh with `ansible.builtin.uri`, assert substring present.

## Orchestration changes

### Serial mode (`_arch_test.yml`)

Per-case block, between "pause" and "Snapshot rb5009 TFTP hits AFTER":

```yaml
- name: "Wait for {{ _vm_name }} DHCP lease (MAC {{ _vm_mac }})"
  # primitive 3 — populates _vm_ip
- name: "Verify HTTP fetch for {{ _vm_name }}"
  ansible.builtin.include_tasks: _verify_http.yml
  vars:
    vm_name: "{{ _vm_name }}"
    vm_mac: "{{ _vm_mac }}"
    vm_ip: "{{ _vm_ip }}"
    expected_fetch_path: "{{ _expected_fetch_path }}"
    expected_substring: "{{ _expected_substring }}"
    case_start_ts: "{{ _case_start_ts }}"
```

`_case_start_ts` is captured before the "Apply VM" step. `_expected_fetch_path` and `_expected_substring` come from preflight's per-case resolution table.

### Parallel mode (`test_netboot_pxe.yml`)

The "Snapshot AFTER" block gains a sibling `include_tasks: _verify_http.yml` looped per case, after the existing TFTP-hits assertion. Each iteration uses its own `vm_ip` (resolved via primitive 3 once per VM, between "Wait for all VMs to reach Ready" and the long pause) and its own `case_start_ts` (the same shared `pre` timestamp captured before the batch apply works fine — the per-IP grep narrows correctly).

## Idempotency, retries, timing

| Concern | Handling |
|---|---|
| iPXE DHCP can lag VMI Ready by a few seconds | DHCP lease lookup retries for 30s, 5s interval. |
| `podman logs --since=<rel>` survives clock skew | Use relative seconds, captured per-case. |
| Log rotation between snapshots | Bounded by the `pause` window (≤180s default). nginx access logs at homelab volume rotate on size, not by minute — extremely unlikely to rotate inside a single case. Flagged, not designed-around. |
| Parallel mode: log lines from N VMs interleave | Per-VM-IP grep handles it. Two VMs sharing a dynamic IP would alias — impossible in practice with the rb5009 DHCP pool. Flagged. |
| Re-running the playbook | Preflight is read-only. Per-case work is unchanged in idempotency: VM applied → asserted → deleted. No state persists between runs. |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| netbootxyz container name or log path differs from what this design assumes | Plan Task 1 spike confirms via `podman ps` / `podman inspect` on truenas. Outcome documented as a header comment in `_verify_http.yml`. |
| Time skew test-runner ↔ truenas | Relative `--since=<seconds>` instead of absolute timestamp. |
| Log rotation drops lines mid-test | `pause` capped at ≤180s; rotation by line count not seen at homelab volume; flagged in code comment. |
| nginx returns 200 for a wrong path (e.g. `/host/MAC-…` falls through to `index.html`) | Preflight static pin-content substring check fails fast before any VM boots. |
| Smoke pin fragment in inventory drifts from what's served | Same preflight check — substring match catches drift. |
| iPXE retains the IP into a kernel that DHCPs again later (would generate extra log lines) | Smoke VMs are diskless; iPXE never hands off to a kernel. The pin fragments `exit 0` after the echo. Not a concern for the smoke cases. |
| Test runner can't reach `netbootxyz_self_url` (e.g. wrong VLAN) | Preflight HTTP probe of `/menu.ipxe` fails fast with the relevant URL in the error. |
| `podman logs` requires elevated privileges on truenas | The truenas connection is already root for other playbooks; verified at plan time. |

## Testing strategy

Manual ladder, run from the existing AWX EE / ansible-navigator container:

1. Run with current default `pxe_test_arches`. Confirm: 4 cases pass — 2 pinned (BIOS + UEFI smoke pins) match per-host file fetched, 2 random match menu fetched and no host/MAC fetched.
2. Negative: temporarily rename `host/MAC-020000505801.ipxe` on truenas. Re-run. Expect: that case fails with "expected to fetch /menus/host/MAC-020000505801.ipxe but the access log shows: [...menu.ipxe]". Restore the file.
3. Negative: temporarily corrupt the smoke pin fragment body (edit inventory, run `playbooks/netboot/deploy_assets.yml --tags render,push`). Re-run smoke test. Expect: preflight fails on the substring check before any VM boots.
4. Negative: stop the netbootxyz container. Re-run smoke test. Expect: preflight fails on the `/menu.ipxe` HTTP probe.
5. Re-run twice in succession with no changes — both runs report identical pass/fail (no per-run state).
6. `ansible-lint --profile=production` and `yamllint` clean before commit.

No molecule scenario (consistent with the rest of `playbooks/kubevirt/`).

## Open items resolved during brainstorming

- Cases verified headlessly: pin-fetch (per-host short-circuit), menu-fetch (unpinned fall-through), pin fragment served-correctly. NOT pin-fragment-executed (rejected as "needs serial console scraping or phone-home — out of scope here").
- Verification mechanism: hybrid HTTP fetch by test runner (catches static config drift) + container access log grep (catches dynamic dispatch breakage). No virtctl console; no probe URL.
- Test fixture location: `pxe_test_arches` stays in the playbook (test concern). Inventory `netboot_host_pins` is read-only from the test's perspective.
- Inventory schema: unchanged. No `smoke_test:` flag added.
- Real-host pins (helpernode/p330/hpg5/etc.): NOT booted by the test. Only the two `02:00:00:50:58:0*` smoke pins.
- Negative menu assertion (no host/MAC-* fetched on random-MAC cases): YES, included.
- Substring check on served pin body: test-side default substring map keyed by MAC, per-case override available. Inventory stays clean.
- Boot-flip helper playbook: out of scope; deferred to a follow-up brainstorm. The four primitives this design produces are the foundation it will reuse.
- IP source for grep correlation: rb5009 DHCP lease table (works whether or not qemu-guest-agent is in the boot image — and the diskless smoke VMs never run guest-agent anyway).
- Container name / log driver assumptions: deferred to plan Task 1 spike on truenas; outcome captured as a header comment in `_verify_http.yml`.
