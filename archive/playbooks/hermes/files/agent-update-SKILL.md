---
name: agent-update
description: "Update the coding-agent CLIs (claude, codex, opencode, cursor) at runtime with agent-update when one is missing, outdated, or rejected by its vendor."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Tooling, Updates, claude, codex, opencode, cursor, terminal]
    related_skills: [github-auth]
---

# Runtime coding-agent updates (agent-update)

On THIS host the coding-agent CLIs — `claude`, `codex`, `opencode`,
`cursor-agent` — resolve from a runtime overlay that you are allowed to update
yourself. You do NOT need to ask the operator to rebuild an image to get a
newer agent version.

## When to use

Run an update when, and only when, you hit a concrete version problem:

- a coding agent CLI is **missing** from PATH,
- a vendor/API responds that the CLI is **too old or unsupported**,
- a documented flag or subcommand you need **doesn't exist** in the installed
  version,
- the operator asks you to update an agent.

Do not update speculatively before every task — a nightly timer already keeps
the overlay fresh, and each update costs a download.

## How

```bash
agent-update status              # what's installed in the overlay
agent-update check               # current vs latest, no install
agent-update codex               # update one tool (claude|codex|opencode|cursor)
agent-update all                 # update everything
agent-update --force codex       # bypass the recent-check TTL
```

Updates are verified where the vendor supports it (claude: PGP; codex:
published SHA256SUMS), staged, smoke-tested, and flipped atomically — a failed
download never replaces a working binary. If an update makes things worse:

```bash
agent-update rollback codex      # previous kept version
agent-update reset               # wipe the overlay; baked baseline versions apply
```

## Notes

- The overlay lives at `~/.agents/runtime` (terminal container) /
  `~/.hermes/agent-runtime` (host); its `bin/` is already first on PATH.
  After an update, re-run `hash -r` (or just start the CLI) — no restarts
  needed.
- If a tool is reported as `pinned`, the operator froze it deliberately; do
  not unpin it without being asked.
- If version discovery fails repeatedly (GitHub API rate limit), wait and
  retry later rather than looping.
