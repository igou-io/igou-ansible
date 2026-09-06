# UniFi Network configuration as code

`playbooks/unifi/configure.yml` applies the `unifi_network` role to the
declarative `unifi` variable in `igou-inventory/group_vars/all/unifi.yml`.

## Verified controller reality

The live controller is Network Application `10.6.101`, self-hosted in the
existing TrueNAS container. It is not UniFi OS Server. Although the application
routes `/integration/v1` and returns the expected 403 without a key, its UI does
not expose local Integration API-key creation and the supported API cannot be
authenticated on this deployment. The imported environment therefore selects
the explicit `legacy` backend. Migrating the controller to UniFi OS Server is
the supported route to selecting `integration`.

The official API supports sites, networks, WiFi broadcasts, adopted-device
inventory, firewall zones, ACL rules, and traffic matching lists in this API
generation. This initial role manages networks and WiFi broadcasts and exposes
adopted AP facts. Firewall/ACL resources are future schema additions. Radio
channel and width are discovery-only; the official API has no device radio
update operation.

The imported `Default` network is controller-managed in the legacy application:
`192.168.1.1/24` with DHCP enabled for `192.168.1.6` through
`192.168.1.254`. The remaining imported networks are VLAN-only objects; their
layer-3 configuration remains owned by RouterOS.

## Operations

```bash
ansible-navigator run --mode stdout playbooks/unifi/configure.yml \
  -i igou-inventory/inventory.yaml --check --diff

ansible-navigator run --mode stdout playbooks/unifi/configure.yml \
  -i igou-inventory/inventory.yaml
```

AAP config-as-code defines `unifi_configuration_check` as `job_type: check`
with diff enabled and `unifi_configuration_apply` as `job_type: run`. Both use
the shared `igou_ansible` project, `igou_inventory`, `igou-awx-ee`, and the
manually bootstrapped `onepassword-connect-token` credential. Inventory resolves
the `username` and `password` fields from `lab_agents/unifi`; secret values never
enter Git or job output.

## Adoption and safety

Ownership defaults to `additive`. `authoritative` may delete undeclared objects
within the opted-in class and must be enabled explicitly. Re-import by running
read-only discovery first, updating only non-secret inventory fields, and then
requiring a zero-change `--check --diff` before an apply.

The legacy API returns WLAN passphrases. They were neither printed nor committed.
The current import deliberately leaves them unmanaged, so existing broadcasts
are preserved but PSKs are not yet rebuildable. To manage them, create concealed
per-WLAN fields in `lab_agents/unifi`, add full lookup expressions under each
WiFi `security.passphrase`, and set `secrets.manage_wifi_passphrases: true`.
Legacy-only WLAN values observed but deliberately not owned are
`no2ghz_oui=true`, `iapp_enabled=true`, and `group_rekey=0`; they have no
equivalent in the supported Integration API desired-state surface.
