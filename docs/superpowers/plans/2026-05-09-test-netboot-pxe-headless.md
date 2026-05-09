# Headless verification for `test_netboot_pxe` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `playbooks/kubevirt/test_netboot_pxe/` so each smoke-test VM verifies — without console scraping — that the netbootxyz nginx access log shows the expected per-host HTTP request (200 if the MAC is pinned, 404 if it isn't).

**Architecture:** Preflight stage validates netbootxyz HTTP reachability and pinned-MAC pin-file content (substring check). Per-case verification snapshots the nginx access log line count before the VM boots, reads the VMI to learn the actual MAC, queries rb5009 for the leased IP, and asserts that exactly one new access log line shows `GET /menus/host/MAC-<hexraw>.ipxe` with the expected status (200 or 404) from the VM's IP. Two new shared task files (`_dhcp_lease_lookup.yml`, `_verify_http.yml`) keep serial and parallel paths DRY.

**Tech Stack:** Ansible (kubernetes.core, community.routeros, ansible.builtin.uri/command), `docker exec` over the existing TrueNAS SSH connection, RouterOS DHCP lease API.

---

## Reference material

- **Spec:** `docs/superpowers/specs/2026-05-09-test-netboot-pxe-headless-design.md` — read this first.
- **Existing playbook tree:** `playbooks/kubevirt/test_netboot_pxe/{test_netboot_pxe.yml,_arch_test.yml,vm.yaml.j2}`.
- **Inventory:** `igou-inventory/group_vars/all/netboot.yml` defines `netboot_host_pins`, `netbootxyz_host`, `netbootxyz_self_url`. **Read-only** from this plan's perspective.
- **Smoke pin fragment substrings already in inventory:**
  - MAC `02:00:00:50:58:01` body contains literal `=== pxe-test smoke pin: bios`
  - MAC `02:00:00:50:58:02` body contains literal `=== pxe-test smoke pin: uefi-x64`
- **Inventory file path:** `igou-inventory/inventory.yaml`. All commands assume CWD is `/workspace/igou-ansible`.

## Spike outcome (Task 1, completed 2026-05-09)

These three values are the only outputs of the spike. They are referenced verbatim in Task 5.

```
runtime         = docker            (TrueNAS SCALE uses Docker, not podman)
container_name  = ix-netbootxyz-netbootxyz-1   (TrueCharts naming)
access_log_path = /config/log/nginx/access.log (inside the container)
read_strategy   = wc -l snapshot before; tail -n +<N> after
                  (nginx logs to a file, not stdout — `docker logs` only
                  carries dnsmasq-tftp lines, not nginx access lines)
http_root_probe = GET netbootxyz_self_url/ → 200 (asset root index;
                  /menu.ipxe is NOT HTTP-served — that's TFTP)
```

## Conventions

- **Run from:** `/workspace/igou-ansible`.
- **Run-the-playbook command:** `ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml -i igou-inventory/inventory.yaml`.
- **Linters:** `ansible-lint --profile=production playbooks/kubevirt/test_netboot_pxe/` and `yamllint playbooks/kubevirt/test_netboot_pxe/`.
- **YAML style** (matches the rest of the repo):
  - `---` at file top, 2-space indent.
  - YAML 1.2 booleans (`true`/`false`).
  - Fact names prefixed `_pxe_…` to scope them.
- **Connection assumptions** (already configured by group_vars):
  - `rb5009.igou.systems` — `community.routeros.*` works (existing TFTP-hits step proves it).
  - `truenas` — SSH with `become: true` works (existing `playbooks/truenas/*` use it).
- **Each task ends with a commit.**

## Files Created/Modified/Deleted

**Create:**
- `playbooks/kubevirt/test_netboot_pxe/_preflight.yml`
- `playbooks/kubevirt/test_netboot_pxe/_dhcp_lease_lookup.yml`
- `playbooks/kubevirt/test_netboot_pxe/_verify_http.yml`

**Modify:**
- `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml`
- `playbooks/kubevirt/test_netboot_pxe/_arch_test.yml`

**Delete:** none.

## Pre-flight assumptions

Verify before Task 2:

- `KUBECONFIG` is set; `ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml -i igou-inventory/inventory.yaml` runs the existing playbook end-to-end successfully (4 cases pass on TFTP-hits assertions). If this is broken, fix it BEFORE adding new assertions.
- `playbooks/netboot/deploy_assets.yml` has been run recently enough that the rendered `host/MAC-020000505801.ipxe` and `host/MAC-020000505802.ipxe` files exist on the TrueNAS netbootxyz container with bodies containing the smoke pin substrings.
- Static DHCP leases on rb5009 already exist for MACs `02:00:00:50:58:01` and `02:00:00:50:58:02` (referenced in the existing playbook header).

If any of these is not true, surface it before starting Task 2.

---

## Task 1: Spike — DONE

The spike completed on 2026-05-09. Outcomes are recorded in the "Spike outcome" section at the top of this plan and embedded into `_verify_http.yml` in Task 5. **No further action.**

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
#   1. HTTP-probe netbootxyz_self_url/ (the asset root) -- fails fast
#      if nginx is down. NB: /menu.ipxe is NOT HTTP-served in this
#      deployment; iPXE TFTP-fetches it from the netbootxyz container's
#      built-in dnsmasq.
#   2. (Task 3) Build set of pinned MACs from netboot_host_pins,
#      HTTP-fetch every smoke pin file referenced by pxe_test_arches,
#      assert each body contains its expected substring, cache bodies.
#
# All preflight tasks delegate_to: localhost -- no TrueNAS or rb5009
# contact at this stage.

- name: Preflight -- netbootxyz HTTP root is reachable
  ansible.builtin.uri:
    url: "{{ netbootxyz_self_url }}/"
    status_code: 200
    timeout: 10
  register: _pxe_preflight_root
  delegate_to: localhost
  changed_when: false
```

- [ ] **Step 2: Wire `_preflight.yml` into `test_netboot_pxe.yml`**

Modify `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml`. Locate the `tasks:` block. Insert the include as the **first** task in `tasks:`, before the existing CUDN read:

```yaml
  tasks:
    - name: Preflight -- netbootxyz HTTP-side checks
      ansible.builtin.include_tasks: _preflight.yml

    # --- Pre-flight (idempotent, never destructive) --------------------------

    - name: Read each ClusterUserDefinedNetwork referenced by pxe_test_arches
      ...
```

The existing CUDN-read block stays exactly where it is.

- [ ] **Step 3: Run the playbook end-to-end and confirm preflight runs**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml
```

Expected: the first task is `Preflight -- netbootxyz HTTP root is reachable`. The full playbook still passes end-to-end (TFTP-hits checks unchanged).

- [ ] **Step 4: Demonstrate the failure mode**

Override `netbootxyz_self_url` to a known-bad URL:

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e 'netbootxyz_self_url=http://127.0.0.1:1'
```

Expected: the preflight task fails with a connection error. No VMs applied. Non-zero return code.

- [ ] **Step 5: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_preflight.yml \
  playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: add preflight HTTP probe of netbootxyz_self_url

Fails fast if nginx is down before any VM is applied. First step of
the headless verification design (see
docs/superpowers/specs/2026-05-09-test-netboot-pxe-headless-design.md).

The probe targets the asset root (/), not /menu.ipxe -- the latter is
served via TFTP, not HTTP, in this deployment.
EOF
)"
```

---

## Task 3: Preflight — pinned-MAC set + smoke-pin substring assertions

**Files:**
- Modify: `playbooks/kubevirt/test_netboot_pxe/_preflight.yml`
- Modify: `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml` (add the `pxe_test_substring_defaults` map to `vars:`)

- [ ] **Step 1: Add the substring defaults map to the playbook vars**

In `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml`, in the `vars:` block, just below `pxe_test_parallel: false` and above the `pxe_test_arches:` list comment, insert:

```yaml
    # Default substring to grep for in each smoke pin's served body,
    # keyed by lowercase MAC. Drives only the preflight static check;
    # per-case substring assertion is dropped because preflight already
    # validates the body. Adding a new pinned smoke entry?  Add the
    # MAC -> substring pair here.
    pxe_test_substring_defaults:
      "02:00:00:50:58:01": "=== pxe-test smoke pin: bios"
      "02:00:00:50:58:02": "=== pxe-test smoke pin: uefi-x64"
```

- [ ] **Step 2: Extend `_preflight.yml` with pinned-MAC discovery**

Append to `_preflight.yml`:

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

- name: Preflight -- collect pinned MACs referenced by pxe_test_arches
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

Append to `_preflight.yml`:

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
```

- [ ] **Step 4: Run the playbook and verify preflight passes**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml
```

Expected: the new preflight tasks fetch two pin files and assert their bodies contain the expected substrings. Rest of the playbook still passes.

- [ ] **Step 5: Demonstrate the failure mode**

Override the substring defaults to values that won't appear:

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e '{"pxe_test_substring_defaults": {"02:00:00:50:58:01": "DEFINITELY-NOT-IN-THE-BODY", "02:00:00:50:58:02": "DEFINITELY-NOT-IN-THE-BODY"}}'
```

Expected: the substring assertion fails for both pins. No VMs applied.

- [ ] **Step 6: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_preflight.yml \
  playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: preflight asserts smoke pin bodies on netbootxyz

Per-MAC substring map catches inventory drift -- if netboot_host_pins
is updated but deploy_assets.yml is not re-run, preflight fails before
any VM is applied. Pinned-MAC set is also derived here for the per-
case 200-vs-404 classifier (Task 6).
EOF
)"
```

---

## Task 4: Add `_dhcp_lease_lookup.yml` — VMI MAC + rb5009 DHCP lease IP

**Files:**
- Create: `playbooks/kubevirt/test_netboot_pxe/_dhcp_lease_lookup.yml`

- [ ] **Step 1: Create the file**

Write with this exact content:

```yaml
---
# Resolve a VM's MAC and IP for the headless smoke test.
#
# Step A reads the VirtualMachineInstance to learn the MAC. KubeVirt
# fills .spec.domain.devices.interfaces[].macAddress in regardless of
# whether the test fixture pinned a MAC or asked KubeVirt to generate
# one -- so this works for both pinned and random cases.
#
# Step B queries rb5009 for the DHCP lease for that MAC. Up to 30s of
# retry budget because iPXE's DHCP exchange can lag VMI Ready by a few
# seconds, especially in parallel mode.
#
# Inputs (task vars expected on caller):
#   _vm_name   for messages and loop labels
#   pxe_test_namespace -- already a play-level var
#
# Outputs (set as facts):
#   _vm_mac    lowercase, with colons (e.g. "02:00:00:50:58:01")
#   _vm_ip     dotted-quad string (e.g. "10.10.9.42")
#
# Failure modes are distinct: an unreadable VMI fails on Step A with a
# k8s-flavoured message; a missing DHCP lease fails on Step C with a
# network-layer-flavoured message. Neither is confused with a
# netbootxyz problem.

- name: "Read VirtualMachineInstance for {{ _vm_name }} to learn its MAC"
  kubernetes.core.k8s_info:
    api_version: kubevirt.io/v1
    kind: VirtualMachineInstance
    namespace: "{{ pxe_test_namespace }}"
    name: "{{ _vm_name }}"
    validate_certs: false
  register: _pxe_vmi_result
  retries: 6
  delay: 5
  until:
    - _pxe_vmi_result.resources | length == 1
    - _pxe_vmi_result.resources[0].spec.domain.devices.interfaces | default([]) | length > 0
    - _pxe_vmi_result.resources[0].spec.domain.devices.interfaces[0].macAddress is defined

- name: "Set _vm_mac fact for {{ _vm_name }}"
  ansible.builtin.set_fact:
    _vm_mac: "{{ _pxe_vmi_result.resources[0].spec.domain.devices.interfaces[0].macAddress | lower }}"

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
      VM {{ _vm_name }} (MAC {{ _vm_mac }}) appears to have a lease
      entry on rb5009 but no address= field could be parsed. Raw:
      {{ _pxe_lease_query.stdout[0] }}
```

- [ ] **Step 2: yamllint**

```bash
yamllint playbooks/kubevirt/test_netboot_pxe/_dhcp_lease_lookup.yml
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_dhcp_lease_lookup.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: add _dhcp_lease_lookup helper

Reads the VMI to learn the MAC (works for both pinned and KubeVirt-
generated cases), then polls rb5009 for the DHCP lease IP. 30s budget.
Wired in by Tasks 6 (serial) and 7 (parallel).
EOF
)"
```

---

## Task 5: Add `_verify_http.yml` — nginx access-log slice + status-code assertion

**Files:**
- Create: `playbooks/kubevirt/test_netboot_pxe/_verify_http.yml`

- [ ] **Step 1: Create the file**

Write with this exact content:

```yaml
---
# Per-case HTTP-side verification for the netboot.xyz smoke test.
# Called from both _arch_test.yml (serial mode) and the inline parallel
# block in test_netboot_pxe.yml.
#
# SPIKE OUTCOME (2026-05-09):
#   runtime         = docker (TrueNAS SCALE uses Docker, not podman)
#   container_name  = ix-netbootxyz-netbootxyz-1   (TrueCharts naming)
#   access_log_path = /config/log/nginx/access.log (inside the container;
#                     nginx is configured to log to a file, not stdout --
#                     `docker logs` only carries dnsmasq-tftp lines)
#   read_strategy   = wc -l snapshot before, tail -n +<N> after
#                     (slice-by-line-count survives clock skew and is
#                     robust regardless of log driver)
#
# Inputs (task vars expected on caller):
#   vm_name              VM name, used in messages
#   vm_mac               lowercase MAC (with colons)
#   vm_ip                dotted-quad IP from DHCP lease
#   expected_status      200 (pinned) or 404 (random) -- the per-case
#                          discriminator. Pinned MAC has a host file;
#                          random MAC's GET hits 404 then iPXE falls
#                          through to the in-binary :main_menu.
#   pre_log_line_count   line count of access.log captured BEFORE the
#                          VM was applied (snapshot in caller).
#
# Output: assertions only; no facts set.

- name: "Read post-boot nginx access log slice for {{ vm_name }}"
  ansible.builtin.command:
    cmd: >-
      docker exec ix-netbootxyz-netbootxyz-1
      sh -c "tail -n +{{ pre_log_line_count | int + 1 }} /config/log/nginx/access.log"
  register: _pxe_log_slice
  changed_when: false
  delegate_to: "{{ netbootxyz_host }}"
  become: true

- name: "Parse access lines into [ip, method, path, status] for {{ vm_name }}"
  ansible.builtin.set_fact:
    _pxe_parsed_lines: >-
      {{ _pxe_log_slice.stdout_lines
         | map('regex_search',
               '^(\S+) - \S+ \[[^\]]+\] \"(\S+) (\S+) [^\"]+\" (\d+) ',
               '\\1', '\\2', '\\3', '\\4')
         | select('truthy')
         | list }}

- name: "Filter access lines to those from {{ vm_name }}'s IP ({{ vm_ip }})"
  ansible.builtin.set_fact:
    _pxe_vm_lines: "{{ _pxe_parsed_lines | selectattr('0', 'equalto', vm_ip) | list }}"

- name: "Compute expected per-host path for {{ vm_name }}"
  ansible.builtin.set_fact:
    _pxe_expected_path: "/menus/host/MAC-{{ vm_mac | regex_replace(':', '') | lower }}.ipxe"

- name: "Find access lines matching the expected GET for {{ vm_name }}"
  ansible.builtin.set_fact:
    _pxe_matching_lines: >-
      {{ _pxe_vm_lines
         | selectattr('1', 'equalto', 'GET')
         | selectattr('2', 'equalto', _pxe_expected_path)
         | list }}

- name: "Assert {{ vm_name }} made at least one request to netbootxyz"
  ansible.builtin.assert:
    that:
      - _pxe_vm_lines | length > 0
    fail_msg: >-
      VM {{ vm_name }} (IP {{ vm_ip }}) made NO HTTP requests to
      netbootxyz. iPXE never reached the chainload step. Check the
      rb5009 TFTP server, the chain target inside the iPXE binary, or
      VLAN reachability between the VM CUDN and {{ netbootxyz_host }}.

- name: "Assert {{ vm_name }} fetched {{ _pxe_expected_path }} exactly once"
  ansible.builtin.assert:
    that:
      - _pxe_matching_lines | length == 1
    fail_msg: >-
      VM {{ vm_name }} (IP {{ vm_ip }}) -- expected exactly one GET
      {{ _pxe_expected_path }}, but the access log slice shows
      {{ _pxe_matching_lines | length }} matches. All lines from this
      VM: {{ _pxe_vm_lines }}.

- name: "Assert {{ vm_name }} got HTTP {{ expected_status }} on {{ _pxe_expected_path }}"
  ansible.builtin.assert:
    that:
      - _pxe_matching_lines[0][3] | int == expected_status | int
    fail_msg: >-
      VM {{ vm_name }} (IP {{ vm_ip }}) hit {{ _pxe_expected_path }} but
      got HTTP {{ _pxe_matching_lines[0][3] }} (expected
      {{ expected_status }}). For a pinned MAC, this means the host
      file is missing or stale -- run playbooks/netboot/deploy_assets.yml
      and check inventory's netboot_host_pins. For a random MAC, this
      means a per-host file exists for what should be an unpinned MAC
      -- check whether that MAC was added to netboot_host_pins by
      mistake, or whether deploy_assets.yml left a stale file behind.
```

- [ ] **Step 2: yamllint**

```bash
yamllint playbooks/kubevirt/test_netboot_pxe/_verify_http.yml
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_verify_http.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: add _verify_http helper

Slices the netbootxyz nginx access log via docker exec tail (since
nginx logs to a file, not stdout). Asserts exactly one GET on the
per-host path with the expected status (200 for pinned, 404 for
random). Status code is the per-case discriminator -- it captures
both classes of regression with a single observable.
EOF
)"
```

---

## Task 6: Wire HTTP verification into serial mode (`_arch_test.yml`)

**Files:**
- Modify: `playbooks/kubevirt/test_netboot_pxe/_arch_test.yml`

The existing per-arch flow becomes:

```
snapshot pre log line count (docker exec wc -l)
TFTP hits BEFORE
apply VM, wait Ready
include _dhcp_lease_lookup.yml -> _vm_mac, _vm_ip
pause for boot
TFTP hits AFTER
assert TFTP hits incremented (existing)
compute expected_status from _vm_mac in _pxe_pinned_macs
include _verify_http.yml
always: delete VM
```

- [ ] **Step 1: Replace `_arch_test.yml` with the augmented version**

Open `_arch_test.yml` and replace its entire contents with:

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
#   2. netbootxyz access log shows the VM's leased IP fetched
#      /menus/host/MAC-<hexraw>.ipxe with HTTP 200 (pinned MAC) or 404
#      (random MAC). See _verify_http.yml.

- name: "Smoke-test {{ _vm_name }}"
  vars:
    _vm_name: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
    _wait: "{{ item.boot_wait_seconds | default(pxe_test_boot_wait_seconds) }}"
  block:
    - name: "Snapshot nginx access log line count BEFORE {{ _vm_name }}"
      ansible.builtin.command:
        cmd: >-
          docker exec ix-netbootxyz-netbootxyz-1
          wc -l /config/log/nginx/access.log
      register: _pxe_log_pre
      changed_when: false
      delegate_to: "{{ netbootxyz_host }}"
      become: true

    - name: "Capture pre log line count for {{ _vm_name }}"
      ansible.builtin.set_fact:
        _pre_log_line_count: "{{ _pxe_log_pre.stdout.split() | first | int }}"

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

    - name: "Resolve VM addressing for {{ _vm_name }}"
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
          its MAC (`/log print where topics~"dhcp"` on rb5009), and
          that the matcher table still routes option-93 to the right
          binary.

    - name: "Verify HTTP fetch for {{ _vm_name }}"
      ansible.builtin.include_tasks: _verify_http.yml
      vars:
        vm_name: "{{ _vm_name }}"
        vm_mac: "{{ _vm_mac }}"
        vm_ip: "{{ _vm_ip }}"
        expected_status: "{{ 200 if (_vm_mac in _pxe_pinned_macs) else 404 }}"
        pre_log_line_count: "{{ _pre_log_line_count }}"

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

- [ ] **Step 2: Run the playbook in serial mode**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml
```

Expected: 4 cases run sequentially. Each: TFTP-hits assertion (existing) + HTTP-verify assertion (new). Pinned cases see status 200; random cases see status 404. All pass; `failed=0`.

If a case fails on HTTP-verify: read the failure message — it differentiates between (a) no HTTP at all from the VM, (b) wrong path, (c) right path wrong status.

- [ ] **Step 3: Demonstrate the failure mode (pin file missing)**

```bash
# Rename the pin file on truenas to simulate stale deployment.
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.command \
  -a 'docker exec ix-netbootxyz-netbootxyz-1 mv /config/menus/host/MAC-020000505801.ipxe /config/menus/host/MAC-020000505801.ipxe.bak' \
  -b
```

Run only the BIOS-pinned case:
```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e '{"pxe_test_arches": [{"name": "pxe-test-bios-pinned", "arch": "bios", "binary": "netboot.xyz.kpxe", "mac": "02:00:00:50:58:01"}]}'
```

Expected: preflight catches it (404 on the pre-fetch) BEFORE any VM boots. The static check fails fast.

Restore:
```bash
ansible truenas -i igou-inventory/inventory.yaml -m ansible.builtin.command \
  -a 'docker exec ix-netbootxyz-netbootxyz-1 mv /config/menus/host/MAC-020000505801.ipxe.bak /config/menus/host/MAC-020000505801.ipxe' \
  -b
```

Re-run normally; expect pass.

- [ ] **Step 4: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/_arch_test.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: serial mode asserts HTTP-side fetch per case

Snapshots the nginx access log line count, applies the VM, resolves
its MAC and DHCP IP, then asserts exactly one GET on
/menus/host/MAC-<hex>.ipxe from the VM's IP with status 200 (pinned)
or 404 (random). Status is the per-case discriminator.
EOF
)"
```

---

## Task 7: Wire HTTP verification into parallel mode (`test_netboot_pxe.yml`)

**Files:**
- Modify: `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml`

- [ ] **Step 1: Replace the parallel block**

Locate the block named `Smoke-test architectures in parallel` (`when: pxe_test_parallel`) and replace it (entire block, including `always:`) with:

```yaml
    - name: Smoke-test architectures in parallel
      when: pxe_test_parallel
      block:
        - name: Snapshot nginx access log line count BEFORE the parallel batch
          ansible.builtin.command:
            cmd: >-
              docker exec ix-netbootxyz-netbootxyz-1
              wc -l /config/log/nginx/access.log
          register: _pxe_log_pre_parallel
          changed_when: false
          delegate_to: "{{ netbootxyz_host }}"
          become: true

        - name: Capture pre log line count (parallel mode, shared)
          ansible.builtin.set_fact:
            _pre_log_line_count_parallel: "{{ _pxe_log_pre_parallel.stdout.split() | first | int }}"

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

        - name: Resolve VM addressing for every VM (parallel)
          ansible.builtin.include_tasks: _dhcp_lease_lookup.yml
          vars:
            _vm_name: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
          loop: "{{ pxe_test_arches }}"
          loop_control:
            label: "{{ item.name | default('pxe-test-' ~ item.arch) }}"
          register: _pxe_parallel_addr_results

        - name: Build {vm_name -> {mac, ip}} map for the parallel batch
          ansible.builtin.set_fact:
            _pxe_parallel_addr_map: >-
              {{ _pxe_parallel_addr_map | default({})
                 | combine({ (item.item.name | default('pxe-test-' ~ item.item.arch)):
                             {'mac': item.ansible_facts._vm_mac,
                              'ip':  item.ansible_facts._vm_ip} }) }}
          loop: "{{ _pxe_parallel_addr_results.results }}"
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
            vm_name: "{{ _vm_name_inner }}"
            vm_mac: "{{ _pxe_parallel_addr_map[_vm_name_inner].mac }}"
            vm_ip: "{{ _pxe_parallel_addr_map[_vm_name_inner].ip }}"
            expected_status: "{{ 200 if (_pxe_parallel_addr_map[_vm_name_inner].mac in _pxe_pinned_macs) else 404 }}"
            pre_log_line_count: "{{ _pre_log_line_count_parallel }}"
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

- [ ] **Step 2: Run in parallel mode**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e 'pxe_test_parallel=true'
```

Expected: 4 VMs apply concurrently, all reach Ready, addressing resolved per-VM, single shared pause, TFTP-hits AND HTTP-verify assertions for each. All pass.

- [ ] **Step 3: Re-run in serial mode (no regression)**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml
```

Expected: same end state, sequential execution, all assertions pass.

- [ ] **Step 4: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: parallel mode asserts HTTP-side fetch per case

Shared pre-snapshot of the nginx access log line count covers the
whole batch; per-VM-IP filtering inside _verify_http.yml keeps cases
independent. Same per-case discriminator as serial mode (200 pinned /
404 random).
EOF
)"
```

---

## Task 8: Lint, end-to-end verification, header-comment update

**Files:**
- Modify: `playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml` (header comment only)

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
#   * netbootxyz nginx access log shows the VM's leased IP made
#     exactly one GET /menus/host/MAC-<hexraw>.ipxe -- with status 200
#     for pinned MACs (host file served) or 404 for random MACs (file
#     absent, iPXE falls through to in-binary :main_menu). Single
#     observable, two interpretations; status code is the per-case
#     discriminator. Substring assertions on pinned bodies catch
#     deploy_assets.yml drift at preflight.
#   No per-entry override fields; expectations are fully derived from
#   inventory's netboot_host_pins (see _preflight.yml + _verify_http.yml).
```

- [ ] **Step 4: Final end-to-end runs (both modes)**

```bash
ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml

ansible-playbook playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml \
  -i igou-inventory/inventory.yaml \
  -e 'pxe_test_parallel=true'
```

Expected: both runs report `failed=0`, `unreachable=0`. Serial: ~12 minutes; parallel: ~5 minutes.

- [ ] **Step 5: Commit**

```bash
git add playbooks/kubevirt/test_netboot_pxe/test_netboot_pxe.yml
git commit -m "$(cat <<'EOF'
test_netboot_pxe: refresh header comment for headless verification

The 'console scraping intentionally deferred' caveat no longer applies:
the nginx access-log status-code assertion replaces it with a headless
equivalent.
EOF
)"
```

---

## Self-review summary (against the spec)

**Spec coverage check:**

- Goal: assert per-host pin / main-menu fetch headlessly via status code → Tasks 5, 6, 7.
- Goal: detect three regression classes → preflight substring (Task 3), positive log slice + status assertion (Task 5), 404-vs-200 discriminator (Task 5).
- Goal: leave reusable primitives → Tasks 4 (DHCP+VMI), 5 (log slice + parser).
- Goal: block/always cleanup → preserved in Task 6's serial block and Task 7's parallel block.
- Goal: serial AND parallel → Tasks 6 and 7.
- Non-goal: boot-flip helper → not implemented.
- Non-goal: fragment-execution proof → not implemented (no virtctl, no probe URL).
- Non-goal: real-host pin booting → `pxe_test_arches` default unchanged.
- Non-goal: inventory schema changes → no edits to `igou-inventory/`.
- Architecture: preflight + per-case + always-cleanup → Tasks 2-3 (preflight), 4-7 (per-case).
- Test-case schema (no override fields) → no schema changes; substring map default added in Task 3.
- Verification primitives → primitive 1 (mac→pin path) inlined in Tasks 3, 5; primitive 2 (assert_pin_file_served) in Task 3 preflight; primitive 3 (DHCP lease + VMI MAC) in Task 4; primitive 4 (read_nbxyz_access_lines_since) in Task 5.
- Edge cases: container name baked from spike (Task 5); DHCP retry (Task 4); slice-by-line-count survives skew (Task 5); 200-but-wrong-path (Task 5 negative arms); never-fetched (Task 5 first assertion).
- Risks → all mitigated in tasks above.
- Testing strategy → Task 6 step 3 (rename pin), Task 3 step 5 (corrupt body via substring override), Task 2 step 4 (down service), Tasks 6-7 (re-runs), Task 8 (lints).

**Placeholder scan:** none.

**Identifier consistency:**
- Fact names: `_pxe_preflight_root`, `_pxe_pinned_macs`, `_pxe_preflight_pin_macs`, `_pxe_preflight_pin_results`, `_pxe_tftp_pre`, `_pxe_tftp_post`, `_pxe_hits_pre`, `_pxe_hits_post`, `_pxe_log_pre`, `_pre_log_line_count`, `_pxe_log_pre_parallel`, `_pre_log_line_count_parallel`, `_pxe_log_slice`, `_pxe_parsed_lines`, `_pxe_vm_lines`, `_pxe_expected_path`, `_pxe_matching_lines`, `_pxe_lease_query`, `_pxe_vmi_result`, `_pxe_parallel_addr_results`, `_pxe_parallel_addr_map`, `_vm_name`, `_vm_mac`, `_vm_ip`, `_wait`. All consistent across tasks.
- `_dhcp_lease_lookup.yml` outputs `_vm_mac` and `_vm_ip` as facts; both serial (Task 6) and parallel (Task 7) consume identically.
- `_verify_http.yml` inputs: `vm_name`, `vm_mac`, `vm_ip`, `expected_status`, `pre_log_line_count`. Used identically in serial and parallel call sites.
- Path constant: `/menus/host/MAC-<hexraw>.ipxe` — produced identically in Tasks 3 (URL fetch), 5 (computed expectation).
- Container literal: `ix-netbootxyz-netbootxyz-1` appears in Tasks 5, 6, 7. Identical spelling.
