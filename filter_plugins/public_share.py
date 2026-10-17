"""Small data helpers for the AAP public-share playbooks; no network or secrets."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re

from ansible.errors import AnsibleFilterError

MANAGED = "public-share-aap"
ID = "sharing.igou.io/id"
OWNER = "sharing.igou.io/owner"
EXPIRY = "sharing.igou.io/expires-at"
SCOPES = [
    {"api_version": "gateway.networking.k8s.io/v1", "kind": kind, "namespace": "public-services"}
    for kind in ("HTTPRoute", "TCPRoute", "UDPRoute")
] + [
    {"api_version": "v1", "kind": "Service", "namespace": ns}
    for ns in ("public-services", "tailscale")
] + [
    {"api_version": "gateway.networking.k8s.io/v1beta1", "kind": "ReferenceGrant", "namespace": "tailscale"},
    {"api_version": "networking.k8s.io/v1", "kind": "NetworkPolicy", "namespace": "tailscale"},
]


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise AnsibleFilterError("Expiration must be UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise AnsibleFilterError("Invalid expiration date") from exc


def expiry(value, now):
    current = timestamp(now)
    result = timestamp(value) if value else current + timedelta(hours=2)
    if result <= current:
        raise AnsibleFilterError("Expiration must be in the future")
    return result.strftime("%Y-%m-%dT%H:%M:%SZ")


def owned(obj, share_id="", owner=""):
    meta = obj.get("metadata", {})
    labels, annotations = meta.get("labels", {}), meta.get("annotations", {})
    return (labels.get("app.kubernetes.io/managed-by") == MANAGED
            and bool(labels.get(ID)) and bool(annotations.get(OWNER))
            and (not share_id or labels.get(ID) == share_id)
            and (not owner or annotations.get(OWNER) == owner))


def selected(objects, share_id="", owner=""):
    return [o for o in objects if owned(o, share_id, owner)]


def expired(obj, now):
    if not owned(obj):
        return False
    try:
        return timestamp(obj.get("metadata", {}).get("annotations", {}).get(EXPIRY, "")) <= timestamp(now)
    except AnsibleFilterError:
        return False


def invalid_expiries(objects):
    invalid = []
    for obj in objects:
        try:
            timestamp(obj.get("metadata", {}).get("annotations", {}).get(EXPIRY, ""))
        except AnsibleFilterError:
            invalid.append(obj["metadata"]["name"])
    return invalid


def conflict(result):
    # kubernetes.core wraps the API Status body inside CoreException.msg.
    return bool(re.search(r'"code"\s*:\s*409\b', result.get("msg", "")))


def render(backend, share_id, owner, expires, protocols, tcp_port=0, udp_port=0):
    if not re.fullmatch(r"ps-[a-z0-9-]{1,30}", str(share_id)):
        raise AnsibleFilterError("Share ID must be ps- followed by at most 30 lowercase letters/digits/hyphens")
    timestamp(expires)
    if not owner:
        raise AnsibleFilterError("An authenticated owner is required")
    fqdn = backend.get("tailnet_fqdn", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.[a-z0-9-]+\.ts\.net", fqdn):
        raise AnsibleFilterError("Backend must be an approved full MagicDNS name")
    if isinstance(protocols, str):
        protocols = protocols.replace(",", " ").split()
    if not protocols or len(set(protocols)) != len(protocols) or set(protocols) - {"HTTP", "TCP", "UDP"}:
        raise AnsibleFilterError("Select HTTP, TCP and/or UDP")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]|[a-z0-9]", backend.get("service", "")):
        raise AnsibleFilterError("Catalog must identify an OpenShift Service")
    ports = []
    for protocol in protocols:
        port = backend.get("ports", {}).get(protocol)
        if (not isinstance(port, int) or isinstance(port, bool)) or not 1 <= port <= 65535:
            raise AnsibleFilterError(f"Backend does not declare a valid {protocol} port")
        frontend = int(tcp_port if protocol == "TCP" else udp_port if protocol == "UDP" else 443)
        if protocol == "TCP" and frontend not in range(27010, 27018):
            raise AnsibleFilterError("TCP public port must be 27010-27017")
        if protocol == "UDP" and frontend not in range(27018, 27026):
            raise AnsibleFilterError("UDP public port must be 27018-27025")
        ports.append({"name": "http-web" if protocol == "HTTP" else protocol.lower(),
                      "protocol": "UDP" if protocol == "UDP" else "TCP", "port": port,
                      "targetPort": port, "routeProtocol": protocol, "publicPort": frontend})
    pairs = [(p["protocol"], p["port"]) for p in ports]
    if len(set(pairs)) != len(pairs):
        raise AnsibleFilterError("HTTP and TCP backend ports must be distinct")
    name, bridge = "ocp-" + share_id, "ocp-" + share_id + "-bridge"
    selector = {"tailscale.com/managed": "true", "tailscale.com/parent-resource-type": "svc",
                "tailscale.com/parent-resource": name, "tailscale.com/parent-resource-ns": "public-services"}
    objects = []

    def add(kind, resource_name, namespace, spec, version="v1", annotations=None):
        objects.append({"apiVersion": version, "kind": kind, "metadata": {
            "name": resource_name, "namespace": namespace,
            "labels": {"app.kubernetes.io/managed-by": MANAGED, ID: share_id},
            "annotations": {OWNER: str(owner), EXPIRY: expires, **(annotations or {})}}, "spec": spec})

    service_ports = [{k: p[k] for k in ("name", "port", "protocol")} for p in ports]
    add("Service", name, "public-services", {"type": "ExternalName", "externalName": "placeholder",
        "ports": service_ports}, annotations={"tailscale.com/tailnet-fqdn": fqdn,
        "tailscale.com/hostname": "vps-egress-" + share_id, "tailscale.com/tags": "tag:vps-public-service",
        "tailscale.com/proxy-class": "metrics"})
    add("Service", bridge, "tailscale", {"type": "ClusterIP", "selector": selector,
        "ports": [{k: p[k] for k in ("name", "port", "targetPort", "protocol")} for p in ports]})
    add("ReferenceGrant", name, "tailscale", {
        "from": [{"group": "gateway.networking.k8s.io", "kind": p + "Route", "namespace": "public-services"}
                 for p in protocols], "to": [{"group": "", "kind": "Service", "name": bridge}]},
        "gateway.networking.k8s.io/v1beta1")
    add("NetworkPolicy", name, "tailscale", {"podSelector": {"matchLabels": selector},
        "policyTypes": ["Ingress"], "ingress": [{"from": [{"namespaceSelector": {
            "matchLabels": {"kubernetes.io/metadata.name": "nginx-gateway"}}}],
        "ports": [{"port": p["port"], "protocol": p["protocol"]} for p in ports]}]}, "networking.k8s.io/v1")
    for port in ports:
        protocol = port["routeProtocol"]
        listener = "https" if protocol == "HTTP" else f"{protocol.lower()}-{port['publicPort']}"
        rule = {"backendRefs": [{"group": "", "kind": "Service", "name": bridge,
                                "namespace": "tailscale", "port": port["port"], "weight": 1}]}
        spec = {"parentRefs": [{"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": "public",
                                "namespace": "nginx-gateway", "sectionName": listener}], "rules": [rule]}
        if protocol == "HTTP":
            spec["hostnames"] = [share_id + ".igou.io"]
            rule["matches"] = [{"path": {"type": "PathPrefix", "value": "/"}}]
            rule["timeouts"] = {"request": "0s"}
        add(protocol + "Route", share_id + "-" + protocol.lower(), "public-services", spec,
            "gateway.networking.k8s.io/v1")
    return objects


def check_backend(service, backend, protocols):
    if isinstance(protocols, str):
        protocols = protocols.replace(",", " ").split()
    spec = service.get("spec", {})
    if (service.get("metadata", {}).get("namespace") != "public-services"
            or spec.get("type") != "LoadBalancer" or spec.get("loadBalancerClass") != "tailscale"
            or not spec.get("selector")):
        raise AnsibleFilterError("Backend must select application pods in public-services through Tailscale")
    if backend.get("service_uid") and service["metadata"].get("uid") != backend["service_uid"]:
        raise AnsibleFilterError("Catalog Service UID differs from OpenShift")
    expected_hostname = backend['tailnet_fqdn'].split('.')[0]
    if service.get('metadata', {}).get('annotations', {}).get('tailscale.com/hostname') != expected_hostname:
        raise AnsibleFilterError("Catalog MagicDNS hostname does not match the approved OpenShift Service")
    available = {(p["port"], p.get("protocol", "TCP")) for p in spec.get("ports", [])}
    if any((backend["ports"][p], "UDP" if p == "UDP" else "TCP") not in available for p in protocols):
        raise AnsibleFilterError("Requested backend port is not exposed by the OpenShift Service")
    return True


def check_routes(desired, existing, gateway):
    listeners = {l["name"]: l for l in gateway.get("spec", {}).get("listeners", [])}
    for route in [o for o in desired if o["kind"].endswith("Route")]:
        ref = route["spec"]["parentRefs"][0]
        listener = listeners.get(ref["sectionName"], {})
        expected = "HTTPS" if route["kind"] == "HTTPRoute" else route["kind"][:-5]
        if listener.get("protocol") != expected:
            raise AnsibleFilterError("Required public Gateway listener is missing or has the wrong protocol")
        if expected != "HTTPS" and ref["sectionName"] != f"{expected.lower()}-{listener.get('port')}":
            raise AnsibleFilterError("Listener port differs from its approved pool name")
        if expected != "HTTPS" and any(s.get("name") == ref["sectionName"] and s.get("attachedRoutes", 0) > 0
                                        for s in gateway.get("status", {}).get("listeners", [])):
            raise AnsibleFilterError("Gateway reports an occupied TCP/UDP listener")
        for old in existing:
            if old["kind"] != route["kind"]:
                continue
            for parent in old.get("spec", {}).get("parentRefs", []):
                if parent.get("name") != "public" or parent.get("namespace", old["metadata"]["namespace"]) != "nginx-gateway":
                    continue
                same_listener = parent.get("sectionName") in (None, ref["sectionName"])
                overlaps = (expected != "HTTPS" or bool(set(old["spec"].get("hostnames", []))
                                                        & set(route["spec"].get("hostnames", []))))
                if same_listener and overlaps:
                    raise AnsibleFilterError("Public hostname or listener is already in use")
    return True


def available(backends):
    """Describe approved Create inputs; port availability is checked on Create."""
    entries = []
    for name, backend in sorted(backends.items()):
        examples = []
        ports = {p: backend["ports"][p] for p in ("HTTP", "TCP", "UDP")
                 if p in backend.get("ports", {})}
        for protocol in ports:
            inputs = {"public_share_action": "create", "public_share_backend": name,
                      "public_share_protocols": [protocol], "public_share_expires_at": ""}
            if protocol == "TCP":
                inputs["public_share_tcp_port"] = 27010
            elif protocol == "UDP":
                inputs["public_share_udp_port"] = 27018
            examples.append(inputs)
        entries.append({"backend": name, "namespace": "public-services",
                        "service": backend["service"], "backend_ports": ports,
                        "create_examples": examples})
    return entries


def summary(objects):
    shares = {}
    for obj in objects:
        meta = obj['metadata']
        share_id = meta['labels'][ID]
        entry = shares.setdefault(share_id, {'id': share_id, 'owner': meta['annotations'][OWNER],
                                            'expirations': [], 'endpoints': []})
        date = meta['annotations'].get(EXPIRY, '')
        if date not in entry['expirations']:
            entry['expirations'].append(date)
        if obj['kind'] == 'HTTPRoute':
            entry['endpoints'].extend('https://' + h for h in obj['spec'].get('hostnames', []))
        elif obj['kind'] in ('TCPRoute', 'UDPRoute'):
            listener = obj['spec']['parentRefs'][0].get('sectionName', '')
            entry['endpoints'].append(f"{obj['kind'][:-5].lower()}://{share_id}.igou.io:{listener.rsplit('-', 1)[-1]}")
    return list(shares.values())


class FilterModule:
    def filters(self):
        return {"public_share_available": available,
                "public_share_summary": summary, "public_share_invalid_expiries": invalid_expiries, "public_share_conflict": conflict,
                "public_share_expiry": expiry, "public_share_selected": selected,
                "public_share_expired": expired, "public_share_render": render,
                "public_share_check_backend": check_backend, "public_share_check_routes": check_routes,
                "public_share_scopes": lambda _: deepcopy(SCOPES)}
