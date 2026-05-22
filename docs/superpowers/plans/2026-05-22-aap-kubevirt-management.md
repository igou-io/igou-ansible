# AAP-driven KubeVirt VM management + dynamic inventory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the cluster RBAC, gitops, and AAP live-state changes needed for AAP to provision/manage KubeVirt VMs (including a burst-node opt-in) and to dynamically inventory those VMs.

**Architecture:** Three repos. `igou-openshift` gets the ServiceAccounts moved + applied. `igou-inventory` gets a new kubevirt.core.kubevirt plugin file + AAP inventory-source definition + new `vm_target_burst` extra_var. `igou-ansible` gets `virtualmachine-manage.yml` rewritten without the David-Igou role. Because 1Password is rate-limited, gitops changes are committed (and pushed, for the AAP-consumed projects) but **not synced via the playbook**; instead, AAP live state is updated directly via the AAP API. Credentials are PATCHed with real cluster-extracted tokens; the new inventory source and job-template extra_var are created/patched via direct API.

**Tech Stack:** OpenShift 4.21, KubeVirt (OpenShift Virtualization), CAPI/CAPM3 (for the casval burst node), kustomize, AAP 2.5, `kubernetes.core` + `kubevirt.core` Ansible collections.

**Spec:** `docs/superpowers/specs/2026-05-22-aap-kubevirt-management-design.md`

---

## Pre-flight

Confirm these are exported in the working shell (the devcontainer has them):

```bash
echo "${AAP_HOSTNAME:?AAP_HOSTNAME not set}" >/dev/null
echo "${AAP_USERNAME:?AAP_USERNAME not set}" >/dev/null
echo "${AAP_PASSWORD:?AAP_PASSWORD not set}" >/dev/null
oc whoami >/dev/null   # confirms KUBECONFIG works against the ocp cluster
```

If any of these fail, stop and fix shell setup before proceeding.

Define two shell helpers used by several tasks (paste once at the start of the working shell; they persist for the rest of the session):

```bash
aap_wait_job() {
  # $1 = AAP job id. Polls /jobs/<id>/ until terminal status; fails non-zero on failed/error/canceled.
  local JID="$1"
  for i in $(seq 1 360); do
    local STATUS
    STATUS=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
      "${AAP_HOSTNAME%/}/api/controller/v2/jobs/${JID}/" \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    case "$STATUS" in
      successful)  echo "✅ job ${JID} successful"; return 0 ;;
      failed|error|canceled)
        echo "❌ job ${JID} ${STATUS} — last 50 lines of stdout:"
        curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
          "${AAP_HOSTNAME%/}/api/controller/v2/jobs/${JID}/stdout/?format=txt" | tail -50
        return 1 ;;
    esac
    sleep 5
  done
  echo "❌ job ${JID} did not finish within 30 min"
  return 1
}

aap_wait_source_update() {
  # $1 = inventory source id. Polls /inventory_sources/<id>/ for the last sync's status.
  local SID="$1"
  for i in $(seq 1 60); do
    local STATUS
    STATUS=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
      "${AAP_HOSTNAME%/}/api/controller/v2/inventory_sources/${SID}/" \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    case "$STATUS" in
      successful)  echo "✅ inventory source ${SID} sync successful"; return 0 ;;
      failed|error|canceled)
        echo "❌ inventory source ${SID} sync ${STATUS} — last update stdout:"
        local UPDATE_ID
        UPDATE_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
          "${AAP_HOSTNAME%/}/api/controller/v2/inventory_sources/${SID}/inventory_updates/?order_by=-id&page_size=1" \
          | python3 -c "import sys,json; r=json.load(sys.stdin)['results']; print(r[0]['id'] if r else '')")
        if [ -n "$UPDATE_ID" ]; then
          curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
            "${AAP_HOSTNAME%/}/api/controller/v2/inventory_updates/${UPDATE_ID}/stdout/?format=txt" | tail -50
        fi
        return 1 ;;
    esac
    sleep 2
  done
  echo "❌ inventory source ${SID} did not finish within 2 min"
  return 1
}
```

---

## Task 1: Move SA YAMLs from `openshift-virt` → `service-accounts` (in `igou-openshift`)

**Files:**
- Move + edit: `components/openshift-virt/virtualmachine-deployer-serviceaccount.yaml` → `components/service-accounts/virtualmachine-deployer-serviceaccount.yaml`
- Move + edit: `components/openshift-virt/virtualmachine-deployer-token-secret.yaml` → `components/service-accounts/virtualmachine-deployer-token-secret.yaml`
- Move (no edit): `components/openshift-virt/virtualmachine-deployer-clusterrole.yaml` → `components/service-accounts/virtualmachine-deployer-clusterrole.yaml`
- Move + edit: `components/openshift-virt/virtualmachine-deployer-clusterrolebinding.yaml` → `components/service-accounts/virtualmachine-deployer-clusterrolebinding.yaml`
- Move + edit: `components/openshift-virt/virtualmachine-reader-serviceaccount.yaml` → `components/service-accounts/virtualmachine-reader-serviceaccount.yaml`
- Move + edit: `components/openshift-virt/virtualmachine-reader-token-secret.yaml` → `components/service-accounts/virtualmachine-reader-token-secret.yaml`
- Move (no edit): `components/openshift-virt/virtualmachine-reader-clusterrole.yaml` → `components/service-accounts/virtualmachine-reader-clusterrole.yaml`
- Move + edit: `components/openshift-virt/virtualmachine-reader-clusterrolebinding.yaml` → `components/service-accounts/virtualmachine-reader-clusterrolebinding.yaml`
- Modify: `components/service-accounts/kustomization.yaml` (add 8 new resources)
- Modify: `components/openshift-virt/kustomization.yaml` (drop 8 commented-out lines)

- [ ] **Step 1: Move all 8 files to the new directory**

```bash
cd /workspace/igou-openshift
git mv components/openshift-virt/virtualmachine-deployer-serviceaccount.yaml      components/service-accounts/
git mv components/openshift-virt/virtualmachine-deployer-token-secret.yaml        components/service-accounts/
git mv components/openshift-virt/virtualmachine-deployer-clusterrole.yaml         components/service-accounts/
git mv components/openshift-virt/virtualmachine-deployer-clusterrolebinding.yaml  components/service-accounts/
git mv components/openshift-virt/virtualmachine-reader-serviceaccount.yaml        components/service-accounts/
git mv components/openshift-virt/virtualmachine-reader-token-secret.yaml          components/service-accounts/
git mv components/openshift-virt/virtualmachine-reader-clusterrole.yaml           components/service-accounts/
git mv components/openshift-virt/virtualmachine-reader-clusterrolebinding.yaml    components/service-accounts/
```

- [ ] **Step 2: Edit the 6 files that reference a namespace** — swap `openshift-cnv` → `service-accounts`

Final contents of each (overwrite verbatim):

`components/service-accounts/virtualmachine-deployer-serviceaccount.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: virtualmachine-deployer
  namespace: service-accounts
```

`components/service-accounts/virtualmachine-deployer-token-secret.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: virtualmachine-deployer-token
  namespace: service-accounts
  annotations:
    kubernetes.io/service-account.name: virtualmachine-deployer
type: kubernetes.io/service-account-token
```

`components/service-accounts/virtualmachine-deployer-clusterrolebinding.yaml`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: virtualmachine-deployer
subjects:
  - kind: ServiceAccount
    name: virtualmachine-deployer
    namespace: service-accounts
roleRef:
  kind: ClusterRole
  name: virtualmachine-deployer
  apiGroup: rbac.authorization.k8s.io
```

`components/service-accounts/virtualmachine-reader-serviceaccount.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: virtualmachine-reader
  namespace: service-accounts
```

`components/service-accounts/virtualmachine-reader-token-secret.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: virtualmachine-reader-token
  namespace: service-accounts
  annotations:
    kubernetes.io/service-account.name: virtualmachine-reader
type: kubernetes.io/service-account-token
```

`components/service-accounts/virtualmachine-reader-clusterrolebinding.yaml`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: virtualmachine-reader
subjects:
  - kind: ServiceAccount
    name: virtualmachine-reader
    namespace: service-accounts
roleRef:
  kind: ClusterRole
  name: virtualmachine-reader
  apiGroup: rbac.authorization.k8s.io
```

The two ClusterRole files (`virtualmachine-deployer-clusterrole.yaml`, `virtualmachine-reader-clusterrole.yaml`) are **not** edited — their rules are cluster-scoped and don't reference a namespace.

- [ ] **Step 3: Add the 8 new resources to `components/service-accounts/kustomization.yaml`**

Append to the `resources:` list (preserve everything below it, including `helmGlobals` and `helmCharts`):

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - service-accounts-namespace.yaml
  - cluster-read-only-serviceaccount.yaml
  - cluster-read-only-token-secret.yaml
  - cluster-read-only-clusterrolebinding.yaml
  - cluster-read-only-monitoring-clusterrolebinding.yaml
  - cluster-edit-serviceaccount.yaml
  - cluster-edit-token-secret.yaml
  - claude-edit-serviceaccount.yaml
  - claude-edit-token-secret.yaml
  - ansible-molecule-serviceaccount.yaml
  - ansible-molecule-token-secret.yaml
  - ansible-molecule-clusterrole.yaml
  - virtualmachine-reader-serviceaccount.yaml
  - virtualmachine-reader-token-secret.yaml
  - virtualmachine-reader-clusterrole.yaml
  - virtualmachine-reader-clusterrolebinding.yaml
  - virtualmachine-deployer-serviceaccount.yaml
  - virtualmachine-deployer-token-secret.yaml
  - virtualmachine-deployer-clusterrole.yaml
  - virtualmachine-deployer-clusterrolebinding.yaml

helmGlobals:
  chartHome: ../../.helm/charts

helmCharts:
  - name: namespace-rolebindings
    releaseName: cluster-edit
    valuesFile: values.yaml
```

- [ ] **Step 4: Drop the 8 commented-out lines from `components/openshift-virt/kustomization.yaml`**

Final contents:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
- hco-operatorhub-subscription.yml
- kubevirt-hyperconverged-group-operatorgroup.yml
- kubevirt-hyperconverged.yml
- openshift-cnv-namespace.yml
- freenas-nvmeof-ssd-csi-storageprofile.yaml
- freenas-nvmeof-fast-csi-storageprofile.yaml
- freenas-nvmeof-cold-csi-storageprofile.yaml
- freenas-iscsi-fast-csi-storageprofile.yaml
- freenas-iscsi-ssd-csi-storageprofile.yaml
- freenas-iscsi-cold-csi-storageprofile.yaml
- freenas-nfs-fast-csi-storageprofile.yaml
- freenas-nfs-ssd-csi-storageprofile.yaml
- freenas-nfs-cold-csi-storageprofile.yaml
```

- [ ] **Step 5: Validate both kustomizations build**

```bash
cd /workspace/igou-openshift
kustomize build --enable-helm components/service-accounts/ > /dev/null && echo "✅ service-accounts"
kustomize build --enable-helm components/openshift-virt/ > /dev/null && echo "✅ openshift-virt"
```

Expected: both print `✅ ...`. If either fails, re-run with `2>&1` to see the error and fix.

- [ ] **Step 6: Lint**

```bash
cd /workspace/igou-openshift
make lint
```

Expected: clean exit. If yamllint complains about indentation, match the surrounding files' style.

- [ ] **Step 7: Commit**

```bash
cd /workspace/igou-openshift
git add components/openshift-virt/kustomization.yaml \
        components/service-accounts/
git commit -m "$(cat <<'EOF'
feat(service-accounts): move virtualmachine-reader/deployer SAs from openshift-virt

The two ServiceAccounts AAP uses to talk to KubeVirt
(virtualmachine-reader for inventory discovery, virtualmachine-deployer
for VM CRUD) now live in components/service-accounts/ with the rest of
the platform SAs. ClusterRoles unchanged; SAs and ClusterRoleBinding
subjects moved to the service-accounts namespace.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Apply SAs to the cluster + extract tokens

**Files:** none (cluster-side operation)

- [ ] **Step 1: Apply the updated component**

```bash
oc apply -k /workspace/igou-openshift/components/service-accounts/
```

Expected: lists each resource with `created` or `unchanged`; the four reader/deployer resources (SA + secret per SA, plus the 2 ClusterRoles + 2 ClusterRoleBindings) all `created`.

- [ ] **Step 2: Wait for token controller to populate the secrets**

```bash
for sa in reader deployer; do
  for i in $(seq 1 30); do
    if oc get secret virtualmachine-${sa}-token -n service-accounts -o jsonpath='{.data.token}' 2>/dev/null | grep -q '.'; then
      echo "✅ virtualmachine-${sa}-token populated"
      break
    fi
    sleep 1
  done
done
```

Expected: both `✅` lines within ~30s. If not, `oc describe secret virtualmachine-reader-token -n service-accounts` — token controller race conditions on first apply sometimes need the secret to be recreated (`oc delete secret virtualmachine-reader-token -n service-accounts && oc apply -k ...`).

- [ ] **Step 3: Verify the SAs have the expected verbs**

```bash
oc auth can-i list   virtualmachines.kubevirt.io -A --as=system:serviceaccount:service-accounts:virtualmachine-reader
oc auth can-i create virtualmachines.kubevirt.io -n default --as=system:serviceaccount:service-accounts:virtualmachine-deployer
oc auth can-i list   namespaces --as=system:serviceaccount:service-accounts:virtualmachine-reader
```

Expected: all three print `yes`. If any prints `no`, the ClusterRoleBinding subject namespace probably says `openshift-cnv` instead of `service-accounts` — re-check Task 1 Step 2.

- [ ] **Step 4: Extract tokens into shell variables for use by Task 5**

```bash
export VM_READER_TOKEN=$(oc get secret virtualmachine-reader-token -n service-accounts -o jsonpath='{.data.token}' | base64 -d)
export VM_DEPLOYER_TOKEN=$(oc get secret virtualmachine-deployer-token -n service-accounts -o jsonpath='{.data.token}' | base64 -d)
export K8S_API_HOST=$(oc whoami --show-server)
[ -n "$VM_READER_TOKEN" ]   && echo "✅ reader token   (${#VM_READER_TOKEN} chars)"
[ -n "$VM_DEPLOYER_TOKEN" ] && echo "✅ deployer token (${#VM_DEPLOYER_TOKEN} chars)"
echo "✅ K8S API host: $K8S_API_HOST"
```

Expected: three `✅` lines. The variables stay in the shell for Task 5.

---

## Task 3: Add kubevirt plugin file + AAP gitops updates (in `igou-inventory`)

**Files:**
- Create: `dynamic/openshift-virtualization.kubevirt.yml`
- Modify: `group_vars/aap/inventories.yml`
- Modify: `group_vars/aap/job_templates.yml`

- [ ] **Step 1: Create the dynamic inventory plugin file**

```bash
mkdir -p /workspace/igou-inventory/dynamic
```

Write `/workspace/igou-inventory/dynamic/openshift-virtualization.kubevirt.yml`:

```yaml
---
# Consumed by AAP inventory source `igou_kubevirt_ocp` (source: scm,
# source_path: dynamic/openshift-virtualization.kubevirt.yml). The
# kubevirt.core.kubevirt plugin reads K8S_AUTH_* env vars injected by
# the `virtualmachine-reader-token` credential (custom type
# `Kubernetes API Token`, see group_vars/aap/credential_types.yml).
#
# `namespaces: []` requests cluster-wide discovery — the reader SA's
# ClusterRole grants `list` on core/namespaces which the plugin needs
# to enumerate all VM namespaces. If a future cluster admin restricts
# that grant, this becomes an explicit namespace list.
plugin: kubevirt.core.kubevirt
connections:
  - namespaces: []
    api_version: v1
    annotation_variable: ansible
host_format: "{name}-{namespace}"
create_groups: true
append_base_host_name: false
use_service: true
```

- [ ] **Step 2: Add the new inventory source to `group_vars/aap/inventories.yml`**

Final contents (preserves existing `igou_inventory_github` block verbatim, adds `igou_kubevirt_ocp`):

```yaml
---
controller_inventories:
  - name: igou_inventory
    description: igou inventory
    organization: igou

controller_inventory_sources:
  - name: igou_inventory_github
    source: scm
    source_project: igou_inventory
    inventory: igou_inventory
    credential: "virtualmachine-reader-token"
    execution_environment: igou-awx-ee
    overwrite: true
    overwrite_vars: true
    update_cache_timeout: 0
    wait: true

  - name: igou_kubevirt_ocp
    source: scm
    source_project: igou_inventory
    source_path: dynamic/openshift-virtualization.kubevirt.yml
    inventory: igou_inventory
    credential: virtualmachine-reader-token
    execution_environment: igou-awx-ee
    # overwrite: true is per-source (deletes only hosts this source created),
    # so stale VMs disappear on re-sync without touching static hosts.
    overwrite: true
    overwrite_vars: true
    update_on_launch: false
    update_cache_timeout: 0
    wait: true
```

- [ ] **Step 3: Add `vm_target_burst` to the `openshift_virtualization_machine` extra_vars**

Edit `group_vars/aap/job_templates.yml`. Find the `openshift_virtualization_machine` template and insert `vm_target_burst: false` in `extra_vars`, just after `vm_state: "present"`:

```yaml
  - name: openshift_virtualization_machine
    description: Manage (create/delete) a KubeVirt VM on the SNO cluster
    labels:
      - kubevirt
    project: igou_ansible
    job_type: run
    playbook: playbooks/openshift_virtualization/virtualmachine-manage.yml
    inventory: igou_inventory
    execution_environment: igou-awx-ee
    concurrent_jobs_enabled: true
    ask_variables_on_launch: true
    verbosity: 2
    credentials:
      - virtualmachine-deployer-token
    extra_vars:
      create_namespace: false
      rebuild: false
      vm_state: "present"
      vm_target_burst: false
      vm_labels:
        created_by: "ansible"
      vm_name: vmname
      vm_instancetype: "u1.small"
      vm_namespace: "default"
      vm_devices:
        interfaces:
          - bridge: {}
            name: default
      vm_networks:
        - name: default
          pod: {}
      vm_virtualmachineclusterpreference: "rhel.9"
      vm_data_volume:
        sourceRef:
          name: rhel9
```

- [ ] **Step 4: Lint and verify inventory still parses**

```bash
cd /workspace/igou-inventory
make yamllint
ansible-inventory -i inventory.yaml --list > /dev/null && echo "✅ inventory parses"
```

Expected: yamllint clean, `✅ inventory parses`. If yamllint complains about `dynamic/openshift-virtualization.kubevirt.yml`, check the file is included in `.yamllint`'s scope — it's at the repo root so should be picked up.

- [ ] **Step 5: Commit + push**

```bash
cd /workspace/igou-inventory
git add dynamic/openshift-virtualization.kubevirt.yml \
        group_vars/aap/inventories.yml \
        group_vars/aap/job_templates.yml
git commit -m "$(cat <<'EOF'
feat(aap): add kubevirt dynamic inventory source + vm_target_burst extra_var

Adds dynamic/openshift-virtualization.kubevirt.yml as the
kubevirt.core.kubevirt plugin config for the new AAP inventory source
`igou_kubevirt_ocp`. Both inventory sources feed the same
`igou_inventory` AAP inventory — the static SCM source stays, the
kubevirt source adds every VM cluster-wide.

The openshift_virtualization_machine job template gains
`vm_target_burst: false` for opt-in scheduling on the casval burst
node.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Push** (operator-confirmed; pauses for approval before pushing to a publicly-visible branch):

```bash
cd /workspace/igou-inventory
git push origin main
```

The push is required so the `igou_inventory` AAP project's auto-sync-on-launch picks up the new plugin file. Without the push, the inventory source sync in Task 8 fails to find `dynamic/openshift-virtualization.kubevirt.yml`.

---

## Task 4: Rewrite `virtualmachine-manage.yml` (in `igou-ansible`)

**Files:**
- Modify: `playbooks/openshift_virtualization/virtualmachine-manage.yml` (full rewrite)

- [ ] **Step 1: Overwrite the playbook**

Write `/workspace/igou-ansible/playbooks/openshift_virtualization/virtualmachine-manage.yml` verbatim:

```yaml
---
# Manage a KubeVirt VirtualMachine (idempotent create/delete/rebuild).
#
# Used by AAP job template `openshift_virtualization_machine` and by
# workflow nodes in `rhel-server-e2e` / `build_ees_e2e`. Reads the
# kubevirt API via env vars injected by the `virtualmachine-deployer-token`
# credential (custom credential type `Kubernetes API Token`, see
# igou-inventory/group_vars/aap/credential_types.yml).
- name: Manage a KubeVirt VirtualMachine
  hosts: "{{ host | default('localhost') }}"
  gather_facts: false
  vars:
    create_namespace: false
    set_stat: true
    rebuild: false
    vm_state: "present"
    vm_target_burst: false
    vm_labels:
      created_by: "ansible"
    vm_name: kubevirt-manage-testing
    vm_instancetype: "u1.small"
    vm_namespace: "default"
    vm_virtualmachineclusterpreference: rhel.9
    vm_data_volume:
      sourceRef:
        name: rhel9
    vm_devices:
      interfaces:
        - bridge: {}
          name: default
    vm_networks:
      - name: default
        pod: {}
    vm_user_data: |
      #cloud-config
      ssh_pwauth: true
      chpasswd:
        expire: false

  tasks:
    - name: Ensure namespace exists
      when: create_namespace | bool
      kubernetes.core.k8s:
        state: present
        kind: Namespace
        name: "{{ vm_namespace }}"

    - name: Tear down existing VM (rebuild mode)
      when:
        - rebuild | bool
        - vm_state == "present"
      kubevirt.core.kubevirt_vm:
        name: "{{ vm_name }}"
        namespace: "{{ vm_namespace }}"
        state: absent
        wait: true
        wait_timeout: 300

    - name: Enforce VM state
      kubevirt.core.kubevirt_vm:
        name: "{{ vm_name }}"
        namespace: "{{ vm_namespace }}"
        state: "{{ vm_state }}"
        labels: "{{ vm_labels }}"
        wait: true
        wait_timeout: "{{ vm_wait_timeout | default(600) }}"
        instancetype:
          kind: virtualmachineclusterinstancetype
          name: "{{ vm_instancetype }}"
        preference:
          kind: virtualmachineclusterpreference
          name: "{{ vm_virtualmachineclusterpreference }}"
        data_volume_templates:
          - metadata:
              name: "{{ vm_name }}-volume"
            spec:
              sourceRef:
                kind: "{{ vm_data_volume.sourceRef.kind | default('DataSource') }}"
                name: "{{ vm_data_volume.sourceRef.name }}"
                namespace: "{{ vm_data_volume.sourceRef.namespace | default('openshift-virtualization-os-images') }}"
              storage:
                resources: "{{ vm_data_volume.storage.resources | default({}) }}"
        spec:
          architecture: amd64
          # Burst placement (casval). nodeSelector matches the CAPI-propagated
          # node-role label; toleration matches the workload=burst:NoSchedule
          # taint on the casval MachineSet template.
          nodeSelector: "{{ {'node-role.kubernetes.io/burst': ''} if vm_target_burst | bool else omit }}"
          tolerations: "{{ [{'key': 'workload', 'operator': 'Equal', 'value': 'burst', 'effect': 'NoSchedule'}] if vm_target_burst | bool else omit }}"
          domain:
            devices: "{{ vm_devices }}"
            resources: {}
          subdomain: headless
          networks: "{{ vm_networks }}"
          volumes:
            - dataVolume:
                name: "{{ vm_name }}-volume"
              name: rootdisk
            - cloudInitNoCloud:
                userDataBase64: "{{ vm_user_data | b64encode }}"
              name: cloudinitdisk
      register: r_vm

    - name: Set workflow stat for downstream nodes
      when:
        - set_stat | bool
        - vm_state == "present"
      ansible.builtin.set_stats:
        data:
          host: "{{ vm_namespace }}-{{ vm_name }}"
        per_host: false
```

- [ ] **Step 2: Syntax-check the playbook**

```bash
cd /workspace/igou-ansible
ansible-playbook --syntax-check playbooks/openshift_virtualization/virtualmachine-manage.yml
```

Expected: `playbook: playbooks/...virtualmachine-manage.yml` printed; no errors.

- [ ] **Step 3: Lint**

```bash
cd /workspace/igou-ansible
make yamllint
ansible-lint --profile=production playbooks/openshift_virtualization/virtualmachine-manage.yml
```

Expected: both clean. If ansible-lint flags `no-changed-when` or similar on the `set_stats` task, fix per its suggestion (this task didn't trip these before, so unlikely).

- [ ] **Step 4: Commit + push**

```bash
cd /workspace/igou-ansible
git add playbooks/openshift_virtualization/virtualmachine-manage.yml
git commit -m "$(cat <<'EOF'
refactor(virtualmachine-manage): drop David-Igou.kubevirt_vm_manage role

Rewrite the playbook with explicit kubevirt.core.kubevirt_vm +
kubernetes.core.k8s tasks. The role at v0.0.3 hard-coded
pc-q35-rhel9.4.0 and had no hook for nodeSelector/tolerations, which
blocks the new vm_target_burst opt-in for the casval burst node.

The role stays in requirements.yml (still used by aap-25-vm and
virtualmachine-manage-testing).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

**Push** (operator-confirmed):

```bash
cd /workspace/igou-ansible
git push origin main
```

The push is required so the `igou_ansible` AAP project's auto-sync-on-launch picks up the new playbook when the job template runs in Task 9.

---

## Task 5: PATCH the two AAP credentials with real tokens

**Files:** none (live AAP API operation; tokens were extracted in Task 2 Step 4)

- [ ] **Step 1: Confirm token vars from Task 2 are still in the shell**

```bash
[ -n "$VM_READER_TOKEN" ] || { echo "VM_READER_TOKEN unset — re-run Task 2 Step 4"; exit 1; }
[ -n "$VM_DEPLOYER_TOKEN" ] || { echo "VM_DEPLOYER_TOKEN unset — re-run Task 2 Step 4"; exit 1; }
[ -n "$K8S_API_HOST" ] || { echo "K8S_API_HOST unset — re-run Task 2 Step 4"; exit 1; }
```

- [ ] **Step 2: PATCH `virtualmachine-reader-token`**

```bash
READER_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  "${AAP_HOSTNAME%/}/api/controller/v2/credentials/?name=virtualmachine-reader-token" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['id'])")
echo "Reader credential id: $READER_ID"

curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X PATCH "${AAP_HOSTNAME%/}/api/controller/v2/credentials/${READER_ID}/" \
  -d "$(python3 -c "
import json,os
print(json.dumps({'inputs': {
  'kube_api_token': os.environ['VM_READER_TOKEN'],
  'kube_api_host':  os.environ['K8S_API_HOST'],
  'verify_ssl':     False,
}}))")" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('PATCH ok:', d['name']) if 'name' in d else print('PATCH failed:', d)"
```

Expected: `PATCH ok: virtualmachine-reader-token`.

- [ ] **Step 3: PATCH `virtualmachine-deployer-token`**

```bash
DEPLOYER_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  "${AAP_HOSTNAME%/}/api/controller/v2/credentials/?name=virtualmachine-deployer-token" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['id'])")
echo "Deployer credential id: $DEPLOYER_ID"

curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X PATCH "${AAP_HOSTNAME%/}/api/controller/v2/credentials/${DEPLOYER_ID}/" \
  -d "$(python3 -c "
import json,os
print(json.dumps({'inputs': {
  'kube_api_token': os.environ['VM_DEPLOYER_TOKEN'],
  'kube_api_host':  os.environ['K8S_API_HOST'],
  'verify_ssl':     False,
}}))")" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('PATCH ok:', d['name']) if 'name' in d else print('PATCH failed:', d)"
```

Expected: `PATCH ok: virtualmachine-deployer-token`.

- [ ] **Step 4: Verify the inputs landed (read back; secret values are returned as `$encrypted$` placeholders)**

```bash
curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  "${AAP_HOSTNAME%/}/api/controller/v2/credentials/${READER_ID}/" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d['inputs'], indent=2))"
```

Expected: `kube_api_host` shows the real URL; `kube_api_token` shows `$encrypted$`; `verify_ssl` shows `false`. Same shape for the deployer.

---

## Task 6: CREATE the `igou_kubevirt_ocp` inventory source in AAP via API

**Files:** none (live AAP API operation; the gitops definition was already committed in Task 3 but won't be synced until 1P recovers)

- [ ] **Step 1: Resolve the project, inventory, credential, and EE IDs**

```bash
get_id() {  # $1 = path component, $2 = name
  curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
    "${AAP_HOSTNAME%/}/api/controller/v2/$1/?name=$2" \
    | python3 -c "import sys,json,urllib.parse; print(json.load(sys.stdin)['results'][0]['id'])"
}
PROJECT_ID=$(get_id projects "$(python3 -c "import urllib.parse; print(urllib.parse.quote('igou_inventory'))")")
INVENTORY_ID=$(get_id inventories "$(python3 -c "import urllib.parse; print(urllib.parse.quote('igou_inventory'))")")
EE_ID=$(get_id execution_environments "$(python3 -c "import urllib.parse; print(urllib.parse.quote('igou-awx-ee'))")")
# reader credential id captured in Task 5 step 2
echo "PROJECT_ID=$PROJECT_ID  INVENTORY_ID=$INVENTORY_ID  EE_ID=$EE_ID  READER_ID=$READER_ID"
```

Expected: all four IDs are integers.

- [ ] **Step 2: Update the project SCM cache (so source_path resolves)**

```bash
curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -X POST "${AAP_HOSTNAME%/}/api/controller/v2/projects/${PROJECT_ID}/update/" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Project update job id:', d.get('id'))"
```

Wait for the update to complete (it pulls the latest `main` from GitHub, including the new `dynamic/openshift-virtualization.kubevirt.yml` from Task 3):

```bash
for i in $(seq 1 60); do
  STATUS=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
    "${AAP_HOSTNAME%/}/api/controller/v2/projects/${PROJECT_ID}/" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "project status: $STATUS"
  [ "$STATUS" = "successful" ] && break
  if [ "$STATUS" = "failed" ] || [ "$STATUS" = "error" ]; then
    echo "❌ project update ${STATUS}"; exit 1
  fi
  sleep 2
done
```

Expected: `project status: successful` within ~30s.

- [ ] **Step 3: Create the inventory source**

```bash
curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X POST "${AAP_HOSTNAME%/}/api/controller/v2/inventories/${INVENTORY_ID}/inventory_sources/" \
  -d "$(python3 -c "
import json, os
print(json.dumps({
  'name': 'igou_kubevirt_ocp',
  'source': 'scm',
  'source_project': int(os.environ['PROJECT_ID']),
  'source_path': 'dynamic/openshift-virtualization.kubevirt.yml',
  'credential': int(os.environ['READER_ID']),
  'execution_environment': int(os.environ['EE_ID']),
  'overwrite': True,
  'overwrite_vars': True,
  'update_on_launch': False,
  'update_cache_timeout': 0,
}))")" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Created source id:', d['id']) if 'id' in d else print('Create failed:', d)"
```

Expected: `Created source id: <int>`. If it errors with "inventory source with this name already exists in this inventory", skip — already created.

---

## Task 7: PATCH the `openshift_virtualization_machine` job template extra_vars in AAP via API

**Files:** none (live AAP API operation; the gitops definition was already committed in Task 3)

- [ ] **Step 1: Fetch the current extra_vars and merge in `vm_target_burst`**

```bash
TMPL_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  "${AAP_HOSTNAME%/}/api/controller/v2/job_templates/?name=openshift_virtualization_machine" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['id'])")

NEW_EXTRA_VARS=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  "${AAP_HOSTNAME%/}/api/controller/v2/job_templates/${TMPL_ID}/" \
  | python3 -c "
import sys, json, yaml
tmpl = json.load(sys.stdin)
ev = yaml.safe_load(tmpl.get('extra_vars') or '{}') or {}
ev['vm_target_burst'] = False
print(yaml.safe_dump(ev, default_flow_style=False, sort_keys=False))")

echo "--- new extra_vars ---"
echo "$NEW_EXTRA_VARS"
```

Expected output should include `vm_target_burst: false` near the top of the YAML dump.

- [ ] **Step 2: PATCH the template with the merged extra_vars**

```bash
curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X PATCH "${AAP_HOSTNAME%/}/api/controller/v2/job_templates/${TMPL_ID}/" \
  -d "$(python3 -c "
import json, os
print(json.dumps({'extra_vars': os.environ['NEW_EXTRA_VARS']}))" )" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('PATCH ok:', d['name']) if 'name' in d else print('PATCH failed:', d)"
```

Expected: `PATCH ok: openshift_virtualization_machine`.

---

## Task 8: Sync the kubevirt inventory source and verify hosts appear

**Files:** none

- [ ] **Step 1: Trigger sync on `igou_kubevirt_ocp`**

```bash
KV_SRC_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  "${AAP_HOSTNAME%/}/api/controller/v2/inventory_sources/?name=igou_kubevirt_ocp" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['id'])")

curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -X POST "${AAP_HOSTNAME%/}/api/controller/v2/inventory_sources/${KV_SRC_ID}/update/" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Inventory sync job id:', d.get('inventory_update'))"
```

- [ ] **Step 2: Wait for the sync to complete**

```bash
aap_wait_source_update "$KV_SRC_ID"
```

Expected: `✅ inventory source ${KV_SRC_ID} sync successful` within ~60s.

- [ ] **Step 3: List the discovered hosts**

(`INVENTORY_ID` was resolved in Task 6 Step 1 and persists in the shell. If running this task in a fresh shell, re-resolve it: `INVENTORY_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" "${AAP_HOSTNAME%/}/api/controller/v2/inventories/?name=igou_inventory" | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['id'])")`.)

```bash
curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  "${AAP_HOSTNAME%/}/api/controller/v2/inventories/${INVENTORY_ID}/hosts/?page_size=200" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for h in d.get('results', []):
    print(h['name'], '|', h.get('inventory_sources', ''))" | head -30
```

Expected: existing static hosts (devhosttest, p330, hpg5, rb5009, etc.) plus any KubeVirt VMs currently on the cluster (look for `<vm-name>-<namespace>` format). If no VMs exist on the cluster, the kubevirt source contributes zero hosts — that's fine, the smoke test in Task 9 creates one.

---

## Task 9: VM smoke tests via AAP

**Files:** none (live job launches)

- [ ] **Step 1: Launch the template with default extras (creates a VM)**

```bash
TMPL_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  "${AAP_HOSTNAME%/}/api/controller/v2/job_templates/?name=openshift_virtualization_machine" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['results'][0]['id'])")

JOB_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X POST "${AAP_HOSTNAME%/}/api/controller/v2/job_templates/${TMPL_ID}/launch/" \
  -d '{"extra_vars": "vm_name: aap-smoke-default\nvm_namespace: default\nvm_state: present\n"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Smoke job id: $JOB_ID"
```

- [ ] **Step 2: Wait for the job to finish**

```bash
aap_wait_job "$JOB_ID"
```

Expected: `✅ job ${JOB_ID} successful` within ~3 minutes (DV cloning is the slow part).

- [ ] **Step 3: Confirm the VM exists with default scheduling**

```bash
oc get vm aap-smoke-default -n default -o jsonpath='{.spec.template.spec}' | python3 -m json.tool | head -20
```

Expected: spec includes the inline cloud-init volume and the dataVolume reference. The `nodeSelector` and `tolerations` keys are absent (because `vm_target_burst` defaults to false → the role passes `omit`).

- [ ] **Step 4: Delete the smoke VM**

```bash
JOB_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X POST "${AAP_HOSTNAME%/}/api/controller/v2/job_templates/${TMPL_ID}/launch/" \
  -d '{"extra_vars": "vm_name: aap-smoke-default\nvm_namespace: default\nvm_state: absent\n"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Delete job id: $JOB_ID"
aap_wait_job "$JOB_ID"
```

Verify gone:

```bash
oc get vm aap-smoke-default -n default 2>&1 | grep -q 'NotFound' && echo "✅ VM deleted"
```

- [ ] **Step 5: Launch the template with `vm_target_burst: true` (creates a burst-pinned VM)**

```bash
JOB_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X POST "${AAP_HOSTNAME%/}/api/controller/v2/job_templates/${TMPL_ID}/launch/" \
  -d '{"extra_vars": "vm_name: aap-smoke-burst\nvm_namespace: default\nvm_state: present\nvm_target_burst: true\n"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Burst smoke job id: $JOB_ID"
aap_wait_job "$JOB_ID"
```

**Note:** if the casval node is currently scaled to 0 replicas, the VM stays Pending until the autoscaler scales it. The job's `wait_timeout` is 600s; if that's not enough for cold bare-metal provisioning, the job may report timeout. The VM is still correctly *created* — that's what this step validates. If autoscaling has the "first scale-from-zero on GPU node group" issue documented in `clusters/ocp/cluster-api/casval-worker-machineset.yaml`, a manual `oc scale machineset/casval-worker -n openshift-cluster-api --replicas=1` is the one-time bootstrap workaround.

- [ ] **Step 6: Confirm the burst placement landed on the spec**

```bash
oc get vm aap-smoke-burst -n default -o jsonpath='{.spec.template.spec.nodeSelector}' ; echo
oc get vm aap-smoke-burst -n default -o jsonpath='{.spec.template.spec.tolerations}' ; echo
```

Expected:
- nodeSelector: `{"node-role.kubernetes.io/burst":""}`
- tolerations: `[{"effect":"NoSchedule","key":"workload","operator":"Equal","value":"burst"}]`

- [ ] **Step 7: Delete the burst smoke VM**

```bash
JOB_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -H "Content-Type: application/json" \
  -X POST "${AAP_HOSTNAME%/}/api/controller/v2/job_templates/${TMPL_ID}/launch/" \
  -d '{"extra_vars": "vm_name: aap-smoke-burst\nvm_namespace: default\nvm_state: absent\n"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Burst delete job id: $JOB_ID"
aap_wait_job "$JOB_ID"
```

- [ ] **Step 8: Re-sync the kubevirt inventory source and confirm no stale hosts remain**

(`KV_SRC_ID` was resolved in Task 8 Step 1 and `INVENTORY_ID` in Task 6 Step 1; both persist in the shell. If running this step in a fresh shell, re-resolve them per the patterns in those tasks.)

```bash
curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  -X POST "${AAP_HOSTNAME%/}/api/controller/v2/inventory_sources/${KV_SRC_ID}/update/" >/dev/null
aap_wait_source_update "$KV_SRC_ID"

curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
  "${AAP_HOSTNAME%/}/api/controller/v2/inventories/${INVENTORY_ID}/hosts/?search=aap-smoke" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Stale hosts found:', d['count'])"
```

Expected: `Stale hosts found: 0`. Validates the `overwrite: true` setting deletes kubevirt-sourced hosts that no longer exist.

---

## Task 10: Delete the 3 demo outliers from AAP

**Files:** none

- [ ] **Step 1: Resolve and delete each outlier**

```bash
for spec in "credentials:Demo Credential" "job_templates:Demo Job Template" "projects:Demo Project"; do
  TYPE="${spec%%:*}"
  NAME="${spec#*:}"
  NAME_ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$NAME")
  ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
       "${AAP_HOSTNAME%/}/api/controller/v2/${TYPE}/?name=${NAME_ENC}" \
       | python3 -c "import sys,json; r=json.load(sys.stdin)['results']; print(r[0]['id'] if r else '')")
  if [ -z "$ID" ]; then
    echo "skip ${TYPE} ${NAME} — not found"
    continue
  fi
  echo "deleting ${TYPE} id=${ID} (${NAME})"
  curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
       -o /dev/null -w "HTTP %{http_code}\n" \
       -X DELETE "${AAP_HOSTNAME%/}/api/controller/v2/${TYPE}/${ID}/"
done
```

Expected: three `HTTP 204` lines, one per outlier. (HTTP 204 = No Content, success.)

- [ ] **Step 2: Verify the outliers are gone**

```bash
for spec in "credentials:Demo Credential" "job_templates:Demo Job Template" "projects:Demo Project"; do
  TYPE="${spec%%:*}"
  NAME="${spec#*:}"
  NAME_ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$NAME")
  COUNT=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
          "${AAP_HOSTNAME%/}/api/controller/v2/${TYPE}/?name=${NAME_ENC}" \
          | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])")
  [ "$COUNT" = "0" ] && echo "✅ ${TYPE}/${NAME} gone" || echo "❌ ${TYPE}/${NAME} still present ($COUNT)"
done
```

Expected: three `✅` lines.

---

## Done — final verification

- [ ] **Step 1: Static checks pass in all three repos**

```bash
(cd /workspace/igou-openshift && make lint && make validate-kustomize)
(cd /workspace/igou-inventory && make yamllint)
(cd /workspace/igou-ansible  && make yamllint && ansible-lint --profile=production playbooks/openshift_virtualization/virtualmachine-manage.yml)
```

- [ ] **Step 2: AAP smoke job runs end-to-end with default extras** (already exercised in Task 9 Step 1-4)

- [ ] **Step 3: AAP smoke job runs end-to-end with `vm_target_burst: true`** (already exercised in Task 9 Step 5-7)

- [ ] **Step 4: Kubevirt inventory source syncs cleanly and reflects current VM state** (already exercised in Task 9 Step 8)

- [ ] **Step 5: AAP has no outliers** (already exercised in Task 10 Step 2)

- [ ] **Step 6: Operator follow-up reminder** — write the 1Password items `virtualmachine-reader-token` and `virtualmachine-deployer-token` in vault `awx` with fields `token` and `k8s_auth_host` populated, so the next `aap_sync_credentials` doesn't wipe the PATCHed values. This step is **out of scope for this plan** but blocks future AAP gitops syncs from regressing the credentials.
