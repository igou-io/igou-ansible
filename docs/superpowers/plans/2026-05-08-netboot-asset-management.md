# netboot.xyz Asset Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `playbooks/truenas/configure_netbootxyz.yml` and `playbooks/truenas/sync_boot_files.yml` with a single declarative-plus-fragments playbook tree under `playbooks/netboot/`. Eliminate the hardcoded `/home/igou/igou-node-bootstrap/netbootxyz-menus` reference, drive the menu from a `netboot_entries` inventory list, and add per-host pins by MAC/hostname auto-served via a header in the generated `menu.ipxe`.

**Architecture:** One orchestrator `playbooks/netboot/deploy_assets.yml` with six tagged stages (`preflight`, `render`, `push`, `fetch`, `local`, `verify`) backed by per-stage task files. `render` runs `delegate_to: localhost` and writes to `.cache/netboot-menus/`. `push`/`fetch`/`local` run against `truenas`. `verify` runs `delegate_to: localhost` and probes the netbootxyz HTTP endpoint. Idempotency via content-hash checks before every write. Inventory variables in a new `igou-inventory/group_vars/all/netboot.yml` (split-dir style).

**Tech Stack:** Ansible (`ansible.builtin.template`, `ansible.builtin.copy`, `ansible.builtin.synchronize`, `ansible.builtin.file`, `ansible.builtin.stat`, `ansible.builtin.get_url`, `ansible.builtin.uri`, `ansible.builtin.assert`), Jinja2 templates, the existing TrueNAS connection (already configured in inventory).

---

## Reference material

- Spec: `docs/superpowers/specs/2026-05-08-netboot-asset-management-design.md`. Re-read end to end. The decisions log at the bottom is the source of truth for ambiguity.
- Prior art (style template): `playbooks/truenas/configure_netbootxyz.yml`, `playbooks/truenas/sync_boot_files.yml`, `playbooks/truenas/configure_docker_containers.yml`. Match the comment-header convention and the 1000:1000 owner/group default.
- The recent stagewise pattern: `playbooks/routeros/deploy_netboot_binaries.yml` and the `playbooks/routeros/tasks/netboot_*.yml` files. Same `import_tasks` + tag-per-stage shape.
- Repo conventions: `CLAUDE.md` at the repo root. YAML must start with `---`, two-space indent, YAML 1.2 booleans (`true`/`false`), ansible-lint production profile.
- Live netbootxyz container config: `igou-inventory/group_vars/truenas.yml` `truenas_docker_containers[name=netbootxyz]`. The `assets:/assets` and `config:/config` named volumes resolve to `/mnt/ssd/containers/netbootxyz/{assets,config}`.
- The existing per-host workaround: `playbooks/truenas/configure_netbootxyz.yml` mirrors content into `config/menus/local/`. We replicate that.

## Files Created/Modified/Deleted

```
.gitignore                                              MODIFY  (add .cache/netboot-menus/)
igou-inventory/group_vars/all.yml                       SPLIT   (becomes all/main.yml)
igou-inventory/group_vars/all/main.yml                  CREATE  (renamed from all.yml)
igou-inventory/group_vars/all/netboot.yml               CREATE  (new vars)
igou-inventory/group_vars/truenas.yml                   MODIFY  (drop truenas_boot_files_* block)

playbooks/netboot/deploy_assets.yml                     CREATE  (orchestrator)
playbooks/netboot/tasks/preflight.yml                   CREATE  (schema validation)
playbooks/netboot/tasks/render_menu.yml                 CREATE  (render to .cache/)
playbooks/netboot/tasks/push_text.yml                   CREATE  (sync to TrueNAS)
playbooks/netboot/tasks/fetch_binaries.yml              CREATE  (download ISOs)
playbooks/netboot/tasks/push_local_artifacts.yml        CREATE  (copy local builds)
playbooks/netboot/tasks/verify.yml                      CREATE  (HTTP probes)
playbooks/netboot/templates/menu.ipxe.j2                CREATE  (top-level menu)
playbooks/netboot/templates/entry-kernel.ipxe.j2        CREATE  (kernel/initrd entry)
playbooks/netboot/templates/entry-iso.ipxe.j2           CREATE  (sanboot iso entry)
playbooks/netboot/templates/entry-chainload.ipxe.j2     CREATE  (chain to URL)
playbooks/netboot/templates/host-mac.ipxe.j2            CREATE  (per-host recipe)
playbooks/netboot/files/fragments/.gitkeep              CREATE
playbooks/netboot/files/kickstart/.gitkeep              CREATE
playbooks/netboot/files/cloud-init/.gitkeep             CREATE

playbooks/truenas/configure_netbootxyz.yml              DELETE
playbooks/truenas/sync_boot_files.yml                   DELETE
```

## Pre-flight assumptions (verify before Task 1)

These are *assumptions baked into this plan*. If any is wrong, stop and clarify with the user before proceeding.

1. **TrueNAS connection works.** `ansible -i igou-inventory/inventory.yaml truenas -m ping` returns `pong`. The container is running and reachable on `10.10.45.242`.
2. **The netbootxyz container is at `1000:1000`.** Verified by `igou-inventory/group_vars/truenas.yml`. New files default to that owner.
3. **`assets:/assets` and `config:/config` are named volumes resolving to `/mnt/ssd/containers/netbootxyz/{assets,config}`.** This is the TrueNAS Docker convention for named volumes inside an app's dataset. Verify by SSH-ing to TrueNAS and `ls /mnt/ssd/containers/netbootxyz/`.
4. **No-one has manually written into `/mnt/ssd/containers/netbootxyz/config/menus/local/` outside the existing playbook.** The new playbook treats the `local/` mirror as managed.
5. **`igou-inventory` is a sibling repo, symlinked at `igou-inventory/` under the workspace.** Inventory edits are committed to that repo, *not* `igou-ansible`.
6. **Existing `~/igou-node-bootstrap/netbootxyz-menus/` content is reachable from the operator's environment for one-time translation.** Per the spec, the playbook does not auto-import; the operator translates by hand into `netboot_entries` / `fragments/` during Task 8.

---

## Task 1: Inventory split-dir + new netboot variables + .gitignore

**Files:**
- Move: `igou-inventory/group_vars/all.yml` → `igou-inventory/group_vars/all/main.yml`
- Create: `igou-inventory/group_vars/all/netboot.yml`
- Modify: `.gitignore` (add `.cache/netboot-menus/`)

This task locks in the variable surface every later task uses. We do this first so the orchestrator and stage tasks can `Read` real values via inventory.

- [ ] **Step 1: Convert `all.yml` to `all/` split-dir**

  Ansible accepts either `group_vars/all.yml` (single file) or `group_vars/all/*.yml` (directory). We need the directory form so we can drop in a per-feature file.

  ```bash
  cd igou-inventory/group_vars
  mkdir all
  git mv all.yml all/main.yml
  ```

  Verify the move:

  ```bash
  ls -la all/
  ```

  Expected: `main.yml` exists; `all.yml` is gone.

- [ ] **Step 2: Create `igou-inventory/group_vars/all/netboot.yml`**

  Full content:

  ```yaml
  ---
  # Variables consumed by playbooks/netboot/deploy_assets.yml.
  #
  # netbootxyz_host         — Ansible inventory hostname that runs the netbootxyz container.
  # netbootxyz_root         — TrueNAS-side filesystem path that maps to the container's
  #                            /config and /assets mounts (i.e. parent of "config" and "assets").
  # netbootxyz_self_url     — Externally-reachable HTTP URL the rendered menu uses for
  #                            chain calls back to itself (per-host hooks, kickstart refs).
  # netboot_entries         — declarative menu items. See the spec for the schema.
  # netboot_host_pins       — MAC/hostname pins. See the spec for the three forms.

  netbootxyz_host: truenas
  netbootxyz_root: /mnt/ssd/containers/netbootxyz
  netbootxyz_self_url: http://10.10.45.242

  netboot_entries: []
  netboot_host_pins: []
  ```

  Both lists start empty; Task 8 populates them as part of the cutover.

- [ ] **Step 3: Add `.cache/netboot-menus/` to `.gitignore`**

  Open `.gitignore` (in the `igou-ansible` repo). Append:

  ```
  # Local render cache for playbooks/netboot/deploy_assets.yml
  .cache/netboot-menus/
  ```

  If `.cache/` is already gitignored as a wildcard (it is — see commit `5025a6c yamllint: ignore .cache/`), add the entry anyway as documentation. The entry is harmless even if redundant.

- [ ] **Step 4: Verify Ansible still resolves the inventory**

  ```bash
  ansible-inventory -i igou-inventory/inventory.yaml --list --yaml | grep -A 5 netbootxyz_host
  ```

  Expected: a line `netbootxyz_host: truenas` appears at least once. If it doesn't, the split-dir conversion broke something — investigate before continuing.

- [ ] **Step 5: Commit (two repos)**

  In `igou-inventory`:

  ```bash
  cd igou-inventory
  git add -A group_vars/all group_vars/all.yml
  git commit -m "Split group_vars/all into directory + add netboot vars"
  cd -
  ```

  In `igou-ansible`:

  ```bash
  git add .gitignore
  git commit -m "Ignore .cache/netboot-menus/ render output"
  ```

---

## Task 2: Orchestrator playbook + preflight stage

**Files:**
- Create: `playbooks/netboot/deploy_assets.yml`
- Create: `playbooks/netboot/tasks/preflight.yml`
- Create: `playbooks/netboot/files/{fragments,kickstart,cloud-init}/.gitkeep` (so empty dirs commit)

This task gives every later task a runnable orchestrator. After this task you can run `--tags preflight` and validate inventory without touching TrueNAS.

- [ ] **Step 1: Create `playbooks/netboot/deploy_assets.yml`**

  Full content:

  ```yaml
  ---
  # Manage netboot.xyz menu, kickstart/cloud-init seeds, and per-host PXE
  # pins on the TrueNAS-hosted netbootxyz container.
  #
  # Stages (each tag-gated):
  #   preflight  — schema validation, always runs.
  #   render     — Jinja-render menu.ipxe + entries/ + host/ to .cache/.
  #   push       — sync rendered + static text content to TrueNAS.
  #   fetch      — idempotent download of upstream URLs into /assets.
  #   local      — copy locally-built kernels/initrds into /assets.
  #   verify     — HTTP probes for menu.ipxe, host files, and ISOs.
  #
  # Inventory schema:
  #   See igou-inventory/group_vars/all/netboot.yml and the design spec
  #   docs/superpowers/specs/2026-05-08-netboot-asset-management-design.md.
  #
  # Common invocations:
  #   ansible-navigator run playbooks/netboot/deploy_assets.yml \
  #     -i igou-inventory/inventory.yaml
  #   # local-only render, no TrueNAS contact:
  #   ansible-navigator run playbooks/netboot/deploy_assets.yml \
  #     -i igou-inventory/inventory.yaml \
  #     --tags render -e netbootxyz_host=localhost --check
  #   # menu touch-up, skip the slow stages:
  #   ansible-navigator run playbooks/netboot/deploy_assets.yml \
  #     -i igou-inventory/inventory.yaml \
  #     --tags render,push,verify
  #
  # Add an entry: edit netboot_entries in
  #   igou-inventory/group_vars/all/netboot.yml.
  # Add a per-host pin: edit netboot_host_pins in the same file.
  # Add a hand-written .ipxe fragment: drop it in
  #   playbooks/netboot/files/fragments/. Auto-included on next render.

  - name: Deploy netboot.xyz custom menu and assets
    hosts: "{{ netbootxyz_host | default('truenas') }}"
    gather_facts: false
    become: true
    tasks:
      - import_tasks: tasks/preflight.yml
        tags: [preflight, render, push, fetch, local, verify, always]
      - import_tasks: tasks/render_menu.yml
        tags: [render]
      - import_tasks: tasks/push_text.yml
        tags: [push]
      - import_tasks: tasks/fetch_binaries.yml
        tags: [fetch]
      - import_tasks: tasks/push_local_artifacts.yml
        tags: [local]
      - import_tasks: tasks/verify.yml
        tags: [verify]
  ```

- [ ] **Step 2: Create `playbooks/netboot/tasks/preflight.yml`**

  Full content:

  ```yaml
  ---
  # Schema validation. delegate_to: localhost — pure data work, no remote contact.
  # Runs under every stage tag so every other task can rely on validated input.

  - name: Preflight — validate netboot_entries shape
    ansible.builtin.assert:
      that:
        - netboot_entries is iterable
        - netboot_entries is not string
      fail_msg: "netboot_entries must be a list (got {{ netboot_entries | type_debug }})."
    delegate_to: localhost
    run_once: true

  - name: Preflight — validate each entry
    ansible.builtin.assert:
      that:
        - item.id is defined
        - item.id is match('^[a-z0-9][a-z0-9._-]*$')
        - item.name is defined
        - item.kind is defined
        - item.kind in ['kernel', 'iso', 'chainload', 'local']
        - (item.kind != 'kernel') or (item.kernel is defined and item.initrd is defined)
        - (item.kind != 'iso') or (item.url is defined and item.sha256 is defined)
        - (item.kind != 'chainload') or (item.url is defined)
        - (item.kind != 'local') or (item.kernel_src is defined and item.initrd_src is defined)
      fail_msg: |
        Invalid netboot_entries item: {{ item | to_nice_yaml }}
        Slug must match ^[a-z0-9][a-z0-9._-]*$. Required fields per kind:
          kernel:    kernel, initrd
          iso:       url, sha256
          chainload: url
          local:     kernel_src, initrd_src
    loop: "{{ netboot_entries }}"
    loop_control:
      label: "{{ item.id | default('<missing-id>') }}"
    delegate_to: localhost
    run_once: true

  - name: Preflight — collect entry ids for cross-reference
    ansible.builtin.set_fact:
      _netboot_entry_ids: "{{ netboot_entries | map(attribute='id') | list }}"
    delegate_to: localhost
    run_once: true

  - name: Preflight — validate each host pin
    ansible.builtin.assert:
      that:
        - item.mac is defined
        - item.mac is match('^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$')
        - >-
          ((item.entry is defined) | int)
          + ((item.kernel is defined and item.initrd is defined) | int)
          + ((item.fragment is defined) | int)
          == 1
        - (item.entry is not defined) or (item.entry in _netboot_entry_ids)
      fail_msg: |
        Invalid netboot_host_pins item: {{ item | to_nice_yaml }}
        Each pin must:
          - have a MAC matching aa:bb:cc:dd:ee:ff
          - specify exactly one of: entry, kernel+initrd, fragment
          - if entry is set, reference an id present in netboot_entries
    loop: "{{ netboot_host_pins }}"
    loop_control:
      label: "{{ item.mac | default('<missing-mac>') }}"
    delegate_to: localhost
    run_once: true

  - name: Preflight — verify referenced kickstart files exist locally
    ansible.builtin.stat:
      path: "{{ playbook_dir }}/files/kickstart/{{ item.kickstart }}"
    register: _ks_check
    loop: "{{ netboot_entries | selectattr('kickstart', 'defined') | list }}"
    loop_control:
      label: "{{ item.id }} -> {{ item.kickstart }}"
    delegate_to: localhost
    run_once: true
    failed_when:
      - _ks_check.stat is defined
      - not _ks_check.stat.exists

  - name: Preflight — discover fragment files
    ansible.builtin.find:
      paths: "{{ playbook_dir }}/files/fragments"
      patterns: "*.ipxe"
      file_type: file
    register: _fragments_find
    delegate_to: localhost
    run_once: true

  - name: Preflight — register fragment filename list
    ansible.builtin.set_fact:
      _netboot_fragments: "{{ _fragments_find.files | map(attribute='path') | map('basename') | sort | list }}"
    delegate_to: localhost
    run_once: true

  - name: Preflight — summary
    ansible.builtin.debug:
      msg:
        - "entries:    {{ netboot_entries | length }}"
        - "host pins:  {{ netboot_host_pins | length }}"
        - "fragments:  {{ _netboot_fragments | length }} ({{ _netboot_fragments | join(', ') }})"
    delegate_to: localhost
    run_once: true
  ```

- [ ] **Step 3: Create empty content directories**

  ```bash
  mkdir -p playbooks/netboot/files/fragments \
           playbooks/netboot/files/kickstart \
           playbooks/netboot/files/cloud-init \
           playbooks/netboot/templates \
           playbooks/netboot/tasks
  touch playbooks/netboot/files/fragments/.gitkeep \
        playbooks/netboot/files/kickstart/.gitkeep \
        playbooks/netboot/files/cloud-init/.gitkeep
  ```

- [ ] **Step 4: Run preflight against the empty inventory (the failing-test step)**

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --tags preflight -e netbootxyz_host=localhost \
    --mode stdout
  ```

  Expected output (paraphrased): every assert PASSES (no entries to validate), the `summary` debug shows `entries: 0`, `host pins: 0`, `fragments: 0`. Empty lists are valid input.

- [ ] **Step 5: Run preflight with a deliberately-bad sample (verify it fails loudly)**

  Create a temp test file `/tmp/netboot-test-vars.yml`:

  ```yaml
  ---
  netboot_entries:
    - id: BAD CAPS
      name: "Bad slug"
      kind: kernel
  netboot_host_pins: []
  ```

  Run:

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --tags preflight -e netbootxyz_host=localhost \
    -e @/tmp/netboot-test-vars.yml \
    --mode stdout
  ```

  Expected: assertion failure naming `BAD CAPS`, complaining about the slug regex AND missing `kernel:`/`initrd:`. Delete `/tmp/netboot-test-vars.yml` after.

- [ ] **Step 6: Lint**

  ```bash
  ansible-lint --profile=production playbooks/netboot/
  yamllint playbooks/netboot/
  ```

  Both should report 0 issues.

- [ ] **Step 7: Commit**

  ```bash
  git add playbooks/netboot/
  git commit -m "Add netboot deploy_assets orchestrator and preflight stage"
  ```

---

## Task 3: Render templates + render_menu stage

**Files:**
- Create: `playbooks/netboot/templates/menu.ipxe.j2`
- Create: `playbooks/netboot/templates/entry-kernel.ipxe.j2`
- Create: `playbooks/netboot/templates/entry-iso.ipxe.j2`
- Create: `playbooks/netboot/templates/entry-chainload.ipxe.j2`
- Create: `playbooks/netboot/templates/host-mac.ipxe.j2`
- Create: `playbooks/netboot/tasks/render_menu.yml`

This task makes `--tags render` produce a complete `.cache/netboot-menus/` tree from inventory. No remote writes yet.

> **Note on chainload entries:** the spec said `chainload` entries embed inline in `menu.ipxe`. We instead give every entry a uniform `entries/<id>.ipxe` file (chainload's is a one-line `chain ${url}`). This is a strict superset — same client behavior, simpler menu template. Spec section "Open items resolved during brainstorming" explicitly listed implementation choices as fair game; this is the only one we deviate on.

- [ ] **Step 1: Create `playbooks/netboot/templates/menu.ipxe.j2`**

  Full content:

  ```jinja
  #!ipxe
  ### Managed by playbooks/netboot/deploy_assets.yml — do not edit manually.

  :per_host
  isset ${hostname} && chain {{ netbootxyz_self_url }}/menus/host/HOSTNAME-${hostname}.ipxe || goto check_mac
  :check_mac
  isset ${mac} && chain {{ netbootxyz_self_url }}/menus/host/MAC-${mac:hexraw}.ipxe || goto main_menu

  :main_menu
  clear menu_choice
  menu netboot.xyz custom menu
  {% for entry in netboot_entries %}
  item {{ entry.id }} ${space} {{ entry.name }}
  {% endfor %}
  {% if _netboot_fragments | length > 0 %}
  item --gap -- Custom fragments
  {% for f in _netboot_fragments %}
  item fragment_{{ loop.index0 }} ${space} {{ f }}
  {% endfor %}
  {% endif %}
  item --gap
  item exit ${space} Exit

  choose menu_choice || goto menu_exit
  echo ${cls}
  goto ${menu_choice}

  {% for entry in netboot_entries %}
  :{{ entry.id }}
  chain entries/{{ entry.id }}.ipxe
  goto menu_exit

  {% endfor %}
  {% for f in _netboot_fragments %}
  :fragment_{{ loop.index0 }}
  chain fragments/{{ f }}
  goto menu_exit

  {% endfor %}
  :menu_exit
  exit
  ```

- [ ] **Step 2: Create `playbooks/netboot/templates/entry-kernel.ipxe.j2`**

  Full content:

  ```jinja
  #!ipxe
  ### Managed by playbooks/netboot/deploy_assets.yml — do not edit manually.
  ### Entry: {{ entry.name }} ({{ entry.id }})

  {% if entry.kind == 'local' %}
  set base /assets/local/{{ entry.id }}
  kernel {{ netbootxyz_self_url }}${base}/vmlinuz {{ entry.cmdline | default('') }}
  initrd {{ netbootxyz_self_url }}${base}/initrd
  {% elif entry.cache | default(false) %}
  set base /assets/cache/{{ entry.id }}
  kernel {{ netbootxyz_self_url }}${base}/vmlinuz {{ entry.cmdline | default('') }}
  initrd {{ netbootxyz_self_url }}${base}/initrd
  {% else %}
  kernel {{ entry.kernel | replace('${netboot_self}', netbootxyz_self_url) }} {{ entry.cmdline | default('') | replace('${netboot_self}', netbootxyz_self_url) }}
  initrd {{ entry.initrd | replace('${netboot_self}', netbootxyz_self_url) }}
  {% endif %}
  boot || goto failed

  :failed
  echo Boot failed for {{ entry.id }}; press any key to return to menu.
  prompt
  exit
  ```

- [ ] **Step 3: Create `playbooks/netboot/templates/entry-iso.ipxe.j2`**

  Full content:

  ```jinja
  #!ipxe
  ### Managed by playbooks/netboot/deploy_assets.yml — do not edit manually.
  ### Entry: {{ entry.name }} ({{ entry.id }})

  ### memdisk is hosted by the netbootxyz container at /memdisk on its HTTP root.
  kernel {{ netbootxyz_self_url }}/memdisk iso raw
  initrd {{ netbootxyz_self_url }}/assets/iso/{{ entry.id }}.iso
  boot || goto failed

  :failed
  echo Boot failed for {{ entry.id }}; press any key to return to menu.
  prompt
  exit
  ```

- [ ] **Step 4: Create `playbooks/netboot/templates/entry-chainload.ipxe.j2`**

  Full content:

  ```jinja
  #!ipxe
  ### Managed by playbooks/netboot/deploy_assets.yml — do not edit manually.
  ### Entry: {{ entry.name }} ({{ entry.id }})

  chain {{ entry.url | replace('${netboot_self}', netbootxyz_self_url) }}
  ```

- [ ] **Step 5: Create `playbooks/netboot/templates/host-mac.ipxe.j2`**

  Full content:

  ```jinja
  #!ipxe
  ### Managed by playbooks/netboot/deploy_assets.yml — do not edit manually.
  ### Host pin: mac={{ pin.mac }}{% if pin.hostname is defined %} hostname={{ pin.hostname }}{% endif %}

  {% if pin.entry is defined %}
  chain entries/{{ pin.entry }}.ipxe
  {% elif pin.fragment is defined %}
  {{ pin.fragment }}
  {% else %}
  kernel {{ pin.kernel | replace('${netboot_self}', netbootxyz_self_url) }} {{ pin.cmdline | default('') | replace('${netboot_self}', netbootxyz_self_url) }}
  initrd {{ pin.initrd | replace('${netboot_self}', netbootxyz_self_url) }}
  boot
  {% endif %}
  ```

- [ ] **Step 6: Create `playbooks/netboot/tasks/render_menu.yml`**

  Full content:

  ```yaml
  ---
  # Render menu.ipxe + entries/<id>.ipxe + host/MAC-*.ipxe + host/HOSTNAME-*.ipxe
  # into the local cache at .cache/netboot-menus/. Push stage handles the upload.

  - name: Render — define cache root
    ansible.builtin.set_fact:
      _netboot_cache_dir: "{{ playbook_dir }}/../../.cache/netboot-menus"
    delegate_to: localhost
    run_once: true

  - name: Render — wipe cache to avoid stale outputs
    ansible.builtin.file:
      path: "{{ _netboot_cache_dir }}"
      state: absent
    delegate_to: localhost
    run_once: true

  - name: Render — recreate cache subdirs
    ansible.builtin.file:
      path: "{{ _netboot_cache_dir }}/{{ item }}"
      state: directory
      mode: "0755"
    loop:
      - ""
      - entries
      - host
    delegate_to: localhost
    run_once: true

  - name: Render — top-level menu.ipxe
    ansible.builtin.template:
      src: menu.ipxe.j2
      dest: "{{ _netboot_cache_dir }}/menu.ipxe"
      mode: "0644"
    delegate_to: localhost
    run_once: true

  - name: Render — entries (kernel/local)
    ansible.builtin.template:
      src: entry-kernel.ipxe.j2
      dest: "{{ _netboot_cache_dir }}/entries/{{ item.id }}.ipxe"
      mode: "0644"
    vars:
      entry: "{{ item }}"
    loop: "{{ netboot_entries | selectattr('kind', 'in', ['kernel', 'local']) | list }}"
    loop_control:
      label: "{{ item.id }}"
    delegate_to: localhost
    run_once: true

  - name: Render — entries (iso)
    ansible.builtin.template:
      src: entry-iso.ipxe.j2
      dest: "{{ _netboot_cache_dir }}/entries/{{ item.id }}.ipxe"
      mode: "0644"
    vars:
      entry: "{{ item }}"
    loop: "{{ netboot_entries | selectattr('kind', 'equalto', 'iso') | list }}"
    loop_control:
      label: "{{ item.id }}"
    delegate_to: localhost
    run_once: true

  - name: Render — entries (chainload)
    ansible.builtin.template:
      src: entry-chainload.ipxe.j2
      dest: "{{ _netboot_cache_dir }}/entries/{{ item.id }}.ipxe"
      mode: "0644"
    vars:
      entry: "{{ item }}"
    loop: "{{ netboot_entries | selectattr('kind', 'equalto', 'chainload') | list }}"
    loop_control:
      label: "{{ item.id }}"
    delegate_to: localhost
    run_once: true

  - name: Render — host pins (MAC file)
    ansible.builtin.template:
      src: host-mac.ipxe.j2
      dest: "{{ _netboot_cache_dir }}/host/MAC-{{ item.mac | lower | replace(':', '') }}.ipxe"
      mode: "0644"
    vars:
      pin: "{{ item }}"
    loop: "{{ netboot_host_pins }}"
    loop_control:
      label: "{{ item.mac }}"
    delegate_to: localhost
    run_once: true

  - name: Render — host pins (HOSTNAME alias chains to MAC file)
    ansible.builtin.copy:
      dest: "{{ _netboot_cache_dir }}/host/HOSTNAME-{{ item.hostname }}.ipxe"
      content: |
        #!ipxe
        ### Managed by playbooks/netboot/deploy_assets.yml — do not edit manually.
        chain MAC-{{ item.mac | lower | replace(':', '') }}.ipxe
      mode: "0644"
    loop: "{{ netboot_host_pins | selectattr('hostname', 'defined') | list }}"
    loop_control:
      label: "{{ item.hostname }}"
    delegate_to: localhost
    run_once: true

  - name: Render — summary
    ansible.builtin.find:
      paths: "{{ _netboot_cache_dir }}"
      file_type: file
      recurse: true
    register: _render_summary
    delegate_to: localhost
    run_once: true

  - name: Render — list rendered files
    ansible.builtin.debug:
      msg: "{{ _render_summary.files | map(attribute='path') | map('regex_replace', _netboot_cache_dir + '/', '') | sort | list }}"
    delegate_to: localhost
    run_once: true
  ```

- [ ] **Step 7: Run render with sample inventory (the test step)**

  Create a temporary test inventory `/tmp/netboot-render-test.yml`:

  ```yaml
  ---
  netboot_entries:
    - id: debian-12-preseed
      name: "Debian 12 (preseed)"
      kind: kernel
      kernel: https://example.org/linux
      initrd: https://example.org/initrd.gz
      cmdline: "auto=true"
    - id: talos-1.9
      name: "Talos 1.9"
      kind: iso
      url: https://example.org/talos.iso
      sha256: 0000000000000000000000000000000000000000000000000000000000000000
    - id: rocky-9-ks
      name: "Rocky 9 (kickstart)"
      kind: chainload
      url: ${netboot_self}/assets/kickstart/rocky9.ipxe

  netboot_host_pins:
    - mac: aa:bb:cc:dd:ee:ff
      hostname: worker-01.test
      entry: talos-1.9
  ```

  Run:

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --tags render -e netbootxyz_host=localhost \
    -e @/tmp/netboot-render-test.yml \
    --mode stdout
  ```

  Expected: completes successfully, the final debug shows files including `menu.ipxe`, `entries/debian-12-preseed.ipxe`, `entries/talos-1.9.ipxe`, `entries/rocky-9-ks.ipxe`, `host/MAC-aabbccddeeff.ipxe`, `host/HOSTNAME-worker-01.test.ipxe`.

- [ ] **Step 8: Eyeball the rendered output**

  ```bash
  cat .cache/netboot-menus/menu.ipxe
  cat .cache/netboot-menus/entries/rocky-9-ks.ipxe
  cat .cache/netboot-menus/host/MAC-aabbccddeeff.ipxe
  ```

  Expected:
  - `menu.ipxe` starts with `#!ipxe`, includes the `:per_host`/`:check_mac`/`:main_menu` block, lists all three entries, and has a `goto` per entry.
  - `entries/rocky-9-ks.ipxe` has `chain http://10.10.45.242/assets/kickstart/rocky9.ipxe` (the `${netboot_self}` substitution worked).
  - `host/MAC-aabbccddeeff.ipxe` chains to `entries/talos-1.9.ipxe`.

  Delete `/tmp/netboot-render-test.yml` after.

- [ ] **Step 9: Lint**

  ```bash
  ansible-lint --profile=production playbooks/netboot/
  yamllint playbooks/netboot/
  ```

- [ ] **Step 10: Commit**

  ```bash
  git add playbooks/netboot/templates playbooks/netboot/tasks/render_menu.yml
  git commit -m "Add render stage with menu, entry, and host-pin templates"
  ```

---

## Task 4: push_text stage

**Files:**
- Create: `playbooks/netboot/tasks/push_text.yml`

This task syncs the rendered cache + the static `files/{kickstart,cloud-init}/` content to the TrueNAS netbootxyz container. Two destinations for the menus: `/config/menus/` (canonical) and `/config/menus/local/` (netbootxyz overwrite workaround).

- [ ] **Step 1: Create `playbooks/netboot/tasks/push_text.yml`**

  Full content:

  ```yaml
  ---
  # Sync rendered menus + static assets text to TrueNAS. Scoped synchronize
  # with delete=true is bounded to subdirs we own: menus/entries/, menus/host/,
  # menus/fragments/. Flat menus/ is left alone so the OpenShift add-node files
  # survive.

  - name: Push — define paths
    ansible.builtin.set_fact:
      _netboot_menus_dst: "{{ netbootxyz_root }}/config/menus"
      _netboot_assets_dst: "{{ netbootxyz_root }}/assets"
      _netboot_cache_dir: "{{ playbook_dir }}/../../.cache/netboot-menus"
      _netboot_fragments_src: "{{ playbook_dir }}/files/fragments"
      _netboot_kickstart_src: "{{ playbook_dir }}/files/kickstart"
      _netboot_cloudinit_src: "{{ playbook_dir }}/files/cloud-init"

  - name: Push — ensure destination subdirs exist
    ansible.builtin.file:
      path: "{{ item }}"
      state: directory
      owner: "1000"
      group: "1000"
      mode: "0755"
    loop:
      - "{{ _netboot_menus_dst }}"
      - "{{ _netboot_menus_dst }}/entries"
      - "{{ _netboot_menus_dst }}/host"
      - "{{ _netboot_menus_dst }}/fragments"
      - "{{ _netboot_menus_dst }}/local"
      - "{{ _netboot_menus_dst }}/local/entries"
      - "{{ _netboot_menus_dst }}/local/host"
      - "{{ _netboot_menus_dst }}/local/fragments"
      - "{{ _netboot_assets_dst }}/kickstart"
      - "{{ _netboot_assets_dst }}/cloud-init"

  - name: Push — top-level menu.ipxe
    ansible.builtin.copy:
      src: "{{ _netboot_cache_dir }}/menu.ipxe"
      dest: "{{ item }}/menu.ipxe"
      owner: "1000"
      group: "1000"
      mode: "0644"
    loop:
      - "{{ _netboot_menus_dst }}"
      - "{{ _netboot_menus_dst }}/local"

  - name: Push — entries/ (with delete-extras)
    ansible.posix.synchronize:
      src: "{{ _netboot_cache_dir }}/entries/"
      dest: "{{ item }}/entries/"
      delete: true
      recursive: true
      use_ssh_args: true
      rsync_opts:
        - "--chown=1000:1000"
        - "--chmod=F0644,D0755"
    loop:
      - "{{ _netboot_menus_dst }}"
      - "{{ _netboot_menus_dst }}/local"

  - name: Push — host/ (with delete-extras)
    ansible.posix.synchronize:
      src: "{{ _netboot_cache_dir }}/host/"
      dest: "{{ item }}/host/"
      delete: true
      recursive: true
      use_ssh_args: true
      rsync_opts:
        - "--chown=1000:1000"
        - "--chmod=F0644,D0755"
    loop:
      - "{{ _netboot_menus_dst }}"
      - "{{ _netboot_menus_dst }}/local"

  - name: Push — fragments/ (with delete-extras)
    ansible.posix.synchronize:
      src: "{{ _netboot_fragments_src }}/"
      dest: "{{ item }}/fragments/"
      delete: true
      recursive: true
      use_ssh_args: true
      rsync_opts:
        - "--chown=1000:1000"
        - "--chmod=F0644,D0755"
        - "--exclude=.gitkeep"
    loop:
      - "{{ _netboot_menus_dst }}"
      - "{{ _netboot_menus_dst }}/local"

  - name: Push — kickstart/
    ansible.posix.synchronize:
      src: "{{ _netboot_kickstart_src }}/"
      dest: "{{ _netboot_assets_dst }}/kickstart/"
      delete: false
      recursive: true
      use_ssh_args: true
      rsync_opts:
        - "--chown=1000:1000"
        - "--chmod=F0644,D0755"
        - "--exclude=.gitkeep"

  - name: Push — cloud-init/
    ansible.posix.synchronize:
      src: "{{ _netboot_cloudinit_src }}/"
      dest: "{{ _netboot_assets_dst }}/cloud-init/"
      delete: false
      recursive: true
      use_ssh_args: true
      rsync_opts:
        - "--chown=1000:1000"
        - "--chmod=F0644,D0755"
        - "--exclude=.gitkeep"
  ```

  > **Why `synchronize` not `copy`:** synchronize handles the delete-extras semantics natively. It requires rsync at both ends (TrueNAS has rsync available in the standard image). If the EE doesn't ship `ansible.posix`, install via `requirements.yml` — check first.

- [ ] **Step 2: Verify `ansible.posix.synchronize` is available in the EE**

  ```bash
  grep -A 5 'collections:' requirements.yml | head -20
  ```

  Expected: `ansible.posix` listed. If not, append:

  ```yaml
  collections:
    - name: ansible.posix
      version: ">=1.5.0"
  ```

  And bump the EE rebuild before running. Note that as a pure-rsync-wrapper, `ansible.posix.synchronize` may already be available transitively. If unsure, run `ansible-doc ansible.posix.synchronize` to confirm.

- [ ] **Step 3: Dry-run against the live TrueNAS (the test step)**

  Use the same render-test inventory from Task 3 (recreate `/tmp/netboot-render-test.yml`).

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --tags render,push \
    -e @/tmp/netboot-render-test.yml \
    --check --diff \
    --mode stdout
  ```

  Expected: render runs as before; push reports each `synchronize` task as `changed=true` listing `menu.ipxe`, `entries/*.ipxe`, `host/*.ipxe`. No file is shown being deleted (the live `menus/` should not have `entries/` / `host/` subdirs yet — we're creating them). If you see deletions of unrelated files, STOP — the scope is wrong.

- [ ] **Step 4: Real-run with the empty inventory (no entries)**

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --tags render,push \
    --mode stdout
  ```

  Expected: empty `entries/` and `host/` directories are created (and `local/` mirrors), `menu.ipxe` is written with no entries. Re-run → `changed=0`.

- [ ] **Step 5: Verify on TrueNAS**

  SSH (or via `ansible -m shell`):

  ```bash
  ansible -i igou-inventory/inventory.yaml truenas -m ansible.builtin.command \
    -a 'ls -la /mnt/ssd/containers/netbootxyz/config/menus/'
  ```

  Expected: `entries/`, `host/`, `fragments/`, `local/`, and `menu.ipxe` exist. `local/` contains a parallel structure.

- [ ] **Step 6: Lint and commit**

  ```bash
  ansible-lint --profile=production playbooks/netboot/
  yamllint playbooks/netboot/
  git add playbooks/netboot/tasks/push_text.yml requirements.yml
  git commit -m "Add push_text stage with scoped synchronize to /config/menus and /assets"
  ```

  (Skip `requirements.yml` from the add if you didn't have to modify it.)

---

## Task 5: fetch_binaries stage

**Files:**
- Create: `playbooks/netboot/tasks/fetch_binaries.yml`

This task downloads `kind: iso` URLs (and optionally `kind: kernel` with `cache: true`) into `/assets/iso/<id>.iso` (and `/assets/cache/<id>/{vmlinuz,initrd}`) on the TrueNAS host. Idempotent via sha256.

- [ ] **Step 1: Create `playbooks/netboot/tasks/fetch_binaries.yml`**

  Full content:

  ```yaml
  ---
  # Idempotent download of upstream URLs into the netbootxyz container's
  # /assets directory. Runs against netbootxyz_host so the bytes never round-
  # trip through the control node.

  - name: Fetch — ensure iso/ directory exists
    ansible.builtin.file:
      path: "{{ netbootxyz_root }}/assets/iso"
      state: directory
      owner: "1000"
      group: "1000"
      mode: "0755"

  - name: Fetch — download kind=iso (sha256-checked)
    ansible.builtin.get_url:
      url: "{{ item.url }}"
      dest: "{{ netbootxyz_root }}/assets/iso/{{ item.id }}.iso"
      checksum: "sha256:{{ item.sha256 }}"
      owner: "1000"
      group: "1000"
      mode: "0644"
      force: false
    loop: "{{ netboot_entries | selectattr('kind', 'equalto', 'iso') | list }}"
    loop_control:
      label: "{{ item.id }}"

  - name: Fetch — ensure cache/ directory exists for kind=kernel cache=true
    ansible.builtin.file:
      path: "{{ netbootxyz_root }}/assets/cache/{{ item.id }}"
      state: directory
      owner: "1000"
      group: "1000"
      mode: "0755"
    loop: >-
      {{ netboot_entries
         | selectattr('kind', 'equalto', 'kernel')
         | selectattr('cache', 'defined')
         | selectattr('cache', 'equalto', true)
         | list }}
    loop_control:
      label: "{{ item.id }}"

  - name: Fetch — download kind=kernel cache=true vmlinuz
    ansible.builtin.get_url:
      url: "{{ item.kernel }}"
      dest: "{{ netbootxyz_root }}/assets/cache/{{ item.id }}/vmlinuz"
      checksum: "{{ ('sha256:' + item.kernel_sha256) if item.kernel_sha256 is defined else omit }}"
      owner: "1000"
      group: "1000"
      mode: "0644"
      force: false
    loop: >-
      {{ netboot_entries
         | selectattr('kind', 'equalto', 'kernel')
         | selectattr('cache', 'defined')
         | selectattr('cache', 'equalto', true)
         | list }}
    loop_control:
      label: "{{ item.id }} vmlinuz"

  - name: Fetch — download kind=kernel cache=true initrd
    ansible.builtin.get_url:
      url: "{{ item.initrd }}"
      dest: "{{ netbootxyz_root }}/assets/cache/{{ item.id }}/initrd"
      checksum: "{{ ('sha256:' + item.initrd_sha256) if item.initrd_sha256 is defined else omit }}"
      owner: "1000"
      group: "1000"
      mode: "0644"
      force: false
    loop: >-
      {{ netboot_entries
         | selectattr('kind', 'equalto', 'kernel')
         | selectattr('cache', 'defined')
         | selectattr('cache', 'equalto', true)
         | list }}
    loop_control:
      label: "{{ item.id }} initrd"
  ```

- [ ] **Step 2: Test fetch with a small known-good ISO (the test step)**

  Pick a *small* test artifact — any tiny ISO with a known sha256. For this test, use Alpine standard mini-ISO (~5 MB).

  Create `/tmp/netboot-fetch-test.yml`:

  ```yaml
  ---
  netboot_entries:
    - id: alpine-test
      name: "Alpine test"
      kind: iso
      url: https://dl-cdn.alpinelinux.org/alpine/v3.19/releases/x86_64/alpine-standard-3.19.0-x86_64.iso
      sha256: 0c2e69e6d1d68ba3f5bf26a8baddc44dbf2fffb31c8b03f4d2a23c1cdca78a2c
  netboot_host_pins: []
  ```

  > **Note:** verify the sha256 against the upstream `.sha256` file at run time — Alpine rotates artifacts. If the checksum no longer matches, get_url will fail loud, which is exactly the behavior we're testing.

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --tags fetch \
    -e @/tmp/netboot-fetch-test.yml \
    --mode stdout
  ```

  Expected: download completes, stat on TrueNAS shows `/mnt/ssd/containers/netbootxyz/assets/iso/alpine-test.iso` exists with the right size.

- [ ] **Step 3: Verify idempotency**

  Run the same command again. Expected: `changed=0`. The file is already there with the matching checksum, so `get_url force: false` is a no-op.

- [ ] **Step 4: Verify checksum mismatch path**

  Edit `/tmp/netboot-fetch-test.yml` to corrupt one hex char in the sha256. Re-run:

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --tags fetch \
    -e @/tmp/netboot-fetch-test.yml \
    --mode stdout
  ```

  Expected: failure with "checksum did not match". This is `get_url`'s built-in behavior; we just need to verify it does fire. Restore the correct sha256 after.

- [ ] **Step 5: Cleanup test fixture**

  ```bash
  ansible -i igou-inventory/inventory.yaml truenas -m ansible.builtin.file \
    -a 'path=/mnt/ssd/containers/netbootxyz/assets/iso/alpine-test.iso state=absent' \
    -b
  rm /tmp/netboot-fetch-test.yml
  ```

- [ ] **Step 6: Lint and commit**

  ```bash
  ansible-lint --profile=production playbooks/netboot/
  yamllint playbooks/netboot/
  git add playbooks/netboot/tasks/fetch_binaries.yml
  git commit -m "Add fetch_binaries stage with sha256-checked get_url"
  ```

---

## Task 6: push_local_artifacts stage

**Files:**
- Create: `playbooks/netboot/tasks/push_local_artifacts.yml`

This task copies locally-built kernels/initrds from a control-node path into `/assets/local/<id>/{vmlinuz,initrd}` on TrueNAS, for `kind: local` entries.

- [ ] **Step 1: Create `playbooks/netboot/tasks/push_local_artifacts.yml`**

  Full content:

  ```yaml
  ---
  # Copy locally-built kernel/initrd artifacts into the netbootxyz /assets tree.
  # Source path comes from the entry's kernel_src/initrd_src; destination is
  # /assets/local/<id>/. Idempotent via copy's checksum comparison.

  - name: Local — define filtered list
    ansible.builtin.set_fact:
      _netboot_local_entries: "{{ netboot_entries | selectattr('kind', 'equalto', 'local') | list }}"

  - name: Local — ensure /assets/local/<id> exists
    ansible.builtin.file:
      path: "{{ netbootxyz_root }}/assets/local/{{ item.id }}"
      state: directory
      owner: "1000"
      group: "1000"
      mode: "0755"
    loop: "{{ _netboot_local_entries }}"
    loop_control:
      label: "{{ item.id }}"

  - name: Local — copy vmlinuz
    ansible.builtin.copy:
      src: "{{ item.kernel_src }}"
      dest: "{{ netbootxyz_root }}/assets/local/{{ item.id }}/vmlinuz"
      owner: "1000"
      group: "1000"
      mode: "0644"
    loop: "{{ _netboot_local_entries }}"
    loop_control:
      label: "{{ item.id }} vmlinuz"

  - name: Local — copy initrd
    ansible.builtin.copy:
      src: "{{ item.initrd_src }}"
      dest: "{{ netbootxyz_root }}/assets/local/{{ item.id }}/initrd"
      owner: "1000"
      group: "1000"
      mode: "0644"
    loop: "{{ _netboot_local_entries }}"
    loop_control:
      label: "{{ item.id }} initrd"
  ```

- [ ] **Step 2: Smoke test with a small fake artifact (the test step)**

  Create local fixtures:

  ```bash
  mkdir -p /tmp/netboot-local-test
  echo "fake kernel" > /tmp/netboot-local-test/vmlinuz
  echo "fake initrd" > /tmp/netboot-local-test/initrd.img
  ```

  Create `/tmp/netboot-local-test.yml`:

  ```yaml
  ---
  netboot_entries:
    - id: localtest
      name: "Local artifact test"
      kind: local
      kernel_src: /tmp/netboot-local-test/vmlinuz
      initrd_src: /tmp/netboot-local-test/initrd.img
      cmdline: "console=ttyS0"
  netboot_host_pins: []
  ```

  Run:

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --tags local \
    -e @/tmp/netboot-local-test.yml \
    --mode stdout
  ```

  Expected: copy completes, stat on TrueNAS shows the two files.

- [ ] **Step 3: Verify idempotency**

  Re-run. Expected: `changed=0`.

- [ ] **Step 4: Cleanup**

  ```bash
  ansible -i igou-inventory/inventory.yaml truenas -m ansible.builtin.file \
    -a 'path=/mnt/ssd/containers/netbootxyz/assets/local/localtest state=absent' \
    -b
  rm -rf /tmp/netboot-local-test /tmp/netboot-local-test.yml
  ```

- [ ] **Step 5: Lint and commit**

  ```bash
  ansible-lint --profile=production playbooks/netboot/
  yamllint playbooks/netboot/
  git add playbooks/netboot/tasks/push_local_artifacts.yml
  git commit -m "Add push_local_artifacts stage for kind=local entries"
  ```

---

## Task 7: verify stage

**Files:**
- Create: `playbooks/netboot/tasks/verify.yml`

This task does HTTP probes from the control node against the netbootxyz container's HTTP root. Catches misconfigured pins, missing entries, and broken renders at deploy time.

- [ ] **Step 1: Create `playbooks/netboot/tasks/verify.yml`**

  Full content:

  ```yaml
  ---
  # HTTP probes against the running netbootxyz container.
  # delegate_to: localhost — the probes run from wherever the playbook runs,
  # not from inside TrueNAS.

  - name: Verify — top-level menu.ipxe is reachable and well-formed
    ansible.builtin.uri:
      url: "{{ netbootxyz_self_url }}/menu.ipxe"
      return_content: true
      status_code: 200
    register: _verify_menu
    delegate_to: localhost
    run_once: true
    failed_when:
      - _verify_menu.status != 200
      - "'#!ipxe' not in (_verify_menu.content | default(''))"

  - name: Verify — every entry's name appears in menu.ipxe
    ansible.builtin.assert:
      that:
        - "item.name in _verify_menu.content"
      fail_msg: "Entry {{ item.id }} ({{ item.name }}) not found in served menu.ipxe."
    loop: "{{ netboot_entries }}"
    loop_control:
      label: "{{ item.id }}"
    delegate_to: localhost
    run_once: true

  - name: Verify — each entry .ipxe is reachable
    ansible.builtin.uri:
      url: "{{ netbootxyz_self_url }}/menus/entries/{{ item.id }}.ipxe"
      method: GET
      status_code: 200
    loop: "{{ netboot_entries }}"
    loop_control:
      label: "{{ item.id }}"
    delegate_to: localhost
    run_once: true

  - name: Verify — each kind=iso asset is reachable
    ansible.builtin.uri:
      url: "{{ netbootxyz_self_url }}/assets/iso/{{ item.id }}.iso"
      method: HEAD
      status_code: 200
    loop: "{{ netboot_entries | selectattr('kind', 'equalto', 'iso') | list }}"
    loop_control:
      label: "{{ item.id }}"
    delegate_to: localhost
    run_once: true

  - name: Verify — each host pin MAC file is reachable
    ansible.builtin.uri:
      url: "{{ netbootxyz_self_url }}/menus/host/MAC-{{ item.mac | lower | replace(':', '') }}.ipxe"
      method: GET
      status_code: 200
    loop: "{{ netboot_host_pins }}"
    loop_control:
      label: "{{ item.mac }}"
    delegate_to: localhost
    run_once: true

  - name: Verify — each host pin HOSTNAME file is reachable (when set)
    ansible.builtin.uri:
      url: "{{ netbootxyz_self_url }}/menus/host/HOSTNAME-{{ item.hostname }}.ipxe"
      method: GET
      status_code: 200
    loop: "{{ netboot_host_pins | selectattr('hostname', 'defined') | list }}"
    loop_control:
      label: "{{ item.hostname }}"
    delegate_to: localhost
    run_once: true

  - name: Verify — summary
    ansible.builtin.debug:
      msg:
        - "menu.ipxe served (length {{ _verify_menu.content | length }} bytes)"
        - "entries verified: {{ netboot_entries | length }}"
        - "host pins verified: {{ netboot_host_pins | length }}"
    delegate_to: localhost
    run_once: true
  ```

- [ ] **Step 2: Run end-to-end against the live container**

  Use the empty inventory (Task 1):

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --mode stdout
  ```

  Expected: every stage completes; verify reports menu.ipxe served, 0 entries, 0 host pins.

- [ ] **Step 3: Lint and commit**

  ```bash
  ansible-lint --profile=production playbooks/netboot/
  yamllint playbooks/netboot/
  git add playbooks/netboot/tasks/verify.yml
  git commit -m "Add verify stage with HTTP probes for menu, entries, ISOs, and host pins"
  ```

---

## Task 8: One-time cutover from `igou-node-bootstrap`

**Files:**
- Modify: `igou-inventory/group_vars/all/netboot.yml` (populate from existing content)
- Maybe-create: `playbooks/netboot/files/{kickstart,cloud-init,fragments}/<existing files>`

This is *manual translation work*. The playbook does not auto-import. The operator decides what becomes a declarative entry vs. a fragment vs. a kickstart asset.

- [ ] **Step 1: Inventory existing custom content**

  ```bash
  ls -la ~/igou-node-bootstrap/netbootxyz-menus/
  ls -la ~/igou-node-bootstrap/cloud-init/ 2>/dev/null
  ls -la ~/igou-node-bootstrap/kickstart/ 2>/dev/null
  ```

  Note each `.ipxe` file under `netbootxyz-menus/` and decide for each:
  - **Convert to a `netboot_entries` item** if it boots a single OS/installer with a clear kernel/initrd or ISO.
  - **Drop into `playbooks/netboot/files/fragments/`** if it has bespoke iPXE syntax (multiple choose menus, conditional logic).

- [ ] **Step 2: Translate the simple entries**

  For each "simple" entry, edit `igou-inventory/group_vars/all/netboot.yml` and add to `netboot_entries:`. Use the four kinds documented in the spec.

  Example: if `~/igou-node-bootstrap/netbootxyz-menus/talos.ipxe` was a `chain` to `https://factory.talos.dev/...`, translate it into:

  ```yaml
  - id: talos-1.9
    name: "Talos 1.9"
    kind: chainload
    url: https://factory.talos.dev/...
  ```

- [ ] **Step 3: Copy the fragments**

  ```bash
  cp ~/igou-node-bootstrap/netbootxyz-menus/<complex-file>.ipxe \
     playbooks/netboot/files/fragments/
  ```

  Repeat per fragment. Add a header comment (manually, top of file) that says "originated in igou-node-bootstrap on YYYY-MM-DD" so future-you can find it.

- [ ] **Step 4: Copy kickstart and cloud-init**

  ```bash
  rsync -av ~/igou-node-bootstrap/cloud-init/ playbooks/netboot/files/cloud-init/
  rsync -av ~/igou-node-bootstrap/kickstart/  playbooks/netboot/files/kickstart/
  ```

  Replace any embedded URLs that pointed to the old paths so they land at `/assets/cloud-init/...` and `/assets/kickstart/...` under the new layout. Grep for `boot-files` and replace with `assets/`.

- [ ] **Step 5: Add per-host pins**

  If the operator has any hosts that should always boot a specific recipe, add to `netboot_host_pins:` in `igou-inventory/group_vars/all/netboot.yml`. Skip if there are none today.

- [ ] **Step 6: Render-check the new inventory**

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --tags render -e netbootxyz_host=localhost \
    --mode stdout
  ```

  Expected: no preflight errors. If preflight fails, fix the inventory entry and re-run.

  Eyeball `.cache/netboot-menus/menu.ipxe` and confirm the listed items match expectations.

- [ ] **Step 7: Real-deploy the cutover**

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --mode stdout
  ```

  Expected: every stage completes, verify passes. Manually browse to `http://10.10.45.242` in a browser and click around the netbootxyz menu — your custom entries should appear.

- [ ] **Step 8: Smoke-test with a real PXE client**

  PXE-boot a test machine (or KubeVirt VM) and confirm:
  - The menu appears.
  - At least one custom entry boots.
  - If you set up a host pin, that machine bypasses the menu and lands on the pinned recipe.

  This is the ultimate proof. If anything is off, fix in inventory or a fragment file and re-run with `--tags render,push,verify`.

- [ ] **Step 9: Commit (two repos)**

  In `igou-inventory`:

  ```bash
  cd igou-inventory
  git add group_vars/all/netboot.yml
  git commit -m "Populate netboot_entries from igou-node-bootstrap cutover"
  cd -
  ```

  In `igou-ansible`:

  ```bash
  git add playbooks/netboot/files/
  git commit -m "Migrate custom iPXE fragments, kickstart, and cloud-init from igou-node-bootstrap"
  ```

---

## Task 9: Delete the legacy playbooks

**Files:**
- Delete: `playbooks/truenas/configure_netbootxyz.yml`
- Delete: `playbooks/truenas/sync_boot_files.yml`
- Modify: `igou-inventory/group_vars/truenas.yml` (drop `truenas_boot_files_*` block)

Only do this *after* Task 8's smoke test passed end-to-end.

- [ ] **Step 1: Confirm the new playbook owns everything**

  Re-run the full suite once more to be safe:

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --mode stdout
  ```

  Expected: `changed=0` (idempotent re-run). Confirms the new playbook is fully effective and you're not about to delete a still-in-use playbook.

- [ ] **Step 2: Delete the legacy playbooks**

  ```bash
  git rm playbooks/truenas/configure_netbootxyz.yml playbooks/truenas/sync_boot_files.yml
  ```

- [ ] **Step 3: Drop `truenas_boot_files_*` from inventory**

  Open `igou-inventory/group_vars/truenas.yml`. Find any `truenas_boot_files_source_dir`, `truenas_boot_files_dest_base`, `truenas_boot_files_owner`, `truenas_boot_files_group` keys and remove them. Verify by grep:

  ```bash
  grep -n truenas_boot_files igou-inventory/group_vars/truenas.yml
  ```

  Expected: no matches.

- [ ] **Step 4: Lint**

  ```bash
  ansible-lint --profile=production
  yamllint .
  ```

- [ ] **Step 5: Commit (two repos)**

  In `igou-ansible`:

  ```bash
  git add playbooks/truenas/
  git commit -m "Remove configure_netbootxyz.yml and sync_boot_files.yml (replaced by playbooks/netboot/deploy_assets.yml)"
  ```

  In `igou-inventory`:

  ```bash
  cd igou-inventory
  git add group_vars/truenas.yml
  git commit -m "Drop truenas_boot_files_* vars (replaced by netboot_entries)"
  cd -
  ```

---

## Task 10: Final integration check

This task verifies the whole thing one more time in a representative way and produces evidence the cutover is done.

- [ ] **Step 1: Idempotent re-run on the canonical inventory**

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --mode stdout
  ```

  Expected: `changed=0` across all stages.

- [ ] **Step 2: Touch-up scenario**

  Edit one entry's `cmdline:` in `igou-inventory/group_vars/all/netboot.yml` (e.g., add a benign `quiet` flag). Re-run with the targeted tag set:

  ```bash
  ansible-navigator run playbooks/netboot/deploy_assets.yml \
    -i igou-inventory/inventory.yaml \
    --tags render,push,verify \
    --mode stdout
  ```

  Expected: only `entries/<that-id>.ipxe` is reported as changed in the push stage. Verify still passes.

  Revert the change and re-run to confirm it goes back to `changed=0`.

- [ ] **Step 3: Lint pass**

  ```bash
  ansible-lint --profile=production
  yamllint .
  pre-commit run --all-files
  ```

  All clean.

- [ ] **Step 4: Final summary commit (if any drift)**

  If lint pass produced fixups (whitespace, key order), commit them:

  ```bash
  git status
  git add -A
  git commit -m "Final lint cleanup for netboot deploy_assets cutover"
  ```

- [ ] **Step 5: Open PR**

  ```bash
  git push -u origin <feature-branch>
  gh pr create --title "Replace netbootxyz menu/asset playbooks with declarative deploy_assets" \
    --body "$(cat <<'EOF'
  ## Summary
  - Replaces playbooks/truenas/configure_netbootxyz.yml and sync_boot_files.yml with a single declarative-plus-fragments playbook tree at playbooks/netboot/.
  - Eliminates the hardcoded /home/igou/igou-node-bootstrap/netbootxyz-menus path so AAP/AWX runs work.
  - Adds per-host PXE pins by MAC/hostname, auto-served via a header in the generated menu.ipxe.
  - Spec: docs/superpowers/specs/2026-05-08-netboot-asset-management-design.md.

  ## Test plan
  - [ ] All stages run idempotently against TrueNAS (changed=0 on second pass).
  - [ ] Touching one entry's cmdline causes only that file to update.
  - [ ] HTTP probes pass for menu.ipxe, every entry, every host pin.
  - [ ] PXE-booting a test machine shows the new menu and a known entry boots.
  - [ ] PXE-booting a host with a netboot_host_pins entry skips the menu and goes directly to the pinned recipe.
  - [ ] ansible-lint --profile=production and yamllint clean.

  Companion PR in igou-inventory must merge alongside.

  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  EOF
  )"
  ```

  Companion `igou-inventory` PR — push + open separately.

---

## Self-review summary (against the spec)

Cross-check of every spec section to a task that implements it:

| Spec section | Task |
|---|---|
| Goals (in-repo source, declarative entries, per-host pins, fragments, idempotent, host abstraction) | 1, 2, 3, 4, 5, 6, 7 |
| Non-goals (OpenShift untouched; iPXE binaries untouched; multi-host) | scoped delete in Task 4; binaries playbook untouched |
| File layout | Task 2 (orchestrator, preflight, dirs), 3 (templates + render), 4-7 (stages) |
| Inventory schema (`netboot_entries`, `netboot_host_pins`) | Task 1 (file), Task 2 (validation), Task 3 (templating) |
| Generated layout on TrueNAS | Task 4 (push) |
| Generated `menu.ipxe` header (per-host hook) | Task 3 (template) |
| Playbook stages | Task 2 (orchestrator), 2-7 (per-stage) |
| Idempotency model | Task 4-6 (synchronize/get_url/copy semantics), Task 10 (verification) |
| Migration plan (single-PR cutover) | Task 8 (translation), Task 9 (legacy delete), Task 10 (PR) |
| Risks & mitigations | scope of `synchronize delete=true` (Task 4 step 1 + 3); `${mac:hexraw}` (Task 3 menu template); ISO checksum required (Task 2 preflight + Task 5 fetch) |
| Testing strategy | Task 2 step 5 (preflight failure path), Task 3 step 7 (render with sample), Task 4 step 3 (check-diff), Task 5 step 4 (checksum mismatch), Task 6 step 3 (idempotency), Task 8 step 8 (real PXE), Task 10 step 2 (touch-up scenario) |
| Repo-level changes | Task 1 (.gitignore + group_vars split), Task 9 (truenas.yml cleanup) |
| Documentation | Task 2 step 1 (header comment block in deploy_assets.yml) |

No gaps detected.
