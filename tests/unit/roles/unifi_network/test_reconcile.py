"""Unit tests for transport-independent UniFi reconciliation."""

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[4] / "roles/unifi_network/library/unifi_network_reconcile.py"
SPEC = importlib.util.spec_from_file_location("unifi_network_reconcile", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_missing_object_creates():
    actions = MODULE.plan_resources("network", [{"name": "Users"}], [])
    assert actions[0]["action"] == "create"


def test_equivalent_object_is_unchanged():
    assert MODULE.plan_resources("network", [{"name": "Users", "vlan_id": 20}],
                                 [{"name": "Users", "vlan_id": 20, "_id": "generated"}]) == []


def test_changed_managed_field_updates():
    actions = MODULE.plan_resources("network", [{"name": "Users", "vlan_id": 20}],
                                    [{"name": "Users", "vlan_id": 21}])
    assert actions[0]["action"] == "update"


def test_absent_object_deletes():
    actions = MODULE.plan_resources("network", [{"name": "Users", "state": "absent"}],
                                    [{"name": "Users"}])
    assert actions[0]["action"] == "delete"


def test_server_generated_fields_are_ignored():
    desired = [{"name": "Users", "enabled": True}]
    current = [{"name": "Users", "enabled": True, "id": "uuid", "metadata": {"x": 1}}]
    assert MODULE.plan_resources("network", desired, current) == []


def test_additive_preserves_unknown_resources():
    assert MODULE.plan_resources("network", [], [{"name": "Unknown"}], "additive") == []


def test_authoritative_identifies_unknown_resources():
    actions = MODULE.plan_resources("network", [], [{"name": "Unknown"}], "authoritative")
    assert actions == [{"action": "delete", "name": "Unknown",
                        "current": {"name": "Unknown"}, "authoritative": True}]


def test_check_mode_reports_change_without_transport_write():
    class FakeTransport:
        writes = []

        def discover(self):
            return [], [], {"site": {"name": "default"}, "access_points": []}

        def apply(self, kind, action, network_ids):
            self.writes.append((kind, action, network_ids))

    transport = FakeTransport()
    config = {"networks": [{"name": "Guest", "enabled": True}]}
    changes, _ = MODULE.reconcile(config, transport, check_mode=True)
    assert changes == [{"resource": "Network/Guest", "action": "create"}]
    assert transport.writes == []


def test_secret_diff_is_redacted():
    changes = MODULE.diff_fields("WiFi", "home", {"security": {"passphrase": "old"}},
                                 {"security": {"passphrase": "new"}})
    assert changes[0]["before"] == "<redacted>"
    assert changes[0]["after"] == "<redacted>"
    assert "old" not in repr(changes) and "new" not in repr(changes)


def test_nested_unmanaged_security_fields_are_ignored():
    desired = [{"name": "home", "security": {"mode": "WPA2_PERSONAL"}}]
    current = [{"name": "home", "security": {"mode": "WPA2_PERSONAL",
                                                "passphrase": "server-secret"}}]
    assert MODULE.plan_resources("wifi", desired, current) == []


def test_duplicate_current_match_fails():
    with pytest.raises(ValueError, match="duplicate current"):
        MODULE.plan_resources("wifi", [{"name": "home"}],
                              [{"name": "home"}, {"name": "home"}])
