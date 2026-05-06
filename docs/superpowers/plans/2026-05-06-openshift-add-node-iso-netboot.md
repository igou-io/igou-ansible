# OpenShift add-node ISO netboot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `playbooks/openshift/add_node_iso.yml` to automate `oc adm node-image create --pxe` against an existing OpenShift cluster and deliver the resulting PXE assets to TrueNAS netbootxyz.

**Architecture:** Single playbook with two plays. Play 1 runs locally on the cluster host (`connection: local`), wipes a per-cluster work dir, fetches the cluster's pull secret via `oc`, renders `nodes-config.yaml` from inventory using a Jinja template, runs `oc adm node-image create --pxe`, and (under the `monitor` tag) runs `oc adm node-image monitor`. Play 2 runs on the `truenas` host, rsyncs boot artifacts into the existing netbootxyz `/assets/<cluster>-add-node/` directory, and writes one iPXE script per worker (named by MAC) into `/config/menus/`.

**Tech Stack:** Ansible (ansible-navigator + EE), Jinja2 templates, `oc` CLI, `ansible.posix.synchronize`, `ansible.builtin.command`, `ansible.builtin.assert`.

**Reference spec:** `docs/superpowers/specs/2026-05-06-openshift-add-node-iso-netboot-design.md`

**Pre-existing patterns to follow:** `playbooks/openshift/agent-install/deploy_pxe_assets.yml` (mirror its two-play structure, work-dir convention, TrueNAS rsync/copy steps).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `playbooks/openshift/add_node_iso.yml` | Create | The two-play playbook |
| `playbooks/openshift/templates/nodes-config.yaml.j2` | Create | Renders `nodes-config.yaml` from inventory |

No changes to `igou-inventory/` are part of this plan — the operator populates `openshift_workers_<cluster>` and the new cluster-host vars at use time. The inventory schema is documented inline in the playbook header.

---

## Task 1: Scaffold playbook skeleton and template directory

**Files:**
- Create: `playbooks/openshift/templates/nodes-config.yaml.j2` (placeholder)
- Create: `playbooks/openshift/add_node_iso.yml` (skeleton)

- [ ] **Step 1: Create the template directory and a placeholder template**

```bash
mkdir -p playbooks/openshift/templates
```

Write `playbooks/openshift/templates/nodes-config.yaml.j2` with a placeholder (real content lands in Task 2):

```jinja
---
# Placeholder — see Task 2.
hosts: []
```

- [ ] **Step 2: Write the playbook skeleton**

Write `playbooks/openshift/add_node_iso.yml`:

```yaml
---
# Add worker nodes to an existing OpenShift cluster by generating PXE
# assets via `oc adm node-image create --pxe` and copying them to
# TrueNAS netbootxyz.
#
# Source procedure:
#   https://github.com/openshift/openshift-docs/blob/main/nodes/nodes/nodes-nodes-adding-node-iso.adoc
#
# Operator workflow:
#   1. Add worker(s) to inventory under group `openshift_workers_<cluster>`
#      with at minimum `openshift_add_node_mac`. Add
#      `openshift_add_node_network_config` (nmstate) for static IPs.
#   2. Set on the cluster host (e.g., host_vars/ocp.yml):
#        openshift_add_node_arch: x86_64
#        openshift_add_node_boot_artifacts_base_url: http://<netboot-host>/<cluster>-add-node/
#   3. export KUBECONFIG=<path-to-cluster-kubeconfig>
#   4. ansible-navigator run playbooks/openshift/add_node_iso.yml \
#        -i igou-inventory/inventory.yaml \
#        -e target_cluster=<cluster>
#   5. PXE-boot the worker.
#   6. Optionally: re-run with `--tags monitor` to watch the join.
#   7. Approve any pending CSRs manually:
#        oc get csr
#        oc adm certificate approve <name>

- name: Generate OpenShift add-node PXE assets
  hosts: "{{ target_cluster }}"
  connection: local
  gather_facts: true
  vars:
    openshift_add_node_work_dir: "{{ ansible_env.HOME }}/openshift-add-node/{{ target_cluster }}"
    openshift_add_node_arch: "{{ openshift_add_node_arch | default('x86_64') }}"
  tasks:
    - name: Placeholder for Play 1
      ansible.builtin.debug:
        msg: "Play 1 stub"

- name: Deliver PXE assets to TrueNAS netbootxyz
  hosts: truenas
  become: true
  gather_facts: false
  tasks:
    - name: Placeholder for Play 2
      ansible.builtin.debug:
        msg: "Play 2 stub"
```

- [ ] **Step 3: Run syntax-check**

```bash
ansible-playbook --syntax-check \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  playbooks/openshift/add_node_iso.yml
```

Expected: `playbook: playbooks/openshift/add_node_iso.yml` (no errors).

- [ ] **Step 4: Run yamllint and ansible-lint on the new files**

```bash
yamllint playbooks/openshift/add_node_iso.yml playbooks/openshift/templates/nodes-config.yaml.j2
ansible-lint --profile=production playbooks/openshift/add_node_iso.yml
```

Expected: clean (or only warnings unrelated to these files). Fix any errors before continuing.

- [ ] **Step 5: Commit**

```bash
git add playbooks/openshift/add_node_iso.yml playbooks/openshift/templates/nodes-config.yaml.j2
git commit -m "Scaffold OpenShift add-node ISO playbook"
```

---

## Task 2: Implement nodes-config.yaml.j2 template

**Files:**
- Modify: `playbooks/openshift/templates/nodes-config.yaml.j2`

The template iterates `groups['openshift_workers_' + target_cluster]` and emits one `hosts[]` entry per worker. Optional dict fields (`rootDeviceHints`, `networkConfig`) are emitted as inline JSON to avoid Jinja indentation gotchas (JSON is a valid YAML subset and `oc adm node-image create` parses it identically).

- [ ] **Step 1: Write the template**

Replace the contents of `playbooks/openshift/templates/nodes-config.yaml.j2`:

```jinja
---
{# Renders nodes-config.yaml for `oc adm node-image create --pxe`.
   Iterates the per-cluster worker group and emits one hosts[] entry
   per worker. Optional dict fields are inline JSON for safety. #}
{% set _workers = groups['openshift_workers_' + target_cluster] %}
hosts:
{% for w in _workers %}
{%   set h = hostvars[w] %}
  - hostname: {{ h.openshift_add_node_hostname | default(w) }}
    interfaces:
      - name: {{ h.openshift_add_node_interface_name | default('eth0') }}
        macAddress: "{{ h.openshift_add_node_mac }}"
{%   if h.openshift_add_node_root_device is defined %}
    rootDeviceHints: {{ h.openshift_add_node_root_device | to_json }}
{%   endif %}
{%   if h.openshift_add_node_network_config is defined %}
    networkConfig: {{ h.openshift_add_node_network_config | to_json }}
{%   endif %}
{% endfor %}
bootArtifactsBaseURL: {{ openshift_add_node_boot_artifacts_base_url }}
```

- [ ] **Step 2: Build a temp test fixture inventory**

```bash
mkdir -p /tmp/add-node-test/host_vars
```

Write `/tmp/add-node-test/inventory.yaml`:

```yaml
---
all:
  children:
    fake_cluster:
      hosts:
        fake-cluster-host:
          ansible_connection: local
          openshift_add_node_boot_artifacts_base_url: http://10.10.45.242/fake-add-node/
    openshift_workers_fake-cluster-host:
      hosts:
        worker-min:
          openshift_add_node_mac: "aa:bb:cc:dd:ee:01"
        worker-full:
          openshift_add_node_mac: "aa:bb:cc:dd:ee:02"
          openshift_add_node_hostname: worker-full.example.com
          openshift_add_node_root_device:
            deviceName: /dev/sda
          openshift_add_node_network_config:
            interfaces:
              - name: eth0
                type: ethernet
                state: up
                mac-address: "aa:bb:cc:dd:ee:02"
                ipv4:
                  enabled: true
                  dhcp: false
                  address:
                    - ip: 192.168.122.10
                      prefix-length: 24
```

- [ ] **Step 3: Build a temp render-only playbook**

Write `/tmp/add-node-test/render.yml`:

```yaml
---
- name: Render nodes-config.yaml from template
  hosts: fake-cluster-host
  connection: local
  gather_facts: false
  vars:
    target_cluster: fake-cluster-host
  tasks:
    - name: Render template to /tmp/nodes-config.rendered.yaml
      ansible.builtin.template:
        src: "{{ playbook_dir | dirname }}/igou-ansible/playbooks/openshift/templates/nodes-config.yaml.j2"
        dest: /tmp/nodes-config.rendered.yaml
        mode: "0644"
```

(Adjust the `src:` path so it points at the actual template in your workspace. Easiest is to use an absolute path: `/workspace/igou-ansible/playbooks/openshift/templates/nodes-config.yaml.j2`.)

- [ ] **Step 4: Render and validate**

```bash
ansible-playbook -i /tmp/add-node-test/inventory.yaml /tmp/add-node-test/render.yml
cat /tmp/nodes-config.rendered.yaml
python3 -c 'import yaml,sys; d=yaml.safe_load(open("/tmp/nodes-config.rendered.yaml")); assert len(d["hosts"]) == 2, d; assert d["hosts"][0]["interfaces"][0]["macAddress"] == "aa:bb:cc:dd:ee:01", d; assert "rootDeviceHints" not in d["hosts"][0], d; assert d["hosts"][1]["rootDeviceHints"] == {"deviceName": "/dev/sda"}, d; assert d["hosts"][1]["networkConfig"]["interfaces"][0]["ipv4"]["address"][0]["ip"] == "192.168.122.10", d; assert d["bootArtifactsBaseURL"] == "http://10.10.45.242/fake-add-node/", d; print("OK")'
```

Expected output ends with `OK`. If the assertions fail, fix the template and re-render.

- [ ] **Step 5: Clean up the test fixtures**

```bash
rm -rf /tmp/add-node-test /tmp/nodes-config.rendered.yaml
```

- [ ] **Step 6: Lint the template**

```bash
yamllint playbooks/openshift/templates/nodes-config.yaml.j2
```

(yamllint may flag the Jinja `{% %}` lines depending on rules; if so, that's expected and not blocking. ansible-lint does not lint .j2 files directly.)

- [ ] **Step 7: Commit**

```bash
git add playbooks/openshift/templates/nodes-config.yaml.j2
git commit -m "Implement nodes-config.yaml.j2 template for add-node playbook"
```

---

## Task 3: Implement Play 1 preflight assertions

**Files:**
- Modify: `playbooks/openshift/add_node_iso.yml` (Play 1 tasks)

Replace the Play 1 stub task with three preflight assertions: `KUBECONFIG` is set, the worker group exists and is non-empty, and every worker has a syntactically valid MAC.

- [ ] **Step 1: Replace Play 1 tasks with preflight block**

In `playbooks/openshift/add_node_iso.yml`, replace the Play 1 `tasks:` block (the placeholder debug task) with:

```yaml
  pre_tasks:
    - name: Preflight — KUBECONFIG must be set in the environment
      ansible.builtin.assert:
        that:
          - lookup('env', 'KUBECONFIG') | length > 0
        fail_msg: >-
          KUBECONFIG environment variable is not set. Export it before
          running this playbook (e.g., `export KUBECONFIG=~/.kube/ocp-config`).

    - name: Preflight — worker group must exist and be non-empty
      vars:
        _workers_group: "openshift_workers_{{ target_cluster }}"
        _workers: "{{ groups[_workers_group] | default([]) }}"
      ansible.builtin.assert:
        that:
          - _workers | length > 0
        fail_msg: >-
          Inventory group `{{ _workers_group }}` is missing or empty.
          Add at least one worker host to that group with
          `openshift_add_node_mac` set.

    - name: Preflight — every worker must have a valid MAC address
      ansible.builtin.assert:
        that:
          - hostvars[item].openshift_add_node_mac is defined
          - hostvars[item].openshift_add_node_mac is match('^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$')
        fail_msg: >-
          Worker `{{ item }}` is missing or has an invalid
          `openshift_add_node_mac`. Expected format aa:bb:cc:dd:ee:ff.
      loop: "{{ groups['openshift_workers_' + target_cluster] }}"
      loop_control:
        label: "{{ item }}"

  tasks: []
```

(`tasks: []` is intentional — Tasks 4–6 will populate it.)

- [ ] **Step 2: Verify the KUBECONFIG assertion fires when unset**

```bash
unset KUBECONFIG
ansible-playbook \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  --skip-tags monitor \
  playbooks/openshift/add_node_iso.yml \
  --check
```

Expected: the play fails on the "KUBECONFIG must be set" assertion. (Other assertions may not be reached.)

- [ ] **Step 3: Verify the empty-group assertion fires**

The `openshift_workers_ocp` group does not yet exist in inventory, so the second assertion is the relevant one once KUBECONFIG is set. Run:

```bash
export KUBECONFIG=/dev/null    # any non-empty value satisfies the env check
ansible-playbook \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  --skip-tags monitor \
  playbooks/openshift/add_node_iso.yml \
  --check
```

Expected: the play fails on the "worker group must exist and be non-empty" assertion with the message naming `openshift_workers_ocp`.

- [ ] **Step 4: Verify the bad-MAC assertion fires using a temp inventory**

```bash
mkdir -p /tmp/add-node-test
```

Write `/tmp/add-node-test/inventory.yaml`:

```yaml
---
all:
  children:
    openshift_clusters:
      hosts:
        ocp:
          ansible_connection: local
    openshift_workers_ocp:
      hosts:
        bad-worker:
          openshift_add_node_mac: "not-a-mac"
```

Run:

```bash
ansible-playbook \
  -i /tmp/add-node-test/inventory.yaml \
  -e target_cluster=ocp \
  --skip-tags monitor \
  playbooks/openshift/add_node_iso.yml \
  --check
```

Expected: fails on the MAC validation assertion naming `bad-worker`.

- [ ] **Step 5: Clean up the temp inventory**

```bash
rm -rf /tmp/add-node-test
unset KUBECONFIG
```

- [ ] **Step 6: Lint**

```bash
yamllint playbooks/openshift/add_node_iso.yml
ansible-lint --profile=production playbooks/openshift/add_node_iso.yml
```

Fix any errors before committing.

- [ ] **Step 7: Commit**

```bash
git add playbooks/openshift/add_node_iso.yml
git commit -m "Add Play 1 preflight assertions to add-node playbook"
```

---

## Task 4: Implement Play 1 setup (work dir + pull secret extraction)

**Files:**
- Modify: `playbooks/openshift/add_node_iso.yml` (Play 1 `tasks:`)

Wipe and recreate the per-cluster work dir, then fetch the cluster's pull secret to `<work_dir>/auth.json`.

- [ ] **Step 1: Add the setup tasks**

Replace `tasks: []` in Play 1 with:

```yaml
  tasks:
    - name: Wipe stale work dir
      ansible.builtin.file:
        path: "{{ openshift_add_node_work_dir }}"
        state: absent

    - name: Create fresh work dir
      ansible.builtin.file:
        path: "{{ openshift_add_node_work_dir }}"
        state: directory
        mode: "0700"

    - name: Fetch cluster pull secret to work dir
      ansible.builtin.shell: |
        set -o pipefail
        oc -n openshift-config get secret pull-secret \
          -o jsonpath='{.data.\.dockerconfigjson}' \
          | base64 -d > "{{ openshift_add_node_work_dir }}/auth.json"
      args:
        executable: /bin/bash
      changed_when: true
      no_log: true

    - name: Lock down pull secret file mode
      ansible.builtin.file:
        path: "{{ openshift_add_node_work_dir }}/auth.json"
        mode: "0600"
```

- [ ] **Step 2: Syntax check**

```bash
ansible-playbook --syntax-check \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  playbooks/openshift/add_node_iso.yml
```

Expected: clean.

- [ ] **Step 3: Lint**

```bash
yamllint playbooks/openshift/add_node_iso.yml
ansible-lint --profile=production playbooks/openshift/add_node_iso.yml
```

Note: `ansible-lint` may warn about the `shell` module preferring `command`. Suppress only if `oc | base64` truly requires shell piping (it does). If a warning fires, add a per-task tag annotation (`# noqa command-instead-of-shell` is not appropriate here; instead document inline that the pipe requires shell).

Fix any genuine errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/openshift/add_node_iso.yml
git commit -m "Add Play 1 work-dir setup and pull-secret extraction"
```

---

## Task 5: Implement Play 1 nodes-config render and `oc adm node-image create --pxe`

**Files:**
- Modify: `playbooks/openshift/add_node_iso.yml` (Play 1 `tasks:`)

Render the template and run the `oc` command, then verify expected outputs.

- [ ] **Step 1: Add the render + create + verify tasks**

Append to Play 1 `tasks:` (after the pull-secret tasks from Task 4):

```yaml
    - name: Render nodes-config.yaml
      ansible.builtin.template:
        src: templates/nodes-config.yaml.j2
        dest: "{{ openshift_add_node_work_dir }}/nodes-config.yaml"
        mode: "0644"

    - name: Run `oc adm node-image create --pxe`
      ansible.builtin.command:
        cmd: >-
          oc adm node-image create --pxe
          --dir {{ openshift_add_node_work_dir }}
          --registry-config {{ openshift_add_node_work_dir }}/auth.json
        creates: "{{ openshift_add_node_work_dir }}/node.{{ openshift_add_node_arch }}.ipxe"

    - name: List generated PXE assets
      ansible.builtin.find:
        paths: "{{ openshift_add_node_work_dir }}"
        file_type: file
        excludes:
          - auth.json
          - nodes-config.yaml
      register: _add_node_assets

    - name: Show generated PXE asset filenames
      ansible.builtin.debug:
        msg: "{{ _add_node_assets.files | map(attribute='path') | map('basename') | sort }}"

    - name: Verify expected PXE asset files exist
      ansible.builtin.stat:
        path: "{{ openshift_add_node_work_dir }}/{{ item }}"
      register: _asset_stat
      failed_when: not _asset_stat.stat.exists
      loop:
        - "node.{{ openshift_add_node_arch }}.ipxe"
        - "node.{{ openshift_add_node_arch }}-vmlinuz"
        - "node.{{ openshift_add_node_arch }}-initrd.img"
        - "node.{{ openshift_add_node_arch }}-rootfs.img"
      loop_control:
        label: "{{ item }}"

    - name: Set fact for Play 2 — generated asset paths
      ansible.builtin.set_fact:
        openshift_add_node_pxe_assets:
          ipxe: "{{ openshift_add_node_work_dir }}/node.{{ openshift_add_node_arch }}.ipxe"
          work_dir: "{{ openshift_add_node_work_dir }}"
          arch: "{{ openshift_add_node_arch }}"
```

- [ ] **Step 2: Note the spec's known-unknown about filenames**

The four filenames in the `loop:` (`node.<arch>.ipxe`, `-vmlinuz`, `-initrd.img`, `-rootfs.img`) are an assumption that mirrors the agent-install equivalents. The first real cluster run (Task 9) will produce a debug message listing the actual files. **If the assumed names differ, update both this stat loop and the Play 2 iPXE source path in Task 7.**

- [ ] **Step 3: Syntax check and lint**

```bash
ansible-playbook --syntax-check \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  playbooks/openshift/add_node_iso.yml
yamllint playbooks/openshift/add_node_iso.yml
ansible-lint --profile=production playbooks/openshift/add_node_iso.yml
```

Fix any errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/openshift/add_node_iso.yml
git commit -m "Render nodes-config and run oc adm node-image create --pxe"
```

---

## Task 6: Implement Play 1 `monitor` tag

**Files:**
- Modify: `playbooks/openshift/add_node_iso.yml` (Play 1 `tasks:`)

Add a tagged block that runs `oc adm node-image monitor` against the static IPs of all workers in the group.

- [ ] **Step 1: Append the monitor block to Play 1 tasks**

Add at the end of Play 1 `tasks:`:

```yaml
    - name: Monitor add-node progress
      tags: [monitor, never]
      vars:
        _worker_ips: >-
          {{ groups['openshift_workers_' + target_cluster]
             | map('extract', hostvars)
             | map(attribute='openshift_add_node_network_config', default={})
             | map(attribute='interfaces', default=[])
             | map('first', default={})
             | map(attribute='ipv4', default={})
             | map(attribute='address', default=[])
             | map('first', default={})
             | map(attribute='ip', default='')
             | list }}
      block:
        - name: Assert every worker has a static IP for monitoring
          ansible.builtin.assert:
            that:
              - _worker_ips | reject('equalto', '') | list | length == _worker_ips | length
            fail_msg: >-
              At least one worker has no static IP in
              `openshift_add_node_network_config`. Run
              `oc adm node-image monitor --ip-addresses <ips>` manually
              with addresses discovered from DHCP.

        - name: Run `oc adm node-image monitor`
          ansible.builtin.command:
            cmd: >-
              oc adm node-image monitor
              --ip-addresses {{ _worker_ips | join(',') }}
          changed_when: false
```

- [ ] **Step 2: Verify the monitor block is gated behind the tag**

```bash
ansible-playbook --list-tasks \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  playbooks/openshift/add_node_iso.yml
```

Expected: the `Monitor add-node progress` block tasks appear with tags `[monitor, never]`. They will not run by default.

- [ ] **Step 3: Lint**

```bash
yamllint playbooks/openshift/add_node_iso.yml
ansible-lint --profile=production playbooks/openshift/add_node_iso.yml
```

- [ ] **Step 4: Commit**

```bash
git add playbooks/openshift/add_node_iso.yml
git commit -m "Add monitor tag for `oc adm node-image monitor` step"
```

---

## Task 7: Implement Play 2 — delivery to TrueNAS netbootxyz

**Files:**
- Modify: `playbooks/openshift/add_node_iso.yml` (Play 2)

Replace the Play 2 stub with the real delivery tasks. This mirrors the second play of `playbooks/openshift/agent-install/deploy_pxe_assets.yml`.

- [ ] **Step 1: Replace Play 2 with the real delivery tasks**

Replace the entire Play 2 (`- name: Deliver PXE assets to TrueNAS netbootxyz` block) with:

```yaml
- name: Deliver PXE assets to TrueNAS netbootxyz
  hosts: truenas
  become: true
  gather_facts: false
  vars:
    cluster_host: "{{ target_cluster }}"
    work_dir: "{{ hostvars[cluster_host]['openshift_add_node_work_dir'] }}"
    arch: "{{ hostvars[cluster_host]['openshift_add_node_arch'] | default('x86_64') }}"
    truenas_assets_root: /mnt/ssd/containers/netbootxyz/assets
    truenas_menus_root: /mnt/ssd/containers/netbootxyz/config/menus
    asset_subdir: "{{ cluster_host }}-add-node"
  tasks:
    - name: Verify netbootxyz menus directory exists
      ansible.builtin.stat:
        path: "{{ truenas_menus_root }}"
      register: _menus_stat

    - name: Fail if netbootxyz menus directory is missing
      ansible.builtin.assert:
        that:
          - _menus_stat.stat.exists
          - _menus_stat.stat.isdir
        fail_msg: "netbootxyz menus directory {{ truenas_menus_root }} is missing"

    - name: Ensure asset destination directory exists
      ansible.builtin.file:
        path: "{{ truenas_assets_root }}/{{ asset_subdir }}"
        state: directory
        mode: "0755"
        owner: "1000"
        group: "1000"

    - name: Sync boot artifacts to TrueNAS
      ansible.posix.synchronize:
        src: "{{ work_dir }}/"
        dest: "{{ truenas_assets_root }}/{{ asset_subdir }}/"
        delete: true
        rsync_opts:
          - "--exclude=*.ipxe"
          - "--exclude=auth.json"
          - "--exclude=nodes-config.yaml"
          - "--chown=1000:1000"

    - name: Copy iPXE script per worker into menus dir
      ansible.builtin.copy:
        src: "{{ work_dir }}/node.{{ arch }}.ipxe"
        dest: "{{ truenas_menus_root }}/{{ hostvars[item].openshift_add_node_mac | replace(':', '') }}-add-node-{{ cluster_host }}.ipxe"
        mode: "0644"
        owner: "1000"
        group: "1000"
      loop: "{{ groups['openshift_workers_' + cluster_host] }}"
      loop_control:
        label: "{{ item }}"

    # Mirrors the netbootxyz workaround in deploy_pxe_assets.yml: a duplicate
    # copy under /local/ that the menu chain references.
    - name: Copy iPXE script per worker into menus/local dir
      ansible.builtin.copy:
        src: "{{ work_dir }}/node.{{ arch }}.ipxe"
        dest: "{{ truenas_menus_root }}/local/{{ hostvars[item].openshift_add_node_mac | replace(':', '') }}-add-node-{{ cluster_host }}.ipxe"
        mode: "0644"
        owner: "1000"
        group: "1000"
      loop: "{{ groups['openshift_workers_' + cluster_host] }}"
      loop_control:
        label: "{{ item }}"
```

- [ ] **Step 2: Syntax check**

```bash
ansible-playbook --syntax-check \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  playbooks/openshift/add_node_iso.yml
```

Expected: clean.

- [ ] **Step 3: Lint**

```bash
yamllint playbooks/openshift/add_node_iso.yml
ansible-lint --profile=production playbooks/openshift/add_node_iso.yml
```

Fix any errors.

- [ ] **Step 4: Commit**

```bash
git add playbooks/openshift/add_node_iso.yml
git commit -m "Implement Play 2 delivery to TrueNAS netbootxyz"
```

---

## Task 8: Final pre-commit pass on all new files

**Files:** none modified unless lint surfaces issues.

- [ ] **Step 1: Run the pre-commit suite on the new files**

```bash
pre-commit run --files \
  playbooks/openshift/add_node_iso.yml \
  playbooks/openshift/templates/nodes-config.yaml.j2
```

Expected: all hooks pass (or only known-flaky ones unrelated to this work).

- [ ] **Step 2: If any hook fails, fix and re-run until clean**

Fix any reported issues. If a hook reports something out of scope (e.g., a yamllint warning on an untouched file), don't fix that — only fix issues in the two new files.

- [ ] **Step 3: Commit any fixes if needed**

If fixes were made:

```bash
git add playbooks/openshift/add_node_iso.yml playbooks/openshift/templates/nodes-config.yaml.j2
git commit -m "Fix lint findings in add-node playbook"
```

If no fixes were made, skip this step.

---

## Task 9: Manual end-to-end validation against the live `ocp` cluster

This task is **not automated** — it requires a live cluster, a real worker host wired into the inventory, and TrueNAS access. Treat it as a checklist for the operator before merging.

**Files:** none in this repo. The operator may add a worker host to `igou-inventory/openshift_workers_ocp` under a separate commit/PR in that repo.

- [ ] **Step 1: Add a real worker to the inventory in the `igou-inventory` repo** (separate commit/PR there):

  - Add an `openshift_workers_ocp` group containing the worker.
  - Set `openshift_add_node_mac` (and `openshift_add_node_network_config` if static IP).
  - On `host_vars/ocp.yml`, add:
    ```yaml
    openshift_add_node_arch: x86_64
    openshift_add_node_boot_artifacts_base_url: http://10.10.45.242/ocp-add-node/
    ```

- [ ] **Step 2: Export a working KUBECONFIG and dry-run**

```bash
export KUBECONFIG=<path-to-ocp-kubeconfig>
ansible-navigator run playbooks/openshift/add_node_iso.yml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  --check --diff
```

Expected: preflight passes; rendered `nodes-config.yaml` looks correct in the diff; `oc` and rsync tasks show as "would run".

- [ ] **Step 3: Real run**

```bash
ansible-navigator run playbooks/openshift/add_node_iso.yml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp
```

- [ ] **Step 4: Verify the "Show generated PXE asset filenames" debug output**

Compare against the assumed names in Task 5: `node.x86_64.ipxe`, `node.x86_64-vmlinuz`, `node.x86_64-initrd.img`, `node.x86_64-rootfs.img`.

If the actual filenames differ:
  - Update the `loop:` in the "Verify expected PXE asset files exist" task (Task 5).
  - Update the iPXE source path in Play 2's two `ansible.builtin.copy` tasks (Task 7).
  - Update the rsync `--exclude=*.ipxe` if the iPXE script's extension differs.
  - Re-run from Step 3.

- [ ] **Step 5: Inspect the generated iPXE script on TrueNAS**

```bash
ssh truenas-admin@truenas.igou.systems \
  "cat /mnt/ssd/containers/netbootxyz/config/menus/<mac-no-colons>-add-node-ocp.ipxe"
```

Verify the kernel/initramfs/rootfs URLs reference `openshift_add_node_boot_artifacts_base_url`. If they reference relative paths or the wrong host, see the spec's "Known unknowns" #2 — fall back to a templated/sed rewrite of the script before copying.

- [ ] **Step 6: PXE-boot the worker**

Power on the worker; it should chainload the iPXE script for its MAC and start the add-node flow.

- [ ] **Step 7: Optionally run monitor**

```bash
ansible-navigator run playbooks/openshift/add_node_iso.yml \
  -i igou-inventory/inventory.yaml \
  -e target_cluster=ocp \
  --tags monitor
```

(Only valid if the worker has a static IP in inventory.)

- [ ] **Step 8: Approve any pending CSRs**

```bash
oc get csr
oc adm certificate approve <name>
```

- [ ] **Step 9: Confirm node joined**

```bash
oc get nodes -o wide
```

Expected: the new worker appears as `Ready`.

---

## Self-Review Notes

Spec coverage check:
- Play 1 preflight (KUBECONFIG, group, MAC) → Task 3 ✓
- Work-dir wipe + create → Task 4 ✓
- Pull-secret extraction via `oc get secret` → Task 4 ✓
- nodes-config.yaml template rendering → Tasks 2 & 5 ✓
- `oc adm node-image create --pxe` → Task 5 ✓
- Asset existence verification → Task 5 ✓
- Monitor tag → Task 6 ✓
- TrueNAS preflight + asset dir + rsync + per-MAC iPXE script x2 → Task 7 ✓
- bootArtifactsBaseURL emitted in nodes-config.yaml → Task 2 ✓
- All four "known unknowns" surfaced for verification → Tasks 5 & 9 ✓

No placeholders. No "TBD". Every code block is complete.

Type/name consistency:
- `openshift_add_node_work_dir` used identically in Play 1 (set in `vars:`) and Play 2 (read via `hostvars[cluster_host]`).
- `openshift_add_node_arch` defaulted to `x86_64` in both plays consistently.
- `openshift_workers_<cluster>` group naming consistent across all tasks.
- iPXE filename pattern `<mac-no-colons>-add-node-<cluster>.ipxe` consistent in both `/menus/` and `/menus/local/` copies (Task 7).
