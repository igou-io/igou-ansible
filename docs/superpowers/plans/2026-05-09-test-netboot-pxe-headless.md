# Headless verification for `test_netboot_pxe` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `playbooks/kubevirt/test_netboot_pxe/` so each smoke-test VM verifies — without console scraping — that the netbootxyz HTTP server served the *expected* file (per-host pin or main menu) to the VM's leased IP.

**Architecture:** Add a preflight stage that statically validates netbootxyz HTTP reachability and smoke-pin file content, plus computes per-case `expected_fetch_path` / `expected_substring` from inventory's `netboot_host_pins`. Add per-case HTTP-side verification that correlates the VM's leased IP (queried from rb5009 DHCP) against new lines in the netbootxyz container access log. Two new shared task files keep serial and parallel orchestration paths DRY.

**Tech Stack:** Ansible (kubernetes.core, community.routeros, ansible.builtin.uri), `podman logs` over the existing TrueNAS SSH connection, RouterOS DHCP API.

---

## Reference material

- **Spec:** `docs/superpowers/specs/2026-05-09-test-netboot-pxe-headless-design.md` — read this first; it explains every design decision.
- **Existing playbook tree:** `playbooks/kubevirt/test_netboot_pxe/{test_netboot_pxe.yml,_arch_test.yml,vm.yaml.j2}`.
- **Inventory:** `igou-inventory/group_vars/all/netboot.yml` defines `netboot_host_pins`, `netbootxyz_host`, `netbootxyz_self_url`. `netboot_host_pins` is **read-only** from this plan's perspective — do not modify it.
- **Smoke pin fragments to assert against** (already in inventory):
  - MAC `02:00:00:50:58:01` body contains literal `=== pxe-test smoke pin: bios`
  - MAC `02:00:00:50:58:02` body contains literal `=== pxe-test smoke pin: uefi-x64`
- **Inventory file path:** `igou-inventory/inventory.yaml`. All commands assume CWD is the repo root.

## Conventions

- **Run commands from:** repository root `/workspace/igou-ansible`.
- **Run-the-playbook command:** `ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml -i igou-inventory/inventory.yaml`.
- **Linters:** `ansible-lint --profile=production playbooks/kubevirt/test_netboot_pxe/` and `yamllint playbooks/kubevirt/test_netboot_pxe/`.
- **YAML style** (matches the rest of the repo and the existing files in this tree):
  - `---` at file top, 2-space indent.
  - YAML 1.2 booleans (`true`/`false`).
  - `gather_facts: false` on plays.
  - Fact names prefixed `_pxe_…` to scope them; transient block-local vars use leading `_` plus a short name.
  - Tasks named with imperative phrasing; per-loop labels via `loop_control: { label: "<name>" }`.
- **Connection assumptions** (already configured by group_vars and used by the existing playbook):
  - `rb5009.igou.systems` — RouterOS over the `community.routeros.*` collection. The existing TFTP-hits step works; reuse the same `delegate_to`.
  - `truenas` — SSH with `become: true` available. Other playbooks in `playbooks/truenas/` use the same connection.

## Files Created/Modified/Deleted

**Create:**
- `playbooks/kubevirt/test_netboot_pxe/_preflight.yml`
- `playbooks/kubevirt/test_netboot_pxe/_dhcp_lease_lookup.yml`
- `playbooks/kubevirt/test_netboot_pxe/_verify_http.yml`

**Modify:**
- `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml`
- `playbooks/kubevirt/test_netboot_pxe/_arch_test.yml`

**Delete:** none.

## Pre-flight assumptions (verify before Task 1)

- `KUBECONFIG` is set in the environment and points at the OpenShift cluster (the existing playbook already depends on this).
- The user can connect to `rb5009.igou.systems` and `truenas` from the current shell using the existing inventory (validated by running the existing playbook end-to-end first).
- Static DHCP leases on rb5009 already exist for MACs `02:00:00:50:58:01` and `02:00:00:50:58:02` (referenced in the existing playbook header comment as "matches static DHCP leases on rb5009"). Random-MAC test cases will get dynamic leases from the pool.
- `playbooks/netboot/deploy_assets.yml` has been run recently enough that the rendered `host/MAC-020000505801.ipxe` and `host/MAC-020000505802.ipxe` files exist on the TrueNAS netbootxyz container with bodies containing the smoke pin substrings above.

If any of these are not true, stop and surface it before starting Task 1.

---

## Task 1: Spike — confirm netbootxyz container name and log driver on truenas

This unblocks Task 5 (`_verify_http.yml`). The spec calls for documenting the spike outcome as a header comment in `_verify_http.yml`.

**Files:**
- Create: none yet.
- Modify: none yet.
- Output: a short note recorded in this plan's task body for downstream tasks to reference.

- [ ] **Step 1: Identify the running netbootxyz container on truenas**

Run:
```bash
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.command \
  -a 'podman ps --format "{{ "{{" }}.Names{{ "}}" }}\t{{ "{{" }}.Image{{ "}}" }}"' \
  -b
```

Expected: one row whose Image is something like `lscr.io/linuxserver/netbootxyz` (or the equivalent linuxserver image used in the TrueNAS app deployment). Capture the value from the Names column — call it `<NBXYZ_CTR>`. Common value: `netbootxyz`.

If multiple matches or zero matches: stop and surface this — the deployment is not in the expected shape.

- [ ] **Step 2: Inspect the log driver**

Run:
```bash
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.command \
  -a 'podman inspect <NBXYZ_CTR> --format "{{ "{{" }}.HostConfig.LogConfig.Type{{ "}}" }}"' \
  -b
```

(Replace `<NBXYZ_CTR>` with the value from Step 1.)

Expected output: `k8s-file` or `journald` or `json-file`. Any of these are fine — `podman logs` works against all three.

If the output is `none`: stop. The container was started with logging disabled and `podman logs` cannot read access lines.

- [ ] **Step 3: Confirm nginx access lines reach `podman logs`**

Run:
```bash
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.command \
  -a 'podman logs --tail=20 <NBXYZ_CTR>' \
  -b
```

Expected: nginx access lines visible, format roughly:
```
<source_ip> - - [<timestamp>] "GET /menu.ipxe HTTP/1.1" 200 1234 "-" "iPXE/..."
```

If the output contains no nginx access lines (only s6-overlay startup messages): stop. The image is configured to log access to a file rather than stdout. Document the file path and switch the strategy in Task 5 to `cat <log_path>` over SSH; otherwise the rest of the plan stands.

- [ ] **Step 4: Hit the HTTP root once and confirm a fresh access line appears**

Run (from the test-runner shell):
```bash
curl -sf http://10.10.45.242/menu.ipxe > /dev/null && echo OK
```

Then re-run the `podman logs --tail=20` from Step 3. Expected: a new access line for the test-runner's source IP fetching `/menu.ipxe`.

- [ ] **Step 5: Record findings**

Add a single-line comment to the top of this task's local notes:

```
SPIKE OUTCOME (2026-05-09):
  container_name = <NBXYZ_CTR>          # e.g. "netbootxyz"
  log_driver     = <log_driver>          # k8s-file | journald | json-file
  access_log_via = podman_logs           # confirmed nginx access lines appear in `podman logs`
```

These three values will be embedded as a comment block in `_verify_http.yml` in Task 5. Carry them forward.

- [ ] **Step 6: No commit for this task**

This is a read-only investigation. Nothing to commit yet.

---

## Task 2: Add `_preflight.yml` — netbootxyz HTTP root probe + wire into the playbook

Goal: a runnable preflight stage that fails fast if netbootxyz is unreachable, with no other behaviour yet.

**Files:**
- Create: `playbooks/kubevirt/test_netboot_pxe/_preflight.yml`
- Modify: `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml`

- [ ] **Step 1: Create `_preflight.yml`**

Write the file with this exact content:

```yaml
---
# Preflight for the netboot.xyz HTTP-side smoke test. Runs once at the
# top of test_netboot_pxe.yml, before any VM is applied.
#
# Stages (each gated on the previous):
#   1. HTTP-probe netbootxyz_self_url/menu.ipxe -- fail fast if down.
#   2. (Task 3) Build the set of pinned MACs from netboot_host_pins,
#      HTTP-fetch every smoke pin file referenced by pxe_test_arches,
#      and assert each body contains the expected substring.
#   3. (Task 4) Resolve expected_fetch_path / expected_substring per
#      pxe_test_arches entry into _pxe_resolved_cases.
#
# All preflight tasks are delegate_to: localhost -- no TrueNAS or rb5009
# contact at this stage.

- name: Preflight -- netbootxyz HTTP root is reachable and serving menu.ipxe
  ansible.builtin.uri:
    url: "{{ netbootxyz_self_url }}/menu.ipxe"
    return_content: true
    status_code: 200
    timeout: 10
  register: _pxe_preflight_menu
  delegate_to: localhost
  changed_when: false

- name: Preflight -- assert menu.ipxe body looks like an iPXE script
  ansible.builtin.assert:
    that:
      - _pxe_preflight_menu.content is defined
      - _pxe_preflight_menu.content.startswith('#!ipxe')
    fail_msg: >-
      {{ netbootxyz_self_url }}/menu.ipxe responded 200 but the body did
      not start with '#!ipxe'. Either nginx is fronting a default page
      because the netbootxyz container's /config/menus/ is empty, or
      playbooks/netboot/deploy_assets.yml has never been run on this
      host. First 200 chars of body: {{ _pxe_preflight_menu.content[:200] }}

- name: Preflight -- cache menu.ipxe body for later substring checks
  ansible.builtin.set_fact:
    _pxe_preflight_bodies:
      menu: "{{ _pxe_preflight_menu.content }}"
```

- [ ] **Step 2: Wire `_preflight.yml` into `test_netboot_pxe.yml`**

Modify `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml`. Locate the `tasks:` block (after the `vars:` block, around the comment `# --- Pre-flight (idempotent, never destructive) -----`). Insert the new include as the **first** task in `tasks:`, before the existing CUDN read:

```yaml
  tasks:
    - name: Preflight -- netbootxyz HTTP-side checks
      ansible.builtin.include_tasks: _preflight.yml

    # --- Pre-flight (idempotent, never destructive) --------------------------

    - name: Read each ClusterUserDefinedNetwork referenced by pxe_test_arches
      ...
```

The existing CUDN-read block stays exactly where it is; only a new include is inserted above it.

- [ ] **Step 3: Run the playbook end-to-end and confirm the new preflight runs**

Run:
```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml
```

Expected: the first task to print is `Preflight -- netbootxyz HTTP root is reachable and serving menu.ipxe`. The full playbook should still pass end-to-end (TFTP-hits checks behave the same as before).

- [ ] **Step 4: Demonstrate the failure mode**

Run a one-off invocation with a deliberately broken `netbootxyz_self_url` to confirm the preflight catches a down service:

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e 'netbootxyz_self_url=http://127.0.0.1:1'
```

Expected: the `Preflight -- netbootxyz HTTP root is reachable...` task fails with a connection error. No VM is applied. The playbook exits with a non-zero return code.

- [ ] **Step 5: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_preflight.yml \
  playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: add preflight HTTP probe of netbootxyz_self_url

Fails fast if netbootxyz is down before any VM is applied. First step
of the headless verification design (see
docs/superpowers/specs/2026-05-09-test-netboot-pxe-headless-design.md).
EOF
)"
```

---

## Task 3: Preflight — pin lookup + smoke-pin substring assertions

**Files:**
- Modify: `playbooks/kubevirt/test_netboot_pxe/_preflight.yml`
- Modify: `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml` (add the `pxe_test_substring_defaults` map to `vars:`)

- [ ] **Step 1: Add the substring defaults map to the playbook vars**

Modify `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml`. In the `vars:` block, just below `pxe_test_parallel: false` and above the `pxe_test_arches:` list comment, insert:

```yaml
    # Default substring to grep for in each smoke pin's served body,
    # keyed by lowercase MAC. Per-entry expected_substring (Task 4)
    # overrides; an unmapped MAC defaults to no substring check.
    pxe_test_substring_defaults:
      "02:00:00:50:58:01": "=== pxe-test smoke pin: bios"
      "02:00:00:50:58:02": "=== pxe-test smoke pin: uefi-x64"
```

- [ ] **Step 2: Extend `_preflight.yml` with the pin lookup**

Append the following after the existing `_pxe_preflight_bodies` set_fact in `_preflight.yml`:

```yaml
- name: Preflight -- build set of pinned MACs (lowercase) from inventory
  ansible.builtin.set_fact:
    _pxe_pinned_macs: >-
      {{ netboot_host_pins
         | default([])
         | selectattr('mac', 'defined')
         | map(attribute='mac')
         | map('lower')
         | list }}

- name: Preflight -- collect smoke-pin MACs referenced by pxe_test_arches that resolve to a pin
  ansible.builtin.set_fact:
    _pxe_preflight_pin_macs: >-
      {{ pxe_test_arches
         | selectattr('mac', 'defined')
         | map(attribute='mac')
         | map('lower')
         | unique
         | select('in', _pxe_pinned_macs)
         | list }}
```

- [ ] **Step 3: Append the per-pin HTTP fetch + substring assertion**

Append (still in `_preflight.yml`) the loop that fetches each pin file and asserts its substring:

```yaml
- name: Preflight -- fetch each smoke pin file from netbootxyz
  ansible.builtin.uri:
    url: "{{ netbootxyz_self_url }}/menus/host/MAC-{{ item | regex_replace(':', '') }}.ipxe"
    return_content: true
    status_code: 200
    timeout: 10
  register: _pxe_preflight_pin_results
  delegate_to: localhost
  changed_when: false
  loop: "{{ _pxe_preflight_pin_macs }}"
  loop_control:
    label: "MAC-{{ item | regex_replace(':', '') }}.ipxe"

- name: Preflight -- assert each smoke pin body contains its expected substring
  ansible.builtin.assert:
    that:
      - _expected_substring == '' or _expected_substring in item.content
    fail_msg: >-
      Pin file MAC-{{ item.item | regex_replace(':', '') }}.ipxe at
      {{ netbootxyz_self_url }} returned 200 but its body does not contain
      the expected substring '{{ _expected_substring }}'. This usually
      means inventory's netboot_host_pins was changed but
      playbooks/netboot/deploy_assets.yml has not been re-run.
      First 200 chars: {{ item.content[:200] }}
  vars:
    _expected_substring: "{{ pxe_test_substring_defaults[item.item] | default('') }}"
  loop: "{{ _pxe_preflight_pin_results.results }}"
  loop_control:
    label: "MAC-{{ item.item | regex_replace(':', '') }}.ipxe"

- name: Preflight -- cache pin bodies keyed by lowercase MAC for per-case re-use
  ansible.builtin.set_fact:
    _pxe_preflight_bodies: >-
      {{ _pxe_preflight_bodies
         | combine({ ('pin:' ~ item.item): item.content }) }}
  loop: "{{ _pxe_preflight_pin_results.results }}"
  loop_control:
    label: "MAC-{{ item.item | regex_replace(':', '') }}.ipxe"
```

- [ ] **Step 4: Run the playbook and verify preflight passes**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml
```

Expected: the new preflight tasks run successfully, fetching two pin files and asserting their bodies contain the expected substrings. The rest of the playbook (TFTP-hits checks) still passes.

- [ ] **Step 5: Demonstrate the failure mode**

Override the substring map to a value that won't appear in the pin body, to confirm the assertion fires:

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e '{"pxe_test_substring_defaults": {"02:00:00:50:58:01": "DEFINITELY-NOT-IN-THE-BODY", "02:00:00:50:58:02": "DEFINITELY-NOT-IN-THE-BODY"}}'
```

Expected: the `Preflight -- assert each smoke pin body...` task fails for both pins. No VMs applied.

- [ ] **Step 6: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_preflight.yml \
  playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: preflight asserts smoke pin bodies on netbootxyz

Per-MAC substring map catches inventory drift -- if netboot_host_pins
is updated but deploy_assets.yml is not re-run, preflight fails before
any VM is applied. Bodies are cached for per-case re-use (Task 5).
EOF
)"
```

---

## Task 4: Preflight — resolve `expected_fetch_path` / `expected_substring` per case

**Files:**
- Modify: `playbooks/kubevirt/test_netboot_pxe/_preflight.yml`

- [ ] **Step 1: Append the per-case resolution to `_preflight.yml`**

Append at the end of `_preflight.yml`:

```yaml
- name: Preflight -- resolve expected_fetch_path and expected_substring per pxe_test_arches entry
  ansible.builtin.set_fact:
    _pxe_resolved_cases: "{{ _pxe_resolved_cases | default([]) + [_resolved] }}"
  vars:
    _name: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
    _mac_raw: "{{ item.mac | default('') }}"
    _mac: "{{ _mac_raw | lower }}"
    _has_mac: "{{ _mac_raw | length > 0 }}"
    _is_pinned: "{{ _has_mac and _mac in _pxe_pinned_macs }}"
    _hexraw: "{{ _mac | regex_replace(':', '') }}"
    _pin_path: "/menus/host/MAC-{{ _hexraw }}.ipxe"
    _mode: "{{ item.expected_fetch | default('auto') }}"
    _expected_fetch_path: >-
      {%- if _mode == 'auto' -%}
      {{ _pin_path if _is_pinned else '/menu.ipxe' }}
      {%- elif _mode == 'host_pin' -%}
      {{ _pin_path }}
      {%- elif _mode == 'menu' -%}
      /menu.ipxe
      {%- else -%}
      {{ _mode }}
      {%- endif -%}
    _default_substring: "{{ pxe_test_substring_defaults[_mac] | default('') if _is_pinned else '' }}"
    _resolved:
      name: "{{ _name }}"
      arch: "{{ item.arch }}"
      binary: "{{ item.binary }}"
      mac: "{{ _mac }}"
      has_mac: "{{ _has_mac }}"
      is_pinned: "{{ _is_pinned }}"
      expected_fetch_path: "{{ _expected_fetch_path | trim }}"
      expected_substring: "{{ item.expected_substring | default(_default_substring) }}"
  loop: "{{ pxe_test_arches }}"
  loop_control:
    label: "{{ item.name | default('pxe-test-' ~ item.arch) }}"

- name: Preflight -- assert host_pin mode entries declare a MAC
  ansible.builtin.assert:
    that:
      - item.has_mac
    fail_msg: >-
      pxe_test_arches entry '{{ item.name }}' specifies expected_fetch:
      host_pin but no mac. host_pin mode requires a MAC so the per-host
      pin path can be computed.
  loop: "{{ _pxe_resolved_cases }}"
  loop_control:
    label: "{{ item.name }}"
  when: item.expected_fetch_path is search('/menus/host/MAC-')

- name: Preflight -- print resolved test cases (for visibility)
  ansible.builtin.debug:
    msg: "{{ item.name }}: fetch={{ item.expected_fetch_path }} substring='{{ item.expected_substring }}'"
  loop: "{{ _pxe_resolved_cases }}"
  loop_control:
    label: "{{ item.name }}"
```

- [ ] **Step 2: Run the playbook; verify resolution**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml
```

Expected `debug` output (exact paths, hexraw lowercase, no separators):

```
pxe-test-bios-random:        fetch=/menu.ipxe                                      substring=''
pxe-test-bios-pinned:        fetch=/menus/host/MAC-020000505801.ipxe                substring='=== pxe-test smoke pin: bios'
pxe-test-uefi-x64-random:    fetch=/menu.ipxe                                      substring=''
pxe-test-uefi-x64-pinned:    fetch=/menus/host/MAC-020000505802.ipxe                substring='=== pxe-test smoke pin: uefi-x64'
```

Whitespace alignment doesn't matter; the values do. If any value is wrong, the resolution logic is broken — fix and re-run before commit.

- [ ] **Step 3: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_preflight.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: preflight resolves expected fetch path per case

Each pxe_test_arches entry resolves to {expected_fetch_path,
expected_substring} from its mac + inventory's netboot_host_pins, with
mode override (auto|host_pin|menu|<literal>). Resolution is the input
to the per-case HTTP assertion added in later tasks.
EOF
)"
```

---

## Task 5: Add `_dhcp_lease_lookup.yml` — VM IP from rb5009

**Files:**
- Create: `playbooks/kubevirt/test_netboot_pxe/_dhcp_lease_lookup.yml`

- [ ] **Step 1: Create the file**

Write the file with this exact content:

```yaml
---
# Look up a VM's leased IP from rb5009's DHCP server, given its MAC.
# Polls up to 30s (5s interval) -- iPXE's DHCP exchange can lag VMI
# Ready by a few seconds, especially in parallel mode where multiple
# discovers race the rb5009 DHCP daemon.
#
# Inputs (task vars expected on caller):
#   _vm_name   for the loop label / failure message
#   _vm_mac    MAC to query, any case, with-or-without separators
#
# Outputs:
#   _vm_ip     populated with the IP string (e.g. "10.10.9.42")
#
# Failure mode: if no lease appears within 30s, fails with a network-
# layer-flavoured message that does NOT confuse the failure with a
# netbootxyz problem.

- name: "Wait for {{ _vm_name }} DHCP lease (MAC {{ _vm_mac }})"
  community.routeros.command:
    commands:
      - >-
        /ip dhcp-server lease print detail without-paging
        where mac-address="{{ _vm_mac | upper }}"
  register: _pxe_lease_query
  changed_when: false
  delegate_to: rb5009.igou.systems
  retries: 6
  delay: 5
  until: _pxe_lease_query.stdout[0] is search('address=')

- name: "Extract IP from lease for {{ _vm_name }}"
  ansible.builtin.set_fact:
    _vm_ip: "{{ _pxe_lease_query.stdout[0] | regex_search('address=([0-9.]+)', '\\1') | first }}"

- name: "Assert IP was extracted for {{ _vm_name }}"
  ansible.builtin.assert:
    that:
      - _vm_ip is defined
      - _vm_ip | length > 0
    fail_msg: >-
      VM {{ _vm_name }} (MAC {{ _vm_mac }}) appears to have a lease entry
      on rb5009 but no address= field could be parsed. Raw output:
      {{ _pxe_lease_query.stdout[0] }}
```

- [ ] **Step 2: Stand-alone smoke test by running the existing playbook tasks once**

This file has no callers yet (those come in Tasks 7-8). The smoke check is just `yamllint` for now:

```bash
yamllint playbooks/kubevirt/test_netboot_pxe/_dhcp_lease_lookup.yml
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_dhcp_lease_lookup.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: add _dhcp_lease_lookup helper

Polls rb5009 for the VM's DHCP lease (MAC -> IP), 30s budget. Wired
in by Task 7 (serial) and Task 8 (parallel) of the headless plan.
EOF
)"
```

---

## Task 6: Add `_verify_http.yml` — log grep + per-case HTTP assertions

**Files:**
- Create: `playbooks/kubevirt/test_netboot_pxe/_verify_http.yml`

This file uses three values from the Task 1 spike:
- `<NBXYZ_CTR>` — the netbootxyz container name on truenas. Substitute literally; do not parametrise unless other playbooks already vary it.
- `<log_driver>` — recorded in a comment block.
- `access_log_via=podman_logs` — confirmed.

If the spike found that `podman logs` does NOT carry nginx access lines (Step 3 of Task 1), replace the `podman logs` invocation in Step 1 below with `cat <log_path>` over the same `delegate_to`, and adjust the `--since` filtering accordingly (drop `--since`, do client-side line counting instead).

- [ ] **Step 1: Create the file**

Write the file with this exact content (substitute `<NBXYZ_CTR>` with the literal container name from the spike — typically `netbootxyz`):

```yaml
---
# Per-case HTTP-side verification for the netboot.xyz smoke test.
# Called from both _arch_test.yml (serial mode) and the inline parallel
# block in test_netboot_pxe.yml.
#
# SPIKE OUTCOME (2026-05-09):
#   container_name = <NBXYZ_CTR>
#   log_driver     = <log_driver from Task 1, e.g. k8s-file>
#   access_log_via = podman_logs   # nginx access lines reach `podman logs`
#
# Inputs (task vars expected on caller):
#   vm_name              VM name, used in messages and loop labels
#   vm_mac               lowercase MAC of the VM (already normalised)
#   vm_ip                IP the VM was leased by rb5009
#   expected_fetch_path  path the netbootxyz access log should show GET'd
#   expected_substring   substring the served body must contain (or '')
#   case_start_seconds   integer seconds elapsed since the case began
#                          (used as `--since=<N>s` for `podman logs`)
#
# Side effect: emits two assertions per case (positive fetch + optional
# negative menu assertion + optional substring check).

- name: "Fetch netbootxyz access log lines since case start ({{ vm_name }})"
  ansible.builtin.command:
    cmd: "podman logs --since={{ case_start_seconds }}s <NBXYZ_CTR>"
  register: _pxe_nbxyz_logs
  changed_when: false
  delegate_to: "{{ netbootxyz_host }}"
  become: true

- name: "Extract fetched paths for {{ vm_name }} (IP {{ vm_ip }})"
  ansible.builtin.set_fact:
    _pxe_fetched_paths: >-
      {{ _pxe_nbxyz_logs.stdout_lines
         | select('search', '^' ~ vm_ip ~ ' ')
         | map('regex_search', '\"GET ([^ ]+) HTTP', '\\1')
         | select('truthy')
         | map('first')
         | list }}

- name: "Assert {{ vm_name }} fetched {{ expected_fetch_path }} from netbootxyz"
  ansible.builtin.assert:
    that:
      - expected_fetch_path in _pxe_fetched_paths
    fail_msg: >-
      VM {{ vm_name }} (IP {{ vm_ip }}) was expected to fetch
      {{ expected_fetch_path }} from netbootxyz, but the access log
      shows: {{ _pxe_fetched_paths }}. Either iPXE never reached the
      netbootxyz HTTP root (check the chain to {{ netbootxyz_self_url }}
      from the binary, or the rb5009 DHCP option-67 setting), or the
      matcher table is wrong.

- name: "Assert {{ vm_name }} did NOT fetch any host/MAC-* file (menu fall-through case)"
  ansible.builtin.assert:
    that:
      - _pxe_fetched_paths | select('match', '^/menus/host/MAC-') | list | length == 0
    fail_msg: >-
      Random-MAC VM {{ vm_name }} unexpectedly fetched a per-host file:
      {{ _pxe_fetched_paths | select('match', '^/menus/host/MAC-') | list }}.
      Either the menu fall-through is broken, or a stale per-host file
      is being served for this MAC. The pinned MAC's hexraw appears in
      the fetched path; cross-check against netboot_host_pins.
  when: expected_fetch_path == '/menu.ipxe'

- name: "Assert served body for {{ vm_name }} contains expected substring"
  when: expected_substring | length > 0
  block:
    - name: "Re-use cached body for {{ vm_name }} if available, else fetch fresh"
      ansible.builtin.set_fact:
        _pxe_served_body: >-
          {{ _pxe_preflight_bodies['pin:' ~ vm_mac]
             | default(_pxe_preflight_bodies['menu'])
             if (expected_fetch_path == '/menu.ipxe'
                 or ('pin:' ~ vm_mac) in _pxe_preflight_bodies)
             else '' }}

    - name: "Fetch served body for {{ vm_name }} (cache miss)"
      ansible.builtin.uri:
        url: "{{ netbootxyz_self_url }}{{ expected_fetch_path }}"
        return_content: true
        status_code: 200
        timeout: 10
      register: _pxe_fresh_body
      delegate_to: localhost
      changed_when: false
      when: _pxe_served_body | length == 0

    - name: "Substring assert for {{ vm_name }}"
      ansible.builtin.assert:
        that:
          - expected_substring in (_pxe_served_body if _pxe_served_body | length > 0 else _pxe_fresh_body.content)
        fail_msg: >-
          Served body for {{ vm_name }} at {{ expected_fetch_path }}
          does not contain '{{ expected_substring }}'.
```

- [ ] **Step 2: yamllint the file**

```bash
yamllint playbooks/kubevirt/test_netboot_pxe/_verify_http.yml
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_verify_http.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: add _verify_http helper

Per-case HTTP-side assertion: greps the netbootxyz container access
log (since case start) for GETs from the VM's leased IP, asserts the
expected path was fetched, and (for menu cases) asserts no per-host
file was fetched. Substring check re-uses the body cached at preflight
when available, otherwise fetches fresh.
EOF
)"
```

---

## Task 7: Wire HTTP verification into serial mode (`_arch_test.yml`)

**Files:**
- Modify: `playbooks/kubevirt/test_netboot_pxe/_arch_test.yml`

The existing per-arch flow becomes:

```
capture _case_start_seconds (now)
TFTP hits BEFORE
apply VM, wait Ready
DHCP lease lookup -> _vm_ip
pause for boot
TFTP hits AFTER
assert TFTP hits incremented (existing)
HTTP verify (new)
always: delete VM
```

- [ ] **Step 1: Replace `_arch_test.yml` with the augmented version**

Open `playbooks/kubevirt/test_netboot_pxe/_arch_test.yml` and replace its entire contents with:

```yaml
---
# Per-architecture smoke test, included once per entry in pxe_test_arches
# by ../test_netboot_pxe.yml when pxe_test_parallel is false. Wrapped in
# block/always so a failed assertion still tears down the VM before the
# next entry attempts.
#
# Expects loop variables on `item`:
#   item.arch    free-form arch label
#   item.binary  TFTP filename to watch on rb5009
# Optional per-entry overrides are documented in the parent playbook.
#
# Per-case assertions:
#   1. rb5009 TFTP hit counter for `item.binary` incremented (existing).
#   2. netbootxyz access log shows the VM's leased IP fetched the
#      expected path (host/MAC-<hexraw>.ipxe for pinned cases, /menu.ipxe
#      for random-MAC cases). See _verify_http.yml.

- name: "Smoke-test {{ _vm_name }}"
  vars:
    _vm_name: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
    _vm_mac: "{{ item.mac | default('') | lower }}"
    _wait: "{{ item.boot_wait_seconds | default(pxe_test_boot_wait_seconds) }}"
    _resolved: "{{ _pxe_resolved_cases | selectattr('name', 'equalto', _vm_name) | first }}"
  block:
    - name: "Capture case-start timestamp for {{ _vm_name }}"
      ansible.builtin.set_fact:
        _case_start_epoch: "{{ ansible_date_time.epoch | default(lookup('pipe','date -u +%s')) | int }}"

    - name: "Snapshot rb5009 TFTP hits BEFORE boot for {{ item.binary }}"
      community.routeros.command:
        commands:
          - >-
            /ip tftp print detail without-paging where
            req-filename="{{ item.binary }}"
      register: _pxe_tftp_pre
      changed_when: false
      delegate_to: rb5009.igou.systems

    - name: "Extract pre-boot hit count"
      ansible.builtin.set_fact:
        _pxe_hits_pre: "{{ _pxe_tftp_pre.stdout[0] | regex_search('hits=\\d+') | default('hits=0') | regex_replace('^hits=', '') | int }}"

    - name: "Apply diskless PXE-boot VM ({{ _vm_name }})"
      kubernetes.core.k8s:
        state: present
        validate_certs: false
        namespace: "{{ pxe_test_namespace }}"
        definition: "{{ lookup('ansible.builtin.template', 'vm.yaml.j2') | from_yaml }}"
        wait: true
        wait_condition:
          type: Ready
          status: "True"
        wait_timeout: 180

    - name: "Look up DHCP lease IP for {{ _vm_name }}"
      ansible.builtin.include_tasks: _dhcp_lease_lookup.yml

    - name: "Pause to let iPXE complete its boot attempt ({{ _vm_name }})"
      ansible.builtin.pause:
        seconds: "{{ _wait }}"

    - name: "Snapshot rb5009 TFTP hits AFTER boot for {{ item.binary }}"
      community.routeros.command:
        commands:
          - >-
            /ip tftp print detail without-paging where
            req-filename="{{ item.binary }}"
      register: _pxe_tftp_post
      changed_when: false
      delegate_to: rb5009.igou.systems

    - name: "Extract post-boot hit count"
      ansible.builtin.set_fact:
        _pxe_hits_post: "{{ _pxe_tftp_post.stdout[0] | regex_search('hits=\\d+') | default('hits=0') | regex_replace('^hits=', '') | int }}"

    - name: "Assert TFTP hits incremented for {{ item.binary }}"
      ansible.builtin.assert:
        that:
          - (_pxe_hits_post | int) > (_pxe_hits_pre | int)
        fail_msg: >-
          VM {{ _vm_name }} did not fetch {{ item.binary }} from rb5009
          within {{ _wait }}s. hits {{ _pxe_hits_pre }} ->
          {{ _pxe_hits_post }}. Check the VMI status, the DHCP offer to
          its MAC (`/log print where topics~"dhcp"` on rb5009), and that
          the matcher table still routes option-93 to the right binary.

    - name: "Verify HTTP fetch for {{ _vm_name }}"
      ansible.builtin.include_tasks: _verify_http.yml
      vars:
        vm_name: "{{ _vm_name }}"
        vm_mac: "{{ _vm_mac }}"
        vm_ip: "{{ _vm_ip }}"
        expected_fetch_path: "{{ _resolved.expected_fetch_path }}"
        expected_substring: "{{ _resolved.expected_substring }}"
        case_start_seconds: "{{ ((lookup('pipe','date -u +%s') | int) - (_case_start_epoch | int)) + 5 }}"

  always:
    - name: "Delete test VM ({{ _vm_name }})"
      kubernetes.core.k8s:
        state: absent
        validate_certs: false
        api_version: kubevirt.io/v1
        kind: VirtualMachine
        namespace: "{{ pxe_test_namespace }}"
        name: "{{ _vm_name }}"
        wait: true
        wait_timeout: 120
```

- [ ] **Step 2: Run the playbook in serial mode and verify all 4 cases pass**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml
```

Default mode is `pxe_test_parallel: false` so this exercises the serial path.

Expected: 4 cases run, each with a TFTP-hits assertion (existing) AND an HTTP-verify assertion (new). All pass. The playbook completes with `failed=0`.

If a case fails on the HTTP-verify assertion: read the failure message — it tells you whether the access log saw the wrong path, didn't see the IP at all, or the substring check tripped.

- [ ] **Step 3: Demonstrate the HTTP-verify failure mode**

To prove the new assertion can detect a regression, temporarily corrupt the smoke pin (or rename the file on truenas):

```bash
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.command \
  -a 'mv /mnt/ssd/containers/netbootxyz/config/menus/host/MAC-020000505801.ipxe /mnt/ssd/containers/netbootxyz/config/menus/host/MAC-020000505801.ipxe.bak' \
  -b
```

Then run only the BIOS-pinned case:
```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e '{"pxe_test_arches": [{"name": "pxe-test-bios-pinned", "arch": "bios", "binary": "netboot.xyz.kpxe", "mac": "02:00:00:50:58:01"}]}'
```

Expected: the new "Preflight -- assert each smoke pin body..." catches it BEFORE any VM boots — the pin file is missing, preflight fails with a 404. (This is the design intent: cheap static check fails fast.)

Restore the file:
```bash
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.command \
  -a 'mv /mnt/ssd/containers/netbootxyz/config/menus/host/MAC-020000505801.ipxe.bak /mnt/ssd/containers/netbootxyz/config/menus/host/MAC-020000505801.ipxe' \
  -b
```

Re-run normally; expect pass.

- [ ] **Step 4: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_arch_test.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: serial mode asserts HTTP-side fetch per case

Each case now also verifies that the netbootxyz access log shows the
VM's leased IP fetching the expected path -- catches the iPXE -> nbxyz
chain regression that the existing TFTP-hits check cannot see.
EOF
)"
```

---

## Task 8: Wire HTTP verification into parallel mode (`test_netboot_pxe.yml`)

**Files:**
- Modify: `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml`

- [ ] **Step 1: Replace the parallel block**

In `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml`, locate the block named `Smoke-test architectures in parallel` (the `when: pxe_test_parallel` block, around line ~150 in the existing file). Replace the entire block (from `- name: Smoke-test architectures in parallel` through the matching `always:` block, inclusive) with:

```yaml
    - name: Smoke-test architectures in parallel
      when: pxe_test_parallel
      block:
        - name: Capture case-start epoch (parallel mode, shared)
          ansible.builtin.set_fact:
            _pxe_parallel_start_epoch: "{{ lookup('pipe','date -u +%s') | int }}"

        - name: Snapshot rb5009 TFTP hits BEFORE boot (all)
          community.routeros.command:
            commands:
              - >-
                /ip tftp print detail without-paging where
                req-filename="{{ item.binary }}"
          register: _pxe_tftp_pre_all
          changed_when: false
          delegate_to: rb5009.igou.systems
          loop: "{{ pxe_test_arches }}"
          loop_control:
            label: "{{ item.name | default('pxe-test-' ~ item.arch) }}"

        - name: Apply all diskless PXE-boot VMs (no per-VM wait)
          kubernetes.core.k8s:
            state: present
            validate_certs: false
            namespace: "{{ pxe_test_namespace }}"
            definition: "{{ lookup('ansible.builtin.template', 'vm.yaml.j2') | from_yaml }}"
            wait: false
          loop: "{{ pxe_test_arches }}"
          loop_control:
            label: "{{ item.name | default('pxe-test-' ~ item.arch) }}"

        - name: Wait for all VMs to reach Ready
          kubernetes.core.k8s_info:
            api_version: kubevirt.io/v1
            kind: VirtualMachine
            namespace: "{{ pxe_test_namespace }}"
            name: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
            validate_certs: false
            wait: true
            wait_condition:
              type: Ready
              status: "True"
            wait_timeout: 180
          loop: "{{ pxe_test_arches }}"
          loop_control:
            label: "{{ item.name | default('pxe-test-' ~ item.arch) }}"

        - name: Look up DHCP lease IP for every VM (parallel)
          ansible.builtin.include_tasks: _dhcp_lease_lookup.yml
          vars:
            _vm_name: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
            _vm_mac: "{{ item.mac | default('') | lower }}"
          loop: "{{ pxe_test_arches }}"
          loop_control:
            label: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
          register: _pxe_parallel_ip_results

        - name: Build {vm_name -> ip} map for the parallel batch
          ansible.builtin.set_fact:
            _pxe_parallel_ip_map: >-
              {{ _pxe_parallel_ip_map | default({})
                 | combine({ (item.item.name | default('pxe-test-' ~ item.item.arch)):
                             item.ansible_facts._vm_ip }) }}
          loop: "{{ _pxe_parallel_ip_results.results }}"
          loop_control:
            label: "{{ item.item.name | default('pxe-test-' ~ item.item.arch) }}"

        - name: Pause to let iPXE complete its boot attempts (longest effective wait)
          ansible.builtin.pause:
            seconds: >-
              {{ pxe_test_arches
                 | map(attribute='boot_wait_seconds', default=pxe_test_boot_wait_seconds)
                 | map('int') | max }}

        - name: Snapshot rb5009 TFTP hits AFTER boot (all)
          community.routeros.command:
            commands:
              - >-
                /ip tftp print detail without-paging where
                req-filename="{{ item.binary }}"
          register: _pxe_tftp_post_all
          changed_when: false
          delegate_to: rb5009.igou.systems
          loop: "{{ pxe_test_arches }}"
          loop_control:
            label: "{{ item.name | default('pxe-test-' ~ item.arch) }}"

        - name: Assert TFTP hits incremented for each binary
          ansible.builtin.assert:
            that:
              - (_pxe_post_hits | int) > (_pxe_pre_hits | int)
            fail_msg: >-
              VM {{ item.0.item.name | default('pxe-test-' ~ item.0.item.arch) }}
              did not fetch {{ item.0.item.binary }} from rb5009. hits
              {{ _pxe_pre_hits }} -> {{ _pxe_post_hits }}. Check the VMI
              status, the DHCP offer to its MAC (`/log print where
              topics~"dhcp"` on rb5009), and that the matcher table
              still routes option-93 to the right binary.
          vars:
            _pxe_pre_hits: "{{ item.0.stdout[0] | regex_search('hits=\\d+') | default('hits=0') | regex_replace('^hits=', '') | int }}"
            _pxe_post_hits: "{{ item.1.stdout[0] | regex_search('hits=\\d+') | default('hits=0') | regex_replace('^hits=', '') | int }}"
          loop: "{{ _pxe_tftp_pre_all.results | zip(_pxe_tftp_post_all.results) | list }}"
          loop_control:
            label: "{{ item.0.item.name | default('pxe-test-' ~ item.0.item.arch) }}"

        - name: Verify HTTP fetch for every VM (parallel)
          ansible.builtin.include_tasks: _verify_http.yml
          vars:
            _vm_name_inner: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
            _resolved: "{{ _pxe_resolved_cases | selectattr('name', 'equalto', _vm_name_inner) | first }}"
            vm_name: "{{ _vm_name_inner }}"
            vm_mac: "{{ item.mac | default('') | lower }}"
            vm_ip: "{{ _pxe_parallel_ip_map[_vm_name_inner] }}"
            expected_fetch_path: "{{ _resolved.expected_fetch_path }}"
            expected_substring: "{{ _resolved.expected_substring }}"
            case_start_seconds: "{{ ((lookup('pipe','date -u +%s') | int) - (_pxe_parallel_start_epoch | int)) + 5 }}"
          loop: "{{ pxe_test_arches }}"
          loop_control:
            label: "{{ item.name | default('pxe-test-' ~ item.arch) }}"

      always:
        - name: Delete all test VMs
          kubernetes.core.k8s:
            state: absent
            api_version: kubevirt.io/v1
            kind: VirtualMachine
            namespace: "{{ pxe_test_namespace }}"
            name: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
            validate_certs: false
            wait: true
            wait_timeout: 120
          loop: "{{ pxe_test_arches }}"
          loop_control:
            label: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
```

Note: the existing parallel block also has a `delegate_to: "{{ pxe_test_router }}"` on the routeros tasks. The default for `pxe_test_router` is `rb5009.igou.systems`. To match the serial-mode style being introduced (literal hostname), use `delegate_to: rb5009.igou.systems` directly. If preserving the var-driven delegate is preferred, swap the literal back — both work.

- [ ] **Step 2: Run the playbook in parallel mode**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e 'pxe_test_parallel=true'
```

Expected: 4 VMs apply concurrently, all reach Ready, all leases looked up, single 180s pause, both TFTP-hits AND HTTP-verify assertions run for each. All pass. Playbook reports `failed=0`.

- [ ] **Step 3: Re-run in serial mode to confirm no regression**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml
```

Expected: same end state, sequential per-case execution, all assertions pass.

- [ ] **Step 4: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: parallel mode asserts HTTP-side fetch per case

Lease lookups happen once per VM after batch Ready, before the long
pause. The shared --since marker captures the whole window; per-VM-IP
grep narrows the access log to each case. Same assertions as serial.
EOF
)"
```

---

## Task 9: Lint, end-to-end verification, header-comment update

**Files:**
- Modify: `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml` (header comment)

- [ ] **Step 1: ansible-lint**

```bash
ansible-lint --profile=production playbooks/kubevirt/test_netboot_pxe/
```

Expected: zero errors. Fix any warnings before proceeding.

- [ ] **Step 2: yamllint**

```bash
yamllint playbooks/kubevirt/test_netboot_pxe/
```

Expected: zero errors.

- [ ] **Step 3: Update the header comment in `test_netboot_pxe.yml`**

The existing header includes:
```
# Iteration 1 scope:
#   * Verifies the binary fetch via TFTP hit counter delta. Does NOT
#     verify the iPXE -> TrueNAS chainload; that needs console
#     scraping, which is intentionally deferred.
```

Replace this paragraph with:

```
# Verification (two layers):
#   * TFTP hit counter on rb5009 increments for the expected binary
#     (catches DHCP / matcher / TFTP regressions).
#   * netbootxyz HTTP access log shows the VM's leased IP GET'd the
#     expected path: /menus/host/MAC-<hexraw>.ipxe for pinned MACs,
#     /menu.ipxe for random-MAC fall-through cases (catches the
#     iPXE -> netbootxyz chain regression). Substring assertions on
#     pinned bodies catch deploy_assets.yml drift.
#   Per-entry expected_fetch / expected_substring overrides are
#   honoured when set; otherwise resolved from inventory's
#   netboot_host_pins (see the vars block + _preflight.yml).
```

The "Namespace selector label" paragraph and the example-invocation paragraph below it are unchanged.

- [ ] **Step 4: Final end-to-end runs (both modes)**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml

ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e 'pxe_test_parallel=true'
```

Expected: both runs report `failed=0` and `unreachable=0`. The serial run takes ~12 minutes (4 cases × ~3 min each); the parallel run takes ~5 minutes (single 180s pause + setup + assertion phase).

- [ ] **Step 5: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: refresh header comment for headless verification

The 'console scraping intentionally deferred' caveat no longer applies:
the HTTP access-log assertion replaces it with a headless equivalent.
EOF
)"
```

---

## Self-review summary (against the spec)

**Spec coverage check** (each item in the spec maps to one or more tasks):

- Goal: assert per-host pin / main-menu fetch headlessly → **Tasks 6, 7, 8**.
- Goal: detect three regression classes (stale pin, no chain, wrong fall-through) → **Task 3** (stale pin via preflight substring), **Task 6** (no chain via positive log assertion), **Task 6** (wrong fall-through via negative `host/MAC-*` assertion).
- Goal: leave reusable primitives → **Tasks 5 (DHCP lookup), 6 (log grep + uri assertion)**. `mac_to_pin_path` is a pure jinja expression embedded where used (no separate file justified for a one-line transform).
- Goal: honour block/always cleanup contract → **Task 7** keeps the existing `block:`/`always:` shape; the new HTTP-verify step lives inside `block:` so a failure still tears down via `always:`.
- Goal: serial AND parallel mode → **Tasks 7 and 8** respectively.
- Non-goal: boot-flip helper → not implemented; spec is explicit and the plan honours that.
- Non-goal: fragment-execution proof → not implemented (no virtctl, no probe URL).
- Non-goal: real-host pin booting → `pxe_test_arches` default unchanged (still only the two smoke pins).
- Non-goal: inventory schema changes → no edits to `igou-inventory/`.
- Architecture: preflight + per-case + always-cleanup → reflected in Tasks 2-4 (preflight) + 5-8 (per-case).
- Test-case schema two new optional fields → introduced in **Task 4** with default + override semantics; substring map default in **Task 3**.
- Verification primitives 1-4 → primitive 1 (mac→pin path) is inlined as Jinja in Tasks 3 & 4 & 6; primitive 2 (assert_pin_file_served) is the preflight URI step (Task 3) reused by Task 6 substring branch; primitive 3 (DHCP lease lookup) is Task 5; primitive 4 (log grep) is Task 6.
- Edge cases (container name spike, lease retry budget, log lookback collisions, time skew, 200-but-wrong-body, never-fetched) → Task 1 spike; Task 5 retry config; relative-seconds `--since` in Task 6; preflight substring check in Task 3; negative log assertion in Task 6.
- Risks (container name unknown, time skew, log rotation, 200-on-default-page, drift) → Task 1, Task 6 (relative since), bounded `pause` (already in playbook), Task 3, Task 3 substring.
- Testing strategy (4 default cases pass, rename-pin-file, corrupt body, stop nbxyz, idempotent re-runs, lints) → Tasks 7 step 3 (rename), Task 3 step 5 (corrupt-body via substring override), Task 2 step 4 (down service), Tasks 7 step 2 + 8 step 3 (re-runs), Task 9 (lints).

**Placeholder scan:** none. Each step has the actual file content, the actual command, and the expected output.

**Type/identifier consistency check:**
- Fact names: `_pxe_preflight_menu`, `_pxe_preflight_bodies`, `_pxe_pinned_macs`, `_pxe_preflight_pin_macs`, `_pxe_preflight_pin_results`, `_pxe_resolved_cases`, `_pxe_tftp_pre`, `_pxe_tftp_post`, `_pxe_hits_pre`, `_pxe_hits_post`, `_case_start_epoch`, `_pxe_parallel_start_epoch`, `_pxe_parallel_ip_map`, `_pxe_parallel_ip_results`, `_pxe_lease_query`, `_pxe_nbxyz_logs`, `_pxe_fetched_paths`, `_pxe_served_body`, `_pxe_fresh_body`, `_vm_name`, `_vm_mac`, `_vm_ip`, `_resolved`, `_wait`. Cross-referenced across tasks; all consistent.
- Resolved-case fields: `name`, `arch`, `binary`, `mac`, `has_mac`, `is_pinned`, `expected_fetch_path`, `expected_substring`. Used identically in Tasks 4, 7, 8.
- Body cache key scheme: `'menu'` for menu.ipxe; `'pin:' ~ vm_mac` (lowercase MAC with colons) for per-host pins. Consistent across Tasks 2, 3, 6.
- Path constant: `/menus/host/MAC-<hexraw>.ipxe` — produced identically in Tasks 3 (URL fetch), 4 (resolution), 6 (negative assert regex anchor).

If any inconsistency surfaces during execution, fix it inline rather than mid-stream restructuring the plan.
