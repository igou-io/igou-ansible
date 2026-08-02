# tailscale_serve

Configure [Tailscale Serve](https://tailscale.com/kb/1242/tailscale-serve) on a
host that is **already joined to the tailnet**, so `tailscaled` terminates
tailnet HTTPS on `:443` (auto-provisioned cert, reachable only from the tailnet)
and proxies to a plain-HTTP listener on `127.0.0.1:<port>`.

Written for T3 Code on the devenv host: the t3 desktop app requires TLS and
`t3 serve` has no native cert support, so tailscaled does the termination.

Exists as a local role because `artis3n.tailscale` — the collection that owns
the tailnet join in `playbooks/tailscale/tailscale.yml` — has no serve
capability at v1.2.1 (checked 2026-08-02, latest release).

## Requirements

- The host is already joined to the tailnet and `tailscaled` is running (run
  this **after** `playbooks/tailscale/tailscale.yml`).
- The tailnet has the HTTPS-certificates feature enabled.
- Root on the target (the caller supplies `become`).

## Role variables

| Var | Default | Meaning |
|-----|---------|---------|
| `tailscale_serve_port` | *(none — intentionally undefined)* | Local plain-HTTP port to proxy `https://<magicdns-name>/` to. **When undefined the role does nothing at all.** |

`tailscale_serve_port` is deliberately **not** defaulted in
`defaults/main.yml`. The undefined-variable no-op is the role's contract: hosts
that do not set it (molecule containers, any devenv host without a served
service) skip both tasks. It is supplied from inventory, in igou-inventory
`group_vars/devenv/tailscale.yml`.

## Behavior

| Situation | Result |
|---|---|
| `tailscale_serve_port` undefined | both tasks skip, no failure |
| defined, but tailscaled down/absent | status read reports ok (`failed_when: false`), apply skips — no failure |
| defined, no existing proxy to that port | `tailscale serve --bg <port>` runs, reports **changed** |
| defined, proxy to that port already present | apply skips, reports ok — **adoptive/idempotent** |

Manual rollback on the host: `tailscale serve --https=443 off`.

## Example

```yaml
- name: Configure Tailscale Serve for devenv services
  hosts: "{{ ansible_limit | default('devenv') }}"
  become: true
  gather_facts: false
  tasks:
    - name: Configure Tailscale Serve
      ansible.builtin.include_role:
        name: tailscale_serve
```

Used by `playbooks/devenv/bootstrap.yml` (final play, after the tailnet join).
