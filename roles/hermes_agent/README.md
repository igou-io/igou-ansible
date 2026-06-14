# hermes_agent

The **Hermes-specific** half of a guest convergence for the Hermes Agent VM. It
does only two things, split into two separately-invoked phases:

1. **install** — format/mount the persistent state disk and run the Hermes
   installer.
2. **configure** — render the CLI config, an in-guest nftables egress backstop,
   and a hardened systemd user unit.

Baseline OS hardening, the `igou`/`hermes` users, and OS package installation
are **out of scope** for this role. They come from the reused `linux_baseline`
and `install_packages` playbooks and run separately.

> **Prerequisite:** `group_vars/hermes` must list `hermes` as a baseline service
> user so `linux_baseline` creates it and enables systemd **linger** for it.
> Linger is what materializes `/run/user/<uid>` (and the per-user D-Bus socket),
> which the rootless `systemctl --user` daemon-reload in the configure phase
> needs. The role also defensively enables linger itself, so it is self-sufficient
> if baseline ordering ever slips, but the user must still exist.

## Two-phase model

The phases are deliberate and ordered around the guest's egress window. There is
no tag/`never` dispatch; each phase is a separate task file invoked with
`import_role` + `tasks_from:`.

| Phase       | Task file                | Network         | What it does                                                        |
| ----------- | ------------------------ | --------------- | ------------------------------------------------------------------- |
| `install`   | `tasks/install.yml`      | **egress open** | xfs-format `/dev/vdb`, mount at `~/.hermes` (fstab), chown, run installer |
| `configure` | `tasks/configure.yml`    | **egress locked** | render `cli-config.yaml` + `.env`, validate+load nftables backstop, install (not start) the systemd unit, persistent journald |

Run order: `install` (during the egress window) → lock egress → `configure`.

```bash
ansible-playbook playbooks/hermes/install.yml   -i <inventory> -l hermes
# ... lock egress (OVN EgressFirewall) ...
ansible-playbook playbooks/hermes/configure.yml -i <inventory> -l hermes
```

## What the installer produces

`curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`, run as the
`hermes` user, lays down `~/.hermes` and symlinks the `hermes` CLI into
`~/.local/bin/hermes` (this is the path the systemd unit's `ExecStart` and the
installer's idempotency `creates:` guard both use). The run command is
`hermes gateway run`.

## Variable contract

| Variable                     | Default                                                                  | Purpose                                                              |
| ---------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------- |
| `hermes_user`                | `hermes`                                                                  | OS user that owns Hermes and runs the gateway.                      |
| `hermes_home`                | `/home/hermes`                                                           | Home of `hermes_user`.                                              |
| `hermes_state_device`        | `/dev/vdb`                                                               | Persistent state disk to format/mount.                             |
| `hermes_state_mount`         | `{{ hermes_home }}/.hermes`                                              | Mount point + Hermes state dir.                                    |
| `hermes_llm_base_url`        | `http://qwen35-2b.llmkube-system.svc.cluster.local:8080/v1`             | OpenAI-compatible LLM endpoint (`model.base_url`).                 |
| `hermes_llm_model`           | `qwen35-2b`                                                              | Model name (`model.model`).                                        |
| `hermes_llm_api_key`         | `sk-no-key`                                                             | API key placeholder; qwen35-2b accepts any/none.                  |
| `hermes_cluster_service_cidr`| `172.30.0.0/16`                                                         | Service CIDR allowed on tcp/8080 in the nftables backstop.        |
| `hermes_install_url`         | `https://hermes-agent.nousresearch.com/install.sh`                      | Installer URL.                                                     |

## Rendered config (`~/.hermes/cli-config.yaml`)

Minimal and verified against `cli-config.yaml.example` in the upstream repo:

- `model.provider: custom`, `model.base_url`, `model.model` — custom
  OpenAI-compatible endpoint.
- `terminal.backend: local` — the exec backend (the example calls this section
  `terminal:`; `backend: local` runs tools locally in the agent cwd).
- `delegation.subagent_auto_approve: false` — subagents never auto-approve a
  dangerous-command prompt (auto-deny). This is the manual-approval knob: Hermes
  defaults to interactive human approval for dangerous commands, and this keeps
  delegated subagents from bypassing it.

Secrets live in `~/.hermes/.env` (mode `0600`): `OPENAI_API_KEY`,
`OPENAI_BASE_URL`.

## Security posture

- **systemd unit** (`~/.config/systemd/user/hermes.service`): `NoNewPrivileges`,
  `ProtectSystem=strict`, `ProtectHome=read-only` with `ReadWritePaths=%h/.hermes`,
  `PrivateTmp`, `RestrictAddressFamilies`, `RestrictNamespaces`,
  `SystemCallFilter=@system-service`, `LockPersonality`. Hermes has **no**
  in-process containment of its own (see the upstream `SECURITY.md`), so the
  unit provides the sandbox.
- **nftables backstop** (`/etc/nftables.d/hermes-egress.nft`): default-drop
  output allowing loopback, established/related, DNS, the LLM service CIDR on
  8080, and external HTTPS. This is defense-in-depth only — the precise control
  is the OVN-Kubernetes EgressFirewall (pins external reach to api.telegram.org).
  The ruleset is declare-then-delete at the top so each `nft -f` atomically
  replaces the table (idempotent re-runs, no duplicate-rule accumulation), and is
  dry-run validated (`nft -c -f`) before the live load.
- **skills** dir (`~/.hermes/skills`) is owned by the installer and left
  writable: Hermes's autonomous skill creation is a core feature, so it is **not**
  clamped read-only. A `0555` chmod would break that learning loop and is not real
  immutability anyway. **Proper immutable, Git-gated skills are deferred §5.5
  work** — do not approximate it with chmod.
- **journald** is set to persistent storage.

## Go-live (messaging deferred)

Telegram/messaging is intentionally deferred: no messaging config or secrets are
rendered, and the systemd unit is **installed but not enabled or started**. To go
live:

1. Add the Telegram bot token + chat allowlist to `~/.hermes/.env` (and any
   messaging block to `cli-config.yaml`).
2. Enable + start the unit as the `hermes` user:
   ```bash
   systemctl --user enable --now hermes.service
   ```
   (Lingering is already enabled by the configure phase — and expected from
   `linux_baseline` — so the user manager runs without an active login.)
