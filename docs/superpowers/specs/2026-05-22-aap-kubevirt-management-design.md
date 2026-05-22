---
title: AAP-driven KubeVirt VM management + dynamic inventory
date: 2026-05-22
status: approved
---

# AAP-driven KubeVirt VM management + dynamic inventory

## Problem

Three loosely-coupled gaps block AAP from being the operator-facing surface for the OCP cluster's KubeVirt VMs:

1. The two ServiceAccounts that AAP uses to talk to KubeVirt (`virtualmachine-reader`, `virtualmachine-deployer`) are declared in `igou-openshift/components/openshift-virt/` but commented out of that component's `kustomization.yaml`. No SA, no token, no working AAP credential — every kubevirt-related job template silently can't authenticate.
2. The `openshift_virtualization_machine` job template's playbook depends on the `David-Igou.kubevirt_vm_manage` role at v0.0.3, which hard-codes machine type `pc-q35-rhel9.4.0`, ships a 130-line Jinja DV template for one used branch (`sourceRef`), and has no hook for `nodeSelector` / `tolerations`. The user wants an opt-in "schedule this VM on the casval burst node" option, which the role can't express.
3. AAP has one SCM-based inventory source (`igou_inventory_github`) pulling the static `igou-inventory/inventory.yaml`. There is no source that discovers KubeVirt VMs dynamically — AAP can list metal/SBC hosts but not the VMs it just created.

Beyond those three: AAP's live state carries three stock demo objects (`Demo Credential`, `Demo Job Template`, `Demo Project`) that aren't in the gitops definitions and should be deleted.

## Goal

- KubeVirt VM create/delete/manage works end-to-end from AAP, including an opt-in flag to schedule on the casval burst node.
- A `kubevirt.core.kubevirt`-plugin dynamic inventory source — defined in `igou-inventory`, applied to AAP via SCM — surfaces every VM in every namespace as an AAP host.
- AAP's live state is free of stock demo objects.

## Non-goals

- EE image build via OCP APIs (`build_supported_ee` job template). Deferred to its own design session — meaningful enough to need dedicated scoping.
- Re-implementing the `David-Igou.kubevirt_vm_manage` role. It stays in `requirements.yml` because two other playbooks still use it (`aap-25-vm/1-deploy-vms.yml`, `virtualmachine-manage-testing.yml`). We just stop using it in `virtualmachine-manage.yml`.
- Migrating any other AAP credential off the custom `Kubernetes API Token` credential type. The `k8s_test`/`internal`/`external` robot tokens stay as-is.
- Replacing 1Password lookups in `credentials.yml` with literal values. 1P is rate-limited right now; for *testing*, this design does a one-shot direct AAP API write to populate the two VM credentials with real tokens. The gitops `credentials.yml` keeps the 1P lookups so the next `aap_sync_credentials` works once 1P recovers (caveat: the operator needs to populate the 1P items separately).

## Architecture

Five workstreams across two gitops repos plus two one-shot AAP API operations. Sequenced so each step's preconditions are satisfied by the prior step.

```
igou-openshift                          igou-inventory                      AAP (live state, one-shot)
──────────────                          ──────────────                      ──────────────────────────
1. Move 8 SA/CR/CRB/Secret YAMLs        3. Add second inventory source     5. After SAs applied:
   from openshift-virt/ →                  igou_kubevirt_ocp                  - oc get secret tokens
   service-accounts/                       (scm, source_path = new            - PATCH the two
   - SAs in service-accounts ns            kubevirt plugin file,                Kubernetes-API-Token
   - cluster-wide ClusterRoles             credential = existing                credentials in AAP
   - ClusterRoleBindings                   virtualmachine-reader-token)         via API to set real
                                                                                token + host inputs

2. Drop the 8 commented-out             4. Add dynamic plugin file        6. Delete 3 outliers:
   refs from openshift-virt/               dynamic/openshift-                  Demo Credential
   kustomization.yaml                      virtualization.kubevirt.yml         Demo Job Template
                                           (plugin: kubevirt.core.             Demo Project
                                            kubevirt, all namespaces)
igou-ansible
────────────
A. Rewrite playbooks/openshift_virtualization/virtualmachine-manage.yml
   with explicit kubevirt.core.kubevirt_vm + kubernetes.core.k8s tasks;
   drop the David-Igou.kubevirt_vm_manage role dependency for this file
   only; add vm_target_burst knob

B. Add vm_target_burst: false to the openshift_virtualization_machine
   extra_vars in group_vars/aap/job_templates.yml
```

## Detailed changes

### `igou-openshift` — 8 files moved, 2 kustomizations edited

#### MOVE 4 files: `components/openshift-virt/` → `components/service-accounts/` (deployer)

- `virtualmachine-deployer-serviceaccount.yaml` — `namespace: service-accounts` (was `openshift-cnv`)
- `virtualmachine-deployer-token-secret.yaml` — `namespace: service-accounts`
- `virtualmachine-deployer-clusterrole.yaml` — unchanged (cluster-scoped already)
- `virtualmachine-deployer-clusterrolebinding.yaml` — subject `namespace: service-accounts`

ClusterRole rules preserved verbatim from the source: `*` verbs on `kubevirt.io` `virtualmachines`/`virtualmachineinstances`, `*` on `cdi.kubevirt.io` `datavolumes`, CRUD on `namespaces`/`services` and `route.openshift.io` `routes` (+ `routes/custom-host`), and `create` on `upload.cdi.kubevirt.io` `uploadtokenrequests`. These are deliberately broad — the deployer is the SA AAP uses to provision VMs into arbitrary namespaces.

#### MOVE 4 files: `components/openshift-virt/` → `components/service-accounts/` (reader)

- `virtualmachine-reader-serviceaccount.yaml` — `namespace: service-accounts`
- `virtualmachine-reader-token-secret.yaml` — `namespace: service-accounts`
- `virtualmachine-reader-clusterrole.yaml` — unchanged
- `virtualmachine-reader-clusterrolebinding.yaml` — subject `namespace: service-accounts`

ClusterRole rules: get/watch/list on `kubevirt.io` `virtualmachines`/`virtualmachineinstances`, `list` on core `namespaces`/`services`. The list-on-namespaces grant is what allows the kubevirt.core inventory plugin to discover all VM namespaces cluster-wide when its plugin config sets `namespaces: []`.

#### EDIT `components/service-accounts/kustomization.yaml`

```diff
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
+  - virtualmachine-reader-serviceaccount.yaml
+  - virtualmachine-reader-token-secret.yaml
+  - virtualmachine-reader-clusterrole.yaml
+  - virtualmachine-reader-clusterrolebinding.yaml
+  - virtualmachine-deployer-serviceaccount.yaml
+  - virtualmachine-deployer-token-secret.yaml
+  - virtualmachine-deployer-clusterrole.yaml
+  - virtualmachine-deployer-clusterrolebinding.yaml
```

The new SAs do **not** plug into the `namespace-rolebindings` Helm chart — they need cluster-wide verbs (deployer: VM CRUD across arbitrary namespaces; reader: VM list cluster-wide for the dynamic inventory), which the chart's per-namespace `RoleBinding` pattern can't express.

#### EDIT `components/openshift-virt/kustomization.yaml`

Drop the 8 commented-out lines entirely. The cluster-wide RBAC for these SAs no longer lives here; openshift-virt is back to being purely operator + storage profile concerns.

```diff
 - freenas-nfs-cold-csi-storageprofile.yaml
-# - virtualmachine-deployer-clusterrolebinding.yaml
-# - virtualmachine-deployer-clusterrole.yaml
-# - virtualmachine-deployer-serviceaccount.yaml
-# - virtualmachine-deployer-token-secret.yaml
-# - virtualmachine-reader-clusterrolebinding.yaml
-# - virtualmachine-reader-clusterrole.yaml
-# - virtualmachine-reader-serviceaccount.yaml
-# - virtualmachine-reader-token-secret.yaml
```

### `igou-inventory` — 1 new file, 1 edit

#### NEW `dynamic/openshift-virtualization.kubevirt.yml`

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
# that grant, this needs to become an explicit namespace list.
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

The `dynamic/` subdir keeps this file out of any root-level inventory auto-discovery that the existing `igou_inventory_github` source might do; both sources point at the same SCM project but only the kubevirt source has `source_path` set to this file.

#### EDIT `group_vars/aap/inventories.yml`

Add a second `controller_inventory_sources` entry. The existing `igou_inventory_github` entry is preserved verbatim — both sources feed the same AAP inventory `igou_inventory`.

```diff
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
+
+  - name: igou_kubevirt_ocp
+    source: scm
+    source_project: igou_inventory
+    source_path: dynamic/openshift-virtualization.kubevirt.yml
+    inventory: igou_inventory
+    credential: virtualmachine-reader-token
+    execution_environment: igou-awx-ee
+    overwrite: false        # leave static hosts alone on every sync
+    overwrite_vars: true
+    update_on_launch: false
+    update_cache_timeout: 0
+    wait: true
```

`overwrite: false` on the kubevirt source is load-bearing — `overwrite: true` would delete the static `igou_inventory_github` hosts on every sync.

The `igou-awx-ee` EE already includes `kubevirt.core` 2.2.4 (via `igou-ansible/requirements.yml`), so the plugin loads without an EE rebuild.

### `igou-ansible` — 1 file rewritten, 1 file edited via gitops sync

#### REWRITE `playbooks/openshift_virtualization/virtualmachine-manage.yml`

Drop the `David-Igou.kubevirt_vm_manage` role dependency in this single playbook. Use `kubevirt.core.kubevirt_vm` directly. Other playbooks that import the role are unaffected.

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

Behavioral differences from the role-based original:

| Change | Reason |
|---|---|
| `hosts: sno` → `hosts: "{{ host | default('localhost') }}"` | `sno` is not an inventory group. Localhost + env-injected K8s creds is the right shape for cluster-API-only plays. The override hook (`{{ host }}`) preserves the calling pattern from `virtualmachine-inventory-issue.yml`. |
| Drop `machine.type: pc-q35-rhel9.4.0` | Stale (other manifests pin 9.6.0); let KubeVirt's preference/default pick. |
| Single inline DV template replaces the role's 130-line Jinja file | Only the `sourceRef` branch was used by the job template; PVC/HTTP/S3/registry branches were dead code in this code path. |
| Explicit `Tear down` task for `rebuild: true` | Role pattern was "include role twice with different `when`" — opaque. Explicit task is clearer. |
| Add `vm_target_burst` knob with inline conditional `nodeSelector`/`tolerations` | The point of this work. `omit` keeps the field out of the generated spec when false. |
| Drop the role's `Manage Services` / `Manage routes` tasks | The job template never set `vm_services` or `vm_routes`; only `aap-25-vm/1-deploy-vms.yml` and `virtualmachine-manage-testing.yml` exercise those, and they import the role directly — unaffected. |

`David-Igou.kubevirt_vm_manage` stays in `requirements.yml` (still used by the two other playbooks).

#### EDIT `igou-inventory/group_vars/aap/job_templates.yml`

Add `vm_target_burst: false` to the `openshift_virtualization_machine` template's `extra_vars`. Operator overrides per-run via the existing "Prompt on launch → Extra Variables" surface.

```diff
   - name: openshift_virtualization_machine
     ...
     extra_vars:
       create_namespace: false
       rebuild: false
       vm_state: "present"
+      vm_target_burst: false
       vm_labels:
         created_by: "ansible"
```

### AAP live state — two one-shot operations

#### After SAs land on the cluster: write tokens directly into AAP

1Password is rate-limited; the gitops `controller_credentials` entries for `virtualmachine-reader-token` / `virtualmachine-deployer-token` resolve those values from 1P. For testing, bypass 1P with a direct AAP API write.

Shape — a Bash script (or one-off ad-hoc playbook) run from the devcontainer with `AAP_USERNAME` / `AAP_PASSWORD` / `AAP_HOSTNAME` already exported:

```bash
for sa in reader deployer; do
  TOKEN=$(oc get secret virtualmachine-${sa}-token -n service-accounts \
            -o jsonpath='{.data.token}' | base64 -d)
  CRED_ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
            "${AAP_HOSTNAME%/}/api/controller/v2/credentials/?name=virtualmachine-${sa}-token" \
            | jq -r '.results[0].id')
  curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
       -H "Content-Type: application/json" \
       -X PATCH "${AAP_HOSTNAME%/}/api/controller/v2/credentials/${CRED_ID}/" \
       -d "{\"inputs\":{\"kube_api_token\":\"${TOKEN}\",\"kube_api_host\":\"https://api.ocp.igou.systems:6443\",\"verify_ssl\":false}}"
done
```

This survives until the next `aap_sync_credentials` run, which will re-resolve the 1P lookups in `credentials.yml` and overwrite the inputs again. The operator separately needs to populate 1P items `virtualmachine-reader-token` and `virtualmachine-deployer-token` with `token` + `k8s_auth_host` fields before that next sync — otherwise the lookup fails and the credentials' inputs revert to empty.

#### Delete the 3 demo outliers

Three direct DELETEs against the AAP API. Sequenced after `aap_sync_credentials` + `aap_sync_templates` so reconcile-from-gitops has already run and nothing in-flight references the demo objects.

```bash
for type in credentials job_templates projects; do
  ID=$(curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
       "${AAP_HOSTNAME%/}/api/controller/v2/${type}/?name__startswith=Demo" \
       | jq -r '.results[0].id // empty')
  [ -n "$ID" ] && curl -sk -u "${AAP_USERNAME}:${AAP_PASSWORD}" \
                  -X DELETE "${AAP_HOSTNAME%/}/api/controller/v2/${type}/${ID}/"
done
```

Not deleted (AAP-managed; deletion would just trigger recreation on the next system reconcile): `Ansible Galaxy` credential, `Control Plane` / `Default` / `Minimal` EEs, the four `Cleanup *` system schedules.

## Validation

Order matters. Each step's preconditions are satisfied by the prior step's success.

1. **Static checks**: `make lint` in `igou-openshift`, `make validate-kustomize` for `components/service-accounts/` and `components/openshift-virt/`. `yamllint` in `igou-inventory` and `igou-ansible`.
2. **Apply SAs**: `oc apply -k igou-openshift/components/service-accounts/`. Confirm 4 SAs, 2 ClusterRoles, 2 ClusterRoleBindings, 2 token Secrets all exist; tokens populated (`oc get secret virtualmachine-reader-token -n service-accounts -o jsonpath='{.data.token}'` returns non-empty).
3. **Confirm SAs work**: `oc auth can-i list virtualmachines.kubevirt.io --as=system:serviceaccount:service-accounts:virtualmachine-reader -A` returns `yes`. Same with `create virtualmachines.kubevirt.io` for `virtualmachine-deployer`.
4. **PATCH the two AAP credentials** with real tokens (per the script above).
5. **Sync AAP gitops**: `make aap-sync-credentials` then `make aap-sync-templates`. The new `igou_kubevirt_ocp` inventory source and the `vm_target_burst` extra_var land. **Note:** the credentials sync re-resolves 1P lookups; if 1P is still rate-limited, the credentials' inputs will be wiped — re-run step 4 immediately after.
6. **Kubevirt inventory sync**: in AAP UI, hit "Sync" on the `igou_kubevirt_ocp` source. Hosts list shows every VM in every namespace, with groups for each namespace and each VM label.
7. **VM smoke test**: trigger `openshift_virtualization_machine` from AAP UI with defaults. VM lands in `default` namespace. Re-trigger with `vm_state: absent`, `vm_name: <same>` — VM gone. Re-trigger with `vm_target_burst: true`, `vm_name: <new>`. VM lands; `oc describe vmi -n default <vm>` shows the burst nodeSelector + toleration; if no burst node is up yet, the launcher pod stays Pending until the CAPI autoscaler scales casval from 0 → 1.
8. **Delete the 3 outliers** (per the script above). Confirm in AAP UI.

If validation fails:

| Step | Failure | Likely cause |
|---|---|---|
| 2 | Token secret has no `data.token` after a few seconds | OCP 4.20+ requires the `kubernetes.io/service-account.name` annotation **and** an existing SA at apply time; check the kustomization applied the SA before the secret. |
| 3 | `can-i` returns `no` | ClusterRoleBinding subject namespace mismatch — confirm it says `service-accounts`, not `openshift-cnv`. |
| 6 | Sync errors with "namespaces is required" | The kubevirt.core plugin's `namespaces: []` semantics changed between collection versions; explicit list workaround is to enumerate `default`, `vm-aap-testing`, `vlan9-vm-multinode`, etc. |
| 6 | Sync succeeds but lists zero hosts | Reader SA can't list VMs cluster-wide — re-check the ClusterRole's `list` verb scope. |
| 7 | VM create fails with auth error | Step 4 PATCH was overwritten by step 5's gitops sync — re-run step 4. |
| 7 (burst) | VM stays Pending forever | CAPI autoscaler bug — first scale-from-zero on a GPU-bearing MachineSet needs a manual scale to 1 once (documented in `clusters/ocp/cluster-api/casval-worker-machineset.yaml` annotations). |

## File summary

`igou-openshift`:

| Change | Path |
| --- | --- |
| MOVE | `components/openshift-virt/virtualmachine-{reader,deployer}-{serviceaccount,token-secret,clusterrole,clusterrolebinding}.yaml` → `components/service-accounts/` |
| EDIT (each moved file) | swap `namespace: openshift-cnv` → `namespace: service-accounts` on SAs, token Secrets, and ClusterRoleBinding subjects |
| EDIT | `components/service-accounts/kustomization.yaml` — add 8 new resources |
| EDIT | `components/openshift-virt/kustomization.yaml` — drop 8 commented-out lines |

`igou-inventory`:

| Change | Path |
| --- | --- |
| NEW | `dynamic/openshift-virtualization.kubevirt.yml` |
| EDIT | `group_vars/aap/inventories.yml` — add `igou_kubevirt_ocp` source |
| EDIT | `group_vars/aap/job_templates.yml` — add `vm_target_burst: false` to `openshift_virtualization_machine` extra_vars |

`igou-ansible`:

| Change | Path |
| --- | --- |
| REWRITE | `playbooks/openshift_virtualization/virtualmachine-manage.yml` |

AAP live state (one-shot, not gitops):

| Operation | Target |
| --- | --- |
| PATCH | `credentials/{id}` for `virtualmachine-reader-token` and `virtualmachine-deployer-token` with real cluster tokens |
| DELETE | `credentials/{id}` for `Demo Credential` |
| DELETE | `job_templates/{id}` for `Demo Job Template` |
| DELETE | `projects/{id}` for `Demo Project` |

## Out-of-band (operator follow-up)

| Change | Item / vault |
| --- | --- |
| CREATE / EDIT | 1Password items `virtualmachine-reader-token` and `virtualmachine-deployer-token` in vault `awx` — populate `token` and `k8s_auth_host` fields with the real values written into AAP at step 4. Required before the next `aap_sync_credentials` run, otherwise the 1P lookup empties the credentials' inputs. |

## Deferred

EE image build via OpenShift APIs (replacement for `build_supported_ee` / `playbooks/aap/build-ee.yml`). Multiple options worth weighing (BuildConfig with Custom strategy, AAP-triggered Tekton PipelineRun, Shipwright). Needs its own design session with proper scoping of the cluster RBAC + build pod privilege surface.
