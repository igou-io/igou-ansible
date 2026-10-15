"""Render/selection regressions without credentials or a cluster."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("public_share", ROOT / "filter_plugins/public_share.py")
share = importlib.util.module_from_spec(spec)
spec.loader.exec_module(share)
BACKEND = {"service": "arena-public", "tailnet_fqdn": "ocp-public-arena.example.ts.net",
           "ports": {"HTTP": 8080, "TCP": 27015, "UDP": 27016}}


class PublicShareTests(unittest.TestCase):
    def render(self, **kwargs):
        return share.render(BACKEND, "ps-42", "aap-user-7", "2026-10-16T18:00:00Z",
                            kwargs.get("protocols", ["HTTP", "TCP", "UDP"]),
                            kwargs.get("tcp_port", 27010), kwargs.get("udp_port", 27018))

    def test_all_forwarding_resources_have_matching_expiry_and_owner(self):
        objects = self.render()
        self.assertEqual(len(objects), 7)
        self.assertEqual({o["kind"] for o in objects},
                         {"Service", "ReferenceGrant", "NetworkPolicy", "HTTPRoute", "TCPRoute", "UDPRoute"})
        for obj in objects:
            self.assertEqual(obj["metadata"]["labels"][share.ID], "ps-42")
            self.assertEqual(obj["metadata"]["annotations"][share.OWNER], "aap-user-7")
            self.assertEqual(obj["metadata"]["annotations"][share.EXPIRY], "2026-10-16T18:00:00Z")
        http = next(o for o in objects if o["kind"] == "HTTPRoute")
        self.assertEqual(http["spec"]["hostnames"], ["ps-42.igou.io"])
        self.assertEqual(http["spec"]["rules"][0]["timeouts"], {"request": "0s"})
        bridge = next(o for o in objects if o["metadata"]["namespace"] == "tailscale" and o["kind"] == "Service")
        self.assertEqual(bridge["spec"]["selector"]["tailscale.com/parent-resource-ns"], "public-services")

    def test_summary_returns_public_endpoints(self):
        result = share.summary(self.render())[0]
        self.assertEqual(result['endpoints'], ['https://ps-42.igou.io',
                         'tcp://ps-42.igou.io:27010', 'udp://ps-42.igou.io:27018'])

    def test_catalog_hostname_matches_verified_namespace_service(self):
        endpoint = {"metadata": {"namespace": "public-services", "uid": "approved",
                    "annotations": {"tailscale.com/hostname": "ocp-public-arena"}},
                    "spec": {"type": "LoadBalancer", "loadBalancerClass": "tailscale",
                    "selector": {"app": "arena"}, "ports": [{"port": 8080}]}}
        self.assertTrue(share.check_backend(endpoint, BACKEND, ["HTTP"]))
        endpoint['metadata']['annotations']['tailscale.com/hostname'] = 'other-service'
        with self.assertRaises(share.AnsibleFilterError):
            share.check_backend(endpoint, BACKEND, ["HTTP"])

    def test_protocols_and_pool_are_validated(self):
        for values in ({"tcp_port": 22}, {"udp_port": 6443}, {"protocols": ["SMTP"]}, {"protocols": []}):
            with self.subTest(values=values), self.assertRaises(share.AnsibleFilterError):
                self.render(**values)

    def test_expiration_is_utc_and_in_the_future(self):
        self.assertEqual(share.expiry("", "2026-10-15T18:00:00Z"), "2026-10-15T20:00:00Z")
        for value in ("forever", "2026-02-30T18:00:00Z", "2025-01-01T00:00:00Z", "2026-10-16T18:00:00+01:00"):
            with self.subTest(value=value), self.assertRaises(share.AnsibleFilterError):
                share.expiry(value, "2026-10-15T18:00:00Z")

    def test_cleanup_ignores_foreign_objects_and_reports_bad_dates(self):
        objects = self.render()
        foreign = {"metadata": {"name": "foreign"}}
        self.assertFalse(share.expired(foreign, "2027-01-01T00:00:00Z"))
        self.assertFalse(share.selected(objects, "ps-42", "aap-user-8"))
        objects[0]["metadata"]["annotations"][share.EXPIRY] = "bad"
        self.assertFalse(share.expired(objects[0], "2027-01-01T00:00:00Z"))
        self.assertEqual(share.invalid_expiries(objects), ["ocp-ps-42"])

    def test_conflict_is_distinct_from_cleanup_failure(self):
        self.assertTrue(share.conflict({"msg": 'Failed to delete object: {"reason": "Conflict", "code": 409}'}))
        self.assertFalse(share.conflict({"msg": 'Failed to delete object: {"code": 500}'}))

    def test_wrong_namespace_backend_and_occupied_routes_fail(self):
        endpoint = {"metadata": {"namespace": "other"}, "spec": {}}
        with self.assertRaises(share.AnsibleFilterError):
            share.check_backend(endpoint, BACKEND, ["HTTP"])
        objects = self.render()
        gateway = {"spec": {"listeners": [{"name": "https", "protocol": "HTTPS", "port": 443},
                    {"name": "tcp-27010", "protocol": "TCP", "port": 27010},
                    {"name": "udp-27018", "protocol": "UDP", "port": 27018}]}}
        self.assertTrue(share.check_routes(objects, [], gateway))
        with self.assertRaises(share.AnsibleFilterError):
            share.check_routes(objects, objects, gateway)


if __name__ == "__main__":
    unittest.main()
