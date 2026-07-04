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

## The role is a function

The role is pure over its inputs and **performs no secret lookups**: every
credential in `rustfs_state_instances` arrives as a resolved value. The
`op://` lookup expressions live in the caller's data (inventory
group_vars), where they are just values of the schema fields — so molecule
feeds the role literal strings and exercises exactly the code path
production runs. The interface is validated at role start by
`meta/argument_specs.yml` (secret fields are `no_log`).

## Behavior

- **Reconcile, don't replace** — every resource is read first
  (`rc ... --json`), diffed in Ansible, and only written on mismatch.
- **Deletion safety** — resources on the server but absent from the spec
  are *reported* (`unmanaged_on_server` in the summary), never deleted.
  There is no prune. Extra policy attachments are reported, not detached.
- **Secrets** — resolved by the caller, never by the role; `no_log` on
  every task that touches them. An existing user's secret is never
  rewritten (rc `admin user add` on an existing access key would reset
  it); rotation is a manual act. The role never generates credentials —
  a user needing creation without a provided `secret_key` fails with
  instructions.
- **Credential liveness** — for each user with `liveness_bucket` (and a
  provided `secret_key`), the provided pair (`access_key` defaults to the
  user name) is used to list that bucket. Failure means the credential
  store and the server diverged (wedged server, stale item, manual
  rotation) — the play fails after the full report. This is the invariant
  that catches the 2026-07-02 `InvalidAccessKeyId` class of surprise. In
  check mode, users missing from the server are skipped here — they are
  already reported as pending creation, not dead credentials.
- **Drift mode** — run the playbook with `--check`: reads still execute,
  mutations don't, and the play **fails** if any change would be made or a
  credential is dead (`rustfs_state_fail_on_drift` defaults to the
  check-mode flag).
- **Flaky admin API** — RustFS 1.0.0-beta.8 intermittently refuses admin
  connections in bursts while the S3 path stays healthy; every rc call
  retries. Network errors are distinguished from real errors via the rc
  error JSON (`details.type == "network_error"`), so "not found" is
  detected instead of retried to exhaustion.

## Canonical comparison

The server does not return stable orderings — IAM `Action`/`Resource`
arrays come back in a different order on every call, and ILM rule `id`s
are server-generated (and **required** by `rc ilm rule import`). Both
sides of every comparison pass through the role's
`rustfs_canonical_policy` / `rustfs_canonical_ilm` filters (sort lists,
strip ids and empty `ID`/`Condition`), so ordering can never produce false
drift. Codify documents verbatim from a live export
(`rc admin policy info`, `rc ilm rule export`); ids in codified ILM rules
are required for import and ignored in comparison.

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `rustfs_state_instances` | — (required) | Desired state; schema below + `meta/argument_specs.yml` |
| `rustfs_state_rc_version` | `0.1.25` | rc CLI pin |
| `rustfs_state_rc_checksum` | sha256 of the pin | Tarball checksum, verified on download |
| `rustfs_state_rc_url` | GitHub release URL | Tarball source |
| `rustfs_state_rc_binary` | `""` | Pre-installed rc path; empty downloads the pin |
| `rustfs_state_retries` / `rustfs_state_retry_delay` | `8` / `3` | Retry policy for every rc call |
| `rustfs_state_builtin_policies` | server builtins | Never reported as unmanaged |
| `rustfs_state_fail_on_drift` | `ansible_check_mode` | Fail the play on any (pending) change |

## Schema (`rustfs_state_instances`)

```yaml
rustfs_state_instances:
  - name: rustfs-cold                    # rc alias name; unique
    endpoint: https://truenas.igou.systems:20292
    # resolved values — in inventory data these are op:// lookups:
    admin_access_key: "{{ lookup('community.general.onepassword', 'rustfs-cold-admin', field='username', vault='awx') }}"
    admin_secret_key: "{{ lookup('community.general.onepassword', 'rustfs-cold-admin', field='password', vault='awx') }}"

    policies:
      - name: quay_rw
        document:                        # verbatim IAM JSON as YAML
          Version: "2012-10-17"
          Statement: [...]

    buckets:
      - name: routeros-backups
        versioning: true                 # omit -> versioning unmanaged
        lifecycle:                       # omit -> lifecycle unmanaged
          rules: [...]                   # verbatim `rc ilm rule export`;
                                         # every rule MUST carry an `id`

    users:
      - name: quay                       # the access key on the server
        policies: [quay_rw]
        # resolved pair; access_key defaults to name — providing the
        # store's own username field makes liveness catch identity drift
        access_key: "{{ lookup('community.general.onepassword', 'quay-user-rustfs-cold', field='username', vault='ocp-pull') }}"
        secret_key: "{{ lookup('community.general.onepassword', 'quay-user-rustfs-cold', field='password', vault='ocp-pull') }}"
        liveness_bucket: quay            # enables the liveness check
```

## Testing

`molecule test -s rustfs-state` (podman required), against a throwaway
`rustfs/rustfs:1.0.0-beta.8` container (the exact live version):

1. converge from empty, idempotence;
2. out-of-band drift injection (policy rewrite, versioning suspend, ILM
   change, user delete) → repair with the **exact** expected change set,
   with server-side state confirmed independently (`podman exec ... ls
   /data`);
3. drift-detection (check) mode: reports the exact pending set, fails,
   mutates nothing;
4. converge applies exactly the pending set; versioning **suspend** branch;
5. unmanaged-resource reporting (deletion safety, positive path);
6. dead-credential liveness failure path.

Known untested assumption: ILM id regeneration on import (the server has
so far preserved provided ids; comparison ignores ids either way).
