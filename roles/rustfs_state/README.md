# rustfs_state

Declarative in-server state management for RustFS instances (igou-inventory#122).

There is no official IaC surface for RustFS — the supported management
interface is the imperative [`rc` CLI](https://github.com/rustfs/cli). This
role layers declarativeness on top: desired state is data (in
igou-inventory `group_vars/rustfs.yml`), the role reconciles it via a
pinned, checksum-verified `rc`, and AAP runs converge plus a nightly
`--check` drift job.

Scope is **Layer 1 (in-server state)**: buckets, versioning, lifecycle
(ILM), IAM policies, users, policy attachments, and credential liveness.
Server deployment (Layer 2, the TrueNAS app itself) is deliberately out of
scope. Quotas are out of scope for v1 (nothing live uses them).

## Behavior

- **Reconcile, don't replace** — every resource is read first
  (`rc ... --json`), diffed in Ansible, and only written on mismatch.
- **Deletion safety** — resources on the server but absent from the spec
  are *reported* (`unmanaged_on_server` in the summary), never deleted.
  There is no prune. Extra policy attachments are reported, not detached.
- **Secrets** — never in the spec; admin and per-user credentials resolve
  from 1Password at runtime (`community.general.onepassword` lookups,
  `no_log` on every task that touches them). An existing user's secret is
  never rewritten (rc `admin user add` on an existing access key would
  reset it); rotation is a manual act.
- **Credential liveness** — for each user with `liveness_bucket` set, the
  key pair *as stored in 1Password* is used to list that bucket. Failure
  means 1P and the server diverged (wedged server, stale item, manual
  rotation) — the play fails after the full report. This is the invariant
  that catches the 2026-07-02 `InvalidAccessKeyId` class of surprise.
- **Drift mode** — run the playbook with `--check`: reads still execute,
  mutations don't, and the play **fails** if any change would be made or a
  credential is dead (`rustfs_state_fail_on_drift` defaults to the
  check-mode flag).
- **Flaky admin API** — RustFS 1.0.0-beta.8 intermittently refuses admin
  connections in bursts while the S3 path stays healthy; every rc call
  retries (`rustfs_state_retries` × `rustfs_state_retry_delay`). Network
  errors are distinguished from real errors via the rc error JSON
  (`details.type == "network_error"`), so "not found" is detected instead
  of retried to exhaustion.

## Canonical comparison

The server does not return stable orderings — IAM `Action`/`Resource`
arrays come back in a different order on every call, and ILM rule `id`s
are server-generated. Both sides of every comparison pass through the
role's `rustfs_canonical_policy` / `rustfs_canonical_ilm` filters (sort
lists, strip ids and empty `ID`/`Condition`), so ordering can never
produce false drift. Codify documents verbatim from a live export
(`rc admin policy info`, `rc ilm rule export`); ids in codified ILM rules
are tolerated and ignored.

## Schema (`rustfs_instances`)

```yaml
rustfs_instances:
  - name: rustfs-cold                    # rc alias name; unique
    endpoint: https://truenas.igou.systems:20292
    # admin credential: 1Password item (fields default to username/password)
    admin_credential_item: rustfs-cold-admin
    admin_credential_vault: awx
    # admin_access_key / admin_secret_key inline overrides exist for tests

    policies:
      - name: quay_rw
        document:                        # verbatim IAM JSON as YAML
          Version: "2012-10-17"
          Statement: [...]

    buckets:
      - name: routeros-backups
        versioning: true                 # omit -> versioning unmanaged
        lifecycle:                       # omit -> lifecycle unmanaged
          rules: [...]                   # verbatim `rc ilm rule export` rules;
                                         # every rule MUST carry an `id`
                                         # (`rc ilm rule import` rejects
                                         # id-less rules); ids are ignored
                                         # in drift comparison

    users:
      - name: quay                       # the access key
        policies: [quay_rw]
        credential_item: quay-user-rustfs-cold
        credential_vault: ocp-pull
        # credential_access_key_field / credential_secret_key_field override
        # the 1P field names (default username/password)
        liveness_bucket: quay            # enables the credential liveness check
        # secret_key: inline override for tests
```

Policy documents and ILM rules are **inline data**, not file paths: AAP
inventory sources import variables, not repository files, so a
`document_file` sidecar in igou-inventory would not exist on the EE
filesystem at runtime. Inline YAML diffs just as well in review.

## Testing

`molecule test -s rustfs-state` (podman required): converge from empty
against a throwaway `rustfs/rustfs:1.0.0-beta.8` container, idempotence,
drift injection (policy rewrite, versioning suspend, ILM change, user
delete) + re-converge, and the liveness failure path.
