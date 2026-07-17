# Design: runtime-updatable coding agents for Hermes ("agent-runtime overlay")

**Status:** proposed — 2026-07-17
**Repos touched:** igou-ansible only (no igou-devenv, no igou-openshift changes)
**Goal:** Hermes can pull new versions of the coding-agent CLIs (claude, codex,
opencode, cursor-agent) at runtime, on demand, without waiting for the weekly
image pipeline — on both of its execution surfaces (podman terminal containers
and the VM host).

## Accepted trade-off (explicit)

Today the CLIs are review-time pinned, signature/SHA-verified, 3-day
age-gated, and shipped through a reviewed image build. This design adds an
**unpinned, latest-from-vendor path** on top. Operator has accepted this.
What is kept: vendor-side verification where it exists (Claude PGP, codex
SHA256SUMS), TLS to allowlisted vendor endpoints only (EgressFirewall
unchanged — every needed domain is already allowed: `claude.ai`,
`downloads.claude.ai`, `storage.googleapis.com`, `github.com`,
`objects.githubusercontent.com`, `opencode.ai`, `cursor.com`,
`downloads.cursor.com`). What is given up: the age gate, review-time pinning,
and reproducibility for the overlay copies. The baked image pins remain as a
clean fallback baseline and keep rising through the (now self-merging) weekly
pipeline.

## Architecture

One persistent **overlay directory on the state disk**, one **updater
script**, PATH precedence on both surfaces, and a **Hermes skill** telling the
agent it may update itself.

```
/home/hermes/.hermes/agent-runtime/          # on the state PVC → survives VM
├── bin/                                     #   rebuilds AND image bumps
│   ├── claude        -> ../tools/claude/<ver>/claude
│   ├── codex         -> ../tools/codex/<ver>/bin/codex
│   ├── opencode      -> ../tools/opencode/<ver>/opencode
│   ├── cursor-agent  -> ../tools/cursor-agent/<ver>/cursor-agent
│   ├── agent         -> cursor-agent
│   └── agent-update  # the updater itself (managed by Ansible, see below)
├── tools/<name>/<version>/                  # versioned installs, keep last 3
├── bashenv.d/10-agent-runtime.sh            # PATH prepend snippet
└── state/                                   # pins.yml (holds), last-check
                                             # stamps, update.log, flock
```

### Resolution order (both surfaces)

`agent-runtime/bin` is prepended to PATH. Overlay present → overlay version
runs. Overlay empty/wiped → image-baked (container) or `~/.local/bin` (host)
pinned versions run. Falling back is always one `agent-update reset` away.

### Container wiring (configure.yml `hermes_terminal_config`)

- Volume: `/home/hermes/.hermes/agent-runtime:/home/igou/.agents/runtime:Z`
  (rw — the agent updates from inside its own terminal).
- PATH precedence: single-file bind
  `…/agent-runtime/bashenv.d/10-agent-runtime.sh:/home/igou/.bashrc.d/10-agent-runtime.sh:ro,Z`.
  Verified in image `2026.07.16`: the skel `~/.bashrc` sources `~/.bashrc.d/*`,
  and the terminal tool's `BASH_ENV=/home/igou/.bashrc` makes every
  non-interactive terminal command inherit it. Single-file bind avoids hiding
  any image-shipped snippets; inode-staleness is a non-issue because Ansible
  writes the snippet in place and containers are short-lived.
  The snippet: `export PATH="$HOME/.agents/runtime/bin:$PATH"` guarded by a
  dir-exists check.
- keep-id note: host uid 1001 (hermes) maps to container uid 1000 (igou), so
  ownership is coherent; host CentOS Stream 10 and container CentOS Stream 10
  share glibc, so the same binaries run on both surfaces (codex is musl-static
  anyway).

### Host wiring (setup-os.yml)

Profile drop-in for the hermes user (`~/.bashrc.d/10-agent-runtime.sh`,
same snippet, host path `~/.hermes/agent-runtime/bin`). This makes the overlay
the effective version on the host too — which also fixes the long-standing
"host CLIs frozen by creates: guards" problem *and* mitigates the
dashboard-chat-runs-on-host gap (Finding 1 from the dispatch test): whichever
surface a session lands on, it resolves the same current CLIs.

### `agent-update` (one script, Ansible-templated, ~200 lines of bash)

```
agent-update [--check] [--force] [all|claude|codex|opencode|cursor]
agent-update pin <tool> <version> | unpin <tool>
agent-update rollback <tool>          # flip symlink to previous kept version
agent-update reset                    # wipe overlay → fall back to baked pins
agent-update status                   # table: tool, overlay ver, baseline ver, held?
```

Per-tool update flow:
1. `flock` on `state/.lock` (containers, host, timer can race).
2. Skip if pinned/held, or checked within TTL (default 6h; `--force` bypasses).
3. Resolve latest:
   - claude: `https://claude.ai/install.sh` version discovery (or the
     `stable` manifest it queries), download tarball + **verify PGP** against
     the same pinned fingerprint the Dockerfile uses.
   - codex: GitHub API latest `rust-v*` release, download
     `codex-package-<triple>.tar.gz`, **verify against the release's
     `codex-package_SHA256SUMS`**.
   - opencode: GitHub API latest, download tarball (TLS-only; no upstream
     sums — accepted).
   - cursor: `cursor.com/install` version probe →
     `downloads.cursor.com/lab/<ver>/linux/x64/agent-cli-package.tar.gz`
     (TLS-only — accepted).
4. Extract into `tools/<name>/<version>.partial/`, smoke-run `--version`,
   atomic rename, flip the `bin/` symlink, prune to last 3 versions, log to
   `state/update.log`.
5. Never touches the baked installs — overlay only.

The script is **managed by Ansible** (template in `playbooks/hermes/templates/`)
and copied to BOTH `~/.local/bin/agent-update` (host, root-owned 0755) and
`agent-runtime/bin/` at converge. The copy inside the overlay is on the
agent-writable state disk — a compromised agent could tamper with it, but it
could equally just curl binaries itself; the overlay is inside the trust
boundary the operator already accepted. Each converge re-asserts both copies.

### Freshness + "as needed" triggers

1. **Hermes skill** (`~/.hermes/skills/tools/agent-update/SKILL.md`, installed
   by configure.yml like the broker-first github-auth skill): tells the agent
   that when a coding CLI is missing, outdated, or a vendor rejects its
   version, it should run `agent-update <tool>` in its terminal and retry.
   This is the "at run time as needed" path.
2. **Nightly systemd --user timer** on the VM host
   (`agent-runtime-update.timer` → `agent-update all`), so the overlay stays
   fresh even if the agent never asks. Host-side because egress always allows
   it and it avoids depending on Hermes cron (whose approvals are
   `cron_mode: deny`).
3. The weekly image pipeline continues unchanged as the reviewed baseline
   floor (and covers every other tool in the image).

### What this does NOT change

- Hermes config.yaml, model routing, delegation settings — untouched.
- EgressFirewall / NetworkPolicies — untouched (verified all endpoints already
  allowed).
- igou-devenv image contents and its Renovate pipeline — untouched.
- The gateway's hardened podman backend and approval behavior — untouched.

### Failure modes considered

- **Bad vendor release**: `agent-update rollback <tool>` (previous 2 kept) or
  `pin` to hold; worst case `reset` to baked pins.
- **Concurrent updates**: flock; atomic dir rename + symlink flip means a
  mid-update container exec still resolves a complete install.
- **State-disk pressure**: 4 tools × ≤3 versions ≈ a few GB max on a 30Gi PVC;
  prune enforced.
- **VM rebuild**: overlay lives on the state PVC → survives; snippet + script
  re-asserted by converge.
- **Partial download**: `.partial` staging + smoke-run gate before flip.

## Implementation plan (single igou-ansible PR)

1. `templates/agent-update.sh.j2`, `templates/agent-runtime-path.sh.j2`,
   `templates/agent-runtime-update.{service,timer}.j2`,
   `files/agent-update-SKILL.md`.
2. configure.yml: create overlay dirs, install script+snippet+skill, add the
   two mounts to `hermes_terminal_config.docker_volumes`, enable the timer.
3. setup-os.yml: host profile drop-in.
4. Verify (live): `agent-update status` → baseline versions; `agent-update all`
   → overlay populated; in-container `claude --version` shows overlay version;
   `agent-update reset` → falls back to baked pins; timer fires.
