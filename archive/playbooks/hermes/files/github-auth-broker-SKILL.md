---
name: github-auth
description: "GitHub auth on this host: mint short-lived scoped tokens from the ghapp broker. No PAT/SSH/gh login."
version: 2.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [GitHub, Authentication, Git, ghapp, broker, token]
    related_skills: [github-pr-workflow, github-code-review, github-issues, github-repo-management]
---

# GitHub Authentication (ghapp token broker)

On THIS host, GitHub access is provided by a **GitHub App token broker**. There
is deliberately **no** standing GitHub credential — no personal access token, no
SSH key, no `gh auth` login, no `~/.git-credentials`, no `GITHUB_TOKEN` in the
environment. Do not try to create or look for any of those, and do not ask the
user for one. For **every** GitHub operation you mint a short-lived,
repository-scoped token from the broker and use it directly.

The broker mints as the GitHub App bot identity. It enforces a repository
allowlist and a permission ceiling: a `403`/`404` from the broker means the repo
or permission is outside policy — report it, do not try to work around it.

## The broker interface — ONE endpoint: mint a token

The broker listens on the unix socket in the `GHAPP_BROKER_SOCKET` environment
variable. It has a single endpoint, `POST /token`, that returns a token. It is
**not** a GitHub proxy — do not POST issues/PRs/GraphQL to it. You mint a token,
then call `https://api.github.com` (or `git`) yourself with that token.

```bash
# Mint a token scoped to ONE repo with ONLY the permission you need.
# permissions: contents (git clone/push), issues, pull_requests -> "read" or "write".
TOKEN=$(curl -s --unix-socket "$GHAPP_BROKER_SOCKET" \
  -X POST http://localhost/token \
  -H 'Content-Type: application/json' \
  -d '{"repo":"OWNER/REPO","permissions":{"contents":"read"}}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
```

The response JSON is `{"token":"ghs_…","expires_at":…,"repositories":[…],"permissions":{…}}`.
The token is a `ghs_…` installation token valid for about an hour. Mint a fresh
one per repo and per task; never cache, log, or print it.

## Using the token

### GitHub REST/GraphQL API — Bearer header

```bash
# Example: create an issue (needs permissions:{"issues":"write"})
TOKEN=$(curl -s --unix-socket "$GHAPP_BROKER_SOCKET" -X POST http://localhost/token \
  -H 'Content-Type: application/json' \
  -d '{"repo":"OWNER/REPO","permissions":{"issues":"write"}}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

curl -s -X POST https://api.github.com/repos/OWNER/REPO/issues \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -d '{"title":"...","body":"..."}'
```

Any REST call follows the same shape: mint with the right permission, then send
`Authorization: Bearer $TOKEN` to `https://api.github.com/...`.

### git over HTTPS — username `x-access-token`, token as password

```bash
# Clone / fetch (needs permissions:{"contents":"read"}); push needs "write".
TOKEN=$(curl -s --unix-socket "$GHAPP_BROKER_SOCKET" -X POST http://localhost/token \
  -H 'Content-Type: application/json' \
  -d '{"repo":"OWNER/REPO","permissions":{"contents":"write"}}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')

git clone "https://x-access-token:${TOKEN}@github.com/OWNER/REPO.git"
# For an existing checkout, set the remote the same way before push:
#   git remote set-url origin "https://x-access-token:${TOKEN}@github.com/OWNER/REPO.git"
```

Set `git config user.name "hermes-agent"` and a matching email before committing
if they are not already configured.

## Permission cheat-sheet

| Task | permissions to request |
|------|------------------------|
| clone / fetch / read code | `{"contents":"read"}` |
| commit & push | `{"contents":"write"}` |
| read/create/comment issues | `{"issues":"write"}` (or `"read"`) |
| open / review PRs | `{"pull_requests":"write"}` (plus `contents` to push the branch) |

## Do NOT

- Do NOT use `gh auth login`, `gh auth token`, or `gh auth status` for identity —
  `gh` is not authenticated here. (You may still use `gh api` **only** if you set
  `GH_TOKEN=$TOKEN` from a freshly minted broker token first.)
- Do NOT create or read personal access tokens, SSH keys, or `~/.git-credentials`.
- Do NOT POST GitHub resources (issues, comments, PRs, GraphQL) to the broker
  socket — its only endpoint is `POST /token`.
- Do NOT reuse a token across repos or paste it into files, logs, or messages.
