#!/usr/bin/python
"""Declarative UniFi Network reconciler with isolated API transports."""

from __future__ import annotations

import json
import re
import ssl
from copy import deepcopy
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

from ansible.module_utils.basic import AnsibleModule


DOCUMENTATION = r"""
---
module: unifi_network_reconcile
short_description: Reconcile UniFi Network desired state
description:
  - Reads, normalizes, compares, and reconciles UniFi Network resources.
  - Supports the official Integration API and an explicitly selected legacy API.
options:
  config:
    description: Controller and desired-state configuration.
    type: dict
    required: true
author:
  - David Igou
"""

RETURN = r"""
facts:
  description: Sanitized discovered sites and adopted devices.
  returned: always
  type: dict
changes:
  description: Secret-safe planned or applied changes.
  returned: always
  type: list
"""

EXAMPLES = r"""
- name: Reconcile UniFi Network
  unifi_network_reconcile:
    config: "{{ unifi }}"
"""

VALID_STATES = {"present", "absent"}
VALID_OWNERSHIP = {"additive", "authoritative"}
VALID_SECURITY_MODES = {
    "open", "wpa2_personal", "wpa3_personal", "wpa2_wpa3_personal",
    "wpa2_enterprise", "wpa3_enterprise", "wpa2_wpa3_enterprise",
}
SECRET_PATHS = {"security.passphrase"}


class ApiError(Exception):
    """A safe API error without credential material."""


def nested_get(value, path):
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def normalize_dict(value, fields):
    result = {}
    for desired_key, source_key in fields.items():
        field_value = nested_get(value, source_key)
        if field_value is not None:
            result[desired_key] = field_value
    return result


def project_managed(current, desired):
    """Project current state onto only the fields recursively owned by desired."""
    result = {}
    for key, wanted in desired.items():
        found = current.get(key) if isinstance(current, dict) else None
        result[key] = project_managed(found or {}, wanted) if isinstance(wanted, dict) else found
    return result


def redact(path, value):
    return "<redacted>" if path in SECRET_PATHS and value is not None else value


def diff_fields(kind, name, before, after, prefix=""):
    changes = []
    for key in sorted(set(before) | set(after)):
        path = f"{prefix}.{key}" if prefix else key
        old = before.get(key)
        new = after.get(key)
        if isinstance(old, dict) and isinstance(new, dict):
            changes.extend(diff_fields(kind, name, old, new, path))
        elif old != new:
            changes.append(
                {"resource": f"{kind}/{name}", "field": path,
                 "before": redact(path, old), "after": redact(path, new)}
            )
    return changes


def plan_resources(kind, desired, current, ownership="additive"):
    """Build deterministic create/update/delete actions using name identity."""
    current_index = {}
    for item in current:
        name = item["name"]
        if name in current_index:
            raise ValueError(f"duplicate current {kind} natural identity: {name}")
        current_index[name] = item
    actions = []
    declared = set()
    for wanted in desired:
        name = wanted["name"]
        if name in declared:
            raise ValueError(f"duplicate desired {kind} natural identity: {name}")
        declared.add(name)
        state = wanted.get("state", "present")
        if state not in VALID_STATES:
            raise ValueError(f"invalid state for {kind}/{name}: {state}")
        found = current_index.get(name)
        managed = {key: value for key, value in wanted.items() if key != "state"}
        if state == "absent":
            if found:
                actions.append({"action": "delete", "name": name, "current": found})
            continue
        if found is None:
            actions.append({"action": "create", "name": name, "desired": managed})
            continue
        comparable = project_managed(found, managed)
        if comparable != managed:
            actions.append({"action": "update", "name": name, "current": found,
                            "desired": managed})
    if ownership == "authoritative":
        for name, found in current_index.items():
            if name not in declared:
                actions.append({"action": "delete", "name": name, "current": found,
                                "authoritative": True})
    return actions


class HttpClient:
    def __init__(self, base_url, validate_certs=True):
        context = ssl.create_default_context()
        if not validate_certs:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()), HTTPSHandler(context=context))

    def call(self, method, path, body=None, headers=None, query=None):
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        payload = json.dumps(body).encode() if body is not None else None
        request_headers = {"Accept": "application/json"}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        try:
            response = self.opener.open(Request(url, data=payload, headers=request_headers,
                                                method=method), timeout=30)
            raw = response.read()
            return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raise ApiError(f"UniFi API {method} {path} returned HTTP {exc.code}") from exc
        except (URLError, ValueError) as exc:
            raise ApiError(f"UniFi API {method} {path} failed: {exc}") from exc


class LegacyTransport:
    """Explicit compatibility transport for the private controller API."""

    NETWORK_FIELDS = {
        "name": "name", "enabled": "enabled", "vlan_id": "vlan",
        "purpose": "purpose", "vlan_enabled": "vlan_enabled",
        "ipv4_subnet": "ip_subnet", "dhcp_enabled": "dhcpd_enabled",
        "dhcp_start": "dhcpd_start", "dhcp_stop": "dhcpd_stop",
        "dhcp_guarding": "dhcpguard_enabled", "igmp_snooping": "igmp_snooping",
    }
    WIFI_FIELDS = {
        "name": "name", "enabled": "enabled", "hidden": "hide_ssid",
        "client_isolation": "l2_isolation", "pmf": "pmf_mode",
        "fast_roaming": "fast_roaming_enabled", "band_steering": "band_steering_mode",
        "bss_transition": "bss_transition", "uapsd": "uapsd_enabled",
        "arp_proxy": "proxy_arp", "multicast_to_unicast": "multicast_enhance_enabled",
    }

    def __init__(self, config):
        controller = config["controller"]
        self.client = HttpClient(controller["url"], controller.get("validate_certs", True))
        credentials = controller["credentials"]
        self.client.call("POST", "/api/login", {
            "username": credentials["username"], "password": credentials["password"],
            "remember": False,
        })
        sites = self.client.call("GET", "/api/self/sites").get("data", [])
        target = controller["site"]
        matches = [site for site in sites if target in (site.get("name"), site.get("desc"))]
        if len(matches) != 1:
            raise ApiError(f"site identity {target!r} matched {len(matches)} sites")
        self.site = matches[0]
        self.prefix = f"/api/s/{self.site['name']}"

    def _list(self, endpoint):
        data = self.client.call("GET", f"{self.prefix}/rest/{endpoint}")
        if not isinstance(data.get("data"), list):
            raise ApiError(f"malformed UniFi response for {endpoint}")
        return data["data"]

    def discover(self):
        raw_networks = self._list("networkconf")
        networks = []
        network_names = {}
        for item in raw_networks:
            normalized = normalize_dict(item, self.NETWORK_FIELDS)
            normalized["_id"] = item["_id"]
            networks.append(normalized)
            network_names[item["_id"]] = item["name"]
        wifi = []
        for item in self._list("wlanconf"):
            normalized = normalize_dict(item, self.WIFI_FIELDS)
            normalized["_id"] = item["_id"]
            normalized["network"] = network_names.get(item.get("networkconf_id"))
            normalized["bands"] = [{"2g": "2.4", "5g": "5", "6g": "6"}.get(band, band)
                                   for band in item.get("wlan_bands", [])]
            legacy_mode = item.get("wpa_mode", "open").lower()
            normalized["security"] = {
                "mode": "open" if legacy_mode == "open" else f"{legacy_mode}_personal",
                "passphrase": item.get("x_passphrase"),
            }
            normalized["dtim"] = {"2.4": item.get("dtim_ng"), "5": item.get("dtim_na")}
            normalized["minimum_data_rate_kbps"] = {
                "2.4": item.get("minrate_ng_data_rate_kbps"),
                "5": item.get("minrate_na_data_rate_kbps"),
            }
            wifi.append(normalized)
        devices = self.client.call("GET", f"{self.prefix}/stat/device").get("data", [])
        facts = {"site": {"name": self.site["name"], "description": self.site.get("desc")},
                 "access_points": []}
        for device in devices:
            if device.get("type") != "uap":
                continue
            radios = [{key: radio.get(source) for key, source in {
                "band": "radio", "channel": "channel", "channel_width": "ht",
                "transmit_power": "tx_power", "minimum_rssi": "min_rssi",
                "minimum_rssi_enabled": "min_rssi_enabled"}.items()}
                      for radio in device.get("radio_table", [])]
            facts["access_points"].append({
                "mac": device.get("mac"), "name": device.get("name"),
                "model": device.get("model"), "adopted": device.get("adopted"),
                "status": "ONLINE" if device.get("state") == 1 else "OFFLINE",
                "site": self.site["name"], "radios": radios,
            })
        return networks, wifi, facts

    def payload(self, kind, desired, network_ids):
        if kind == "network":
            payload = {"name": desired["name"]}
            payload.update({self.NETWORK_FIELDS[key]: value for key, value in desired.items()
                            if key in self.NETWORK_FIELDS and key != "name"})
            return payload
        payload = {"name": desired["name"]}
        payload.update({self.WIFI_FIELDS[key]: value for key, value in desired.items()
                        if key in self.WIFI_FIELDS and key != "name"})
        if "network" in desired:
            payload["networkconf_id"] = network_ids[desired["network"]]
        if "bands" in desired:
            payload["wlan_bands"] = [{"2.4": "2g", "5": "5g", "6": "6g"}[band]
                                     for band in desired["bands"]]
        if "security" in desired:
            security = desired["security"]
            mode = security["mode"].lower()
            payload["security"] = "wpapsk" if mode != "open" else "open"
            payload["wpa_mode"] = mode.removesuffix("_personal")
            if security.get("passphrase"):
                payload["x_passphrase"] = security["passphrase"]
        if "dtim" in desired:
            payload.update({"dtim_ng": desired["dtim"].get("2.4"),
                            "dtim_na": desired["dtim"].get("5")})
        if "minimum_data_rate_kbps" in desired:
            rates = desired["minimum_data_rate_kbps"]
            payload.update({"minrate_ng_data_rate_kbps": rates.get("2.4"),
                            "minrate_na_data_rate_kbps": rates.get("5")})
        return payload

    def apply(self, kind, action, network_ids):
        endpoint = "networkconf" if kind == "network" else "wlanconf"
        if action["action"] == "create":
            self.client.call("POST", f"{self.prefix}/rest/{endpoint}",
                             self.payload(kind, action["desired"], network_ids))
        elif action["action"] == "update":
            self.client.call("PUT", f"{self.prefix}/rest/{endpoint}/{action['current']['_id']}",
                             self.payload(kind, action["desired"], network_ids))
        else:
            self.client.call("DELETE", f"{self.prefix}/rest/{endpoint}/{action['current']['_id']}")


class IntegrationTransport:
    """Supported Ubiquiti Network Integration API transport."""

    def __init__(self, config):
        controller = config["controller"]
        self.client = HttpClient(controller["url"], controller.get("validate_certs", True))
        self.headers = {"X-API-KEY": controller["credentials"]["api_key"]}
        self.root = controller.get("integration_path", "/proxy/network/integration")
        sites = self._page("/v1/sites")
        target = controller["site"]
        matches = [site for site in sites if target in (
            site.get("id"), site.get("internalReference"), site.get("name"))]
        if len(matches) != 1:
            raise ApiError(f"site identity {target!r} matched {len(matches)} sites")
        self.site = matches[0]
        self.prefix = f"/v1/sites/{self.site['id']}"

    def _call(self, method, path, body=None, query=None):
        return self.client.call(method, self.root + path, body, self.headers, query)

    def _page(self, path):
        offset = 0
        result = []
        while True:
            page = self._call("GET", path, query={"offset": offset, "limit": 100})
            data = page.get("data")
            if not isinstance(data, list):
                raise ApiError(f"malformed paginated response for {path}")
            result.extend(data)
            offset += len(data)
            if offset >= page.get("totalCount", len(result)) or not data:
                return result

    @staticmethod
    def _network(item):
        return {"_id": item["id"], "name": item["name"],
                "enabled": item.get("enabled"), "vlan_id": item.get("vlanId"),
                "management": item.get("management")}

    @staticmethod
    def _wifi(item, network_names):
        security = item.get("securityConfiguration", {})
        network = item.get("network", {})
        result = {
            "_id": item["id"], "name": item["name"], "enabled": item.get("enabled"),
            "hidden": item.get("hideName"), "client_isolation": item.get("clientIsolationEnabled"),
            "pmf": security.get("pmfMode"), "fast_roaming": security.get("fastRoamingEnabled"),
            "band_steering": item.get("bandSteeringEnabled"),
            "bss_transition": item.get("bssTransitionEnabled"), "uapsd": item.get("uapsdEnabled"),
            "arp_proxy": item.get("arpProxyEnabled"),
            "multicast_to_unicast": item.get("multicastToUnicastConversionEnabled"),
            "bands": item.get("broadcastingFrequenciesGHz"),
            "security": {"mode": (security.get("type") or "").lower(),
                         "passphrase": security.get("passphrase")},
            "dtim": item.get("dtimPeriodByFrequencyGHzOverride"),
            "minimum_data_rate_kbps": item.get("basicDataRateKbpsByFrequencyGHz"),
        }
        if network.get("type") == "SPECIFIC":
            result["network"] = network_names.get(network.get("networkId"))
        return result

    def discover(self):
        networks = [self._network(item) for item in self._page(f"{self.prefix}/networks")]
        network_names = {item["_id"]: item["name"] for item in networks}
        overviews = self._page(f"{self.prefix}/wifi/broadcasts")
        details = [self._call("GET", f"{self.prefix}/wifi/broadcasts/{item['id']}")
                   for item in overviews]
        wifi = [self._wifi(item, network_names) for item in details]
        devices = self._page(f"{self.prefix}/devices")
        access_points = []
        for device in devices:
            if "accessPoint" not in device.get("features", []):
                continue
            detail = self._call("GET", f"{self.prefix}/devices/{device['id']}")
            radios = [{"band": radio.get("frequencyGHz"), "channel": radio.get("channel"),
                       "channel_width": radio.get("channelWidthMHz")}
                      for radio in detail.get("interfaces", {}).get("radios", [])]
            access_points.append({
                "mac": device.get("macAddress"), "name": device.get("name"),
                "model": device.get("model"), "adopted": True, "status": device.get("state"),
                "site": self.site["name"], "radios": radios,
            })
        return networks, wifi, {"site": self.site, "access_points": access_points}

    @staticmethod
    def payload(kind, desired, network_ids):
        if kind == "network":
            return {"management": desired.get("management", "UNMANAGED"),
                    "name": desired["name"], "enabled": desired.get("enabled", True),
                    "vlanId": desired["vlan_id"]}
        security = deepcopy(desired["security"])
        security["type"] = security.pop("mode").upper()
        payload = {
            "type": "STANDARD", "name": desired["name"],
            "enabled": desired.get("enabled", True),
            "securityConfiguration": security,
            "clientIsolationEnabled": desired.get("client_isolation", False),
            "hideName": desired.get("hidden", False),
            "multicastToUnicastConversionEnabled": desired.get("multicast_to_unicast", False),
            "uapsdEnabled": desired.get("uapsd", False),
            "broadcastingFrequenciesGHz": desired.get("bands", ["2.4", "5"]),
            "arpProxyEnabled": desired.get("arp_proxy", False),
            "bssTransitionEnabled": desired.get("bss_transition", True),
        }
        if desired.get("network"):
            payload["network"] = {"type": "SPECIFIC", "networkId": network_ids[desired["network"]]}
        optional = {"bandSteeringEnabled": "band_steering",
                    "dtimPeriodByFrequencyGHzOverride": "dtim",
                    "basicDataRateKbpsByFrequencyGHz": "minimum_data_rate_kbps"}
        payload.update({api: desired[key] for api, key in optional.items() if key in desired})
        return payload

    def apply(self, kind, action, network_ids):
        endpoint = "networks" if kind == "network" else "wifi/broadcasts"
        path = f"{self.prefix}/{endpoint}"
        if action["action"] == "create":
            self._call("POST", path, self.payload(kind, action["desired"], network_ids))
        elif action["action"] == "update":
            self._call("PUT", f"{path}/{action['current']['_id']}",
                       self.payload(kind, action["desired"], network_ids))
        else:
            self._call("DELETE", f"{path}/{action['current']['_id']}")


def validate(config):
    if not isinstance(config, dict) or "controller" not in config:
        raise ValueError("unifi_network_config.controller is required")
    controller = config["controller"]
    for key in ("url", "site", "transport", "credentials"):
        if key not in controller:
            raise ValueError(f"controller.{key} is required")
    if controller["transport"] not in ("legacy", "integration"):
        raise ValueError("controller.transport must be legacy or integration")
    for kind in ("networks", "wifi"):
        ownership = config.get("management", {}).get(kind, "additive")
        if ownership not in VALID_OWNERSHIP:
            raise ValueError(f"invalid ownership mode for {kind}: {ownership}")
    networks = [item["name"] for item in config.get("networks", []) if item.get("state", "present") == "present"]
    if len(networks) != len(set(networks)):
        raise ValueError("duplicate network names")
    vlan_ids = [item.get("vlan_id") for item in config.get("networks", [])
                if item.get("state", "present") == "present" and item.get("vlan_id") is not None]
    if len(vlan_ids) != len(set(vlan_ids)):
        raise ValueError("duplicate network VLAN IDs")
    if controller["transport"] == "integration":
        for network in config.get("networks", []):
            if network.get("state", "present") == "present" and network.get("vlan_id") is None:
                raise ValueError(f"integration network/{network['name']} requires vlan_id")
    for wifi in config.get("wifi", []):
        if wifi.get("state", "present") != "present":
            continue
        if wifi.get("network") not in networks:
            raise ValueError(f"wifi/{wifi['name']} references unknown network {wifi.get('network')}")
        mode = wifi.get("security", {}).get("mode")
        if mode not in VALID_SECURITY_MODES:
            raise ValueError(f"unsupported security mode for wifi/{wifi['name']}: {mode}")
        if config.get("secrets", {}).get("manage_wifi_passphrases", False):
            if mode != "open" and not wifi.get("security", {}).get("passphrase"):
                raise ValueError(f"wifi/{wifi['name']} requires a passphrase secret")
        if controller["transport"] == "integration" and mode.endswith("_personal"):
            if not wifi.get("security", {}).get("passphrase"):
                raise ValueError(f"integration wifi/{wifi['name']} requires passphrase for full PUT")
    wifi_names = [item["name"] for item in config.get("wifi", [])]
    if len(wifi_names) != len(set(wifi_names)):
        raise ValueError("duplicate WiFi broadcast names")
    macs = []
    for access_point in config.get("access_points", []):
        mac = access_point.get("mac", "").lower()
        if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
            raise ValueError(f"malformed access point MAC address: {mac}")
        macs.append(mac)
    if len(macs) != len(set(macs)):
        raise ValueError("duplicate access point MAC addresses")


def action_changes(kind, action):
    resource = f"{kind}/{action['name']}"
    if action["action"] != "update":
        return [{"resource": resource, "action": action["action"]}]
    desired = action["desired"]
    current = project_managed(action["current"], desired)
    return diff_fields(kind, action["name"], current, desired)


def reconcile(config, transport, check_mode=False):
    """Reconcile through an injected transport; used by the module and tests."""
    current_networks, current_wifi, facts = transport.discover()
    network_actions = plan_resources(
        "network", config.get("networks", []), current_networks,
        config.get("management", {}).get("networks", "additive"))
    wifi_actions = plan_resources(
        "wifi", config.get("wifi", []), current_wifi,
        config.get("management", {}).get("wifi", "additive"))
    changes = []
    for kind, actions in (("Network", network_actions), ("WiFi", wifi_actions)):
        for action in actions:
            changes.extend(action_changes(kind, action))
    if not check_mode:
        network_ids = {item["name"]: item["_id"] for item in current_networks}
        for action in [item for item in network_actions if item["action"] != "delete"]:
            transport.apply("network", action, network_ids)
        if network_actions:
            refreshed, _, _ = transport.discover()
            network_ids = {item["name"]: item["_id"] for item in refreshed}
        for action in wifi_actions:
            transport.apply("wifi", action, network_ids)
        for action in [item for item in network_actions if item["action"] == "delete"]:
            transport.apply("network", action, network_ids)
    return changes, facts


def run_module():
    module = AnsibleModule(
        argument_spec={"config": {"type": "dict", "required": True, "no_log": True}},
        supports_check_mode=True,
    )
    config = deepcopy(module.params["config"])
    try:
        validate(config)
        transport_class = LegacyTransport if config["controller"]["transport"] == "legacy" else IntegrationTransport
        transport = transport_class(config)
        changes, facts = reconcile(config, transport, module.check_mode)
        prepared = "\n".join(
            f"{item['resource']}: {item.get('action', item.get('field'))}"
            + (f" {item.get('before')} -> {item.get('after')}" if "field" in item else "")
            for item in changes
        )
        module.exit_json(changed=bool(changes), changes=changes, facts=facts,
                         diff={"prepared": prepared})
    except (ApiError, KeyError, TypeError, ValueError) as exc:
        module.fail_json(msg=str(exc))


def main():
    run_module()


if __name__ == "__main__":
    main()
