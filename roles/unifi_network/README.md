# UniFi Network role

This role reconciles a human-oriented UniFi desired-state model. It discovers
current objects, resolves generated IDs from names or MAC addresses, projects
current objects onto only the declared fields, and then creates, updates, or
explicitly deletes drift.

## Transports

- `integration` uses Ubiquiti's supported Network Integration API and an API
  key. It is intended for UniFi OS consoles/server and defaults to
  `/proxy/network/integration`.
- `legacy` is a deliberately separated compatibility backend for the classic
  self-hosted Network Application. It uses a local admin session because that
  product does not provide local Integration API-key creation.

The official `ubiquiti.unifi_api` collection is not used. Its mutable `1.0.0`
archive cannot be reproducibly pinned, its current OpenAPI fallback raises on
the portal response shape, it assumes the UniFi OS proxy path, and its module
executes write requests in check mode.

## Behavior

Networks and standard WiFi broadcasts support additive or authoritative
ownership, with additive as the default. `state: absent` is always explicit.
Check mode performs discovery and comparison but no writes. Diff output is
generated from managed fields only and replaces passphrases with `<redacted>`.

Adopted APs are discovery-only. The official API reports radio channel and
width, but has no radio configuration update endpoint; transmit power and
minimum RSSI are available only where the selected discovery transport returns
them.

The role performs no secret lookup. Inventory supplies already-resolved
credentials and must attach `onepassword-connect-token` in AAP.

## Example

```yaml
- role: unifi_network
  vars:
    unifi_network_config: "{{ unifi }}"
```

