"""Disposable local Kubernetes API fixture for the real public-share playbooks."""
import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from uuid import uuid4

import jsonpatch

GROUPS = {
    'v1': {'services': 'Service'},
    'gateway.networking.k8s.io/v1': {'gateways': 'Gateway', 'httproutes': 'HTTPRoute',
                                   'tcproutes': 'TCPRoute', 'udproutes': 'UDPRoute'},
    'gateway.networking.k8s.io/v1beta1': {'referencegrants': 'ReferenceGrant'},
    'networking.k8s.io/v1': {'networkpolicies': 'NetworkPolicy'},
}


class Fixture(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(('127.0.0.1', 0), API)
        self.objects = {}
        self.events = []
        self.renew_on_delete = None
        self.fail_delete = None
        self.put({'apiVersion': 'v1', 'kind': 'Service', 'metadata': {
            'name': 'arena-public', 'namespace': 'public-services',
            'annotations': {'tailscale.com/hostname': 'ocp-public-arena'}}, 'spec': {
            'type': 'LoadBalancer', 'loadBalancerClass': 'tailscale', 'selector': {'app': 'arena'},
            'ports': [{'port': 8080}, {'port': 27015}, {'port': 27016, 'protocol': 'UDP'}]}})
        listeners = [{'name': 'https', 'port': 443, 'protocol': 'HTTPS'}]
        listeners += [{'name': f'{p.lower()}-{n}', 'port': n, 'protocol': p}
                      for p, n in [('TCP', 27010), ('UDP', 27018)]]
        self.put({'apiVersion': 'gateway.networking.k8s.io/v1', 'kind': 'Gateway',
                  'metadata': {'name': 'public', 'namespace': 'nginx-gateway'}, 'spec': {'listeners': listeners}})

    def put(self, obj):
        obj = copy.deepcopy(obj)
        meta = obj['metadata']
        meta.setdefault('uid', str(uuid4()))
        meta.setdefault('resourceVersion', '1')
        meta.setdefault('labels', {})
        meta.setdefault('annotations', {})
        self.objects[(obj['kind'], meta['namespace'], meta['name'])] = obj
        return obj


class API(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, obj, code=200):
        content = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def error(self, code, reason):
        return self.reply({'apiVersion': 'v1', 'kind': 'Status', 'status': 'Failure',
                           'reason': reason, 'message': reason, 'code': code}, code)

    def handle_request(self):
        path = urlparse(self.path).path.rstrip('/')
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or '{}')
        if path == '/version':
            return self.reply({'major': '1', 'minor': '36', 'gitVersion': 'v1.36.0'})
        if path == '/api':
            return self.reply({'kind': 'APIVersions', 'apiVersion': 'v1', 'versions': ['v1']})
        if path == '/apis':
            groups = {}
            for api in GROUPS:
                if '/' in api:
                    group, version = api.split('/')
                    entry = groups.setdefault(group, {'name': group, 'versions': [],
                        'preferredVersion': {'groupVersion': api, 'version': version}})
                    entry['versions'].append({'groupVersion': api, 'version': version})
            return self.reply({'kind': 'APIGroupList', 'apiVersion': 'v1', 'groups': list(groups.values())})
        api = next((g for g in GROUPS if path == ('/api/' if g == 'v1' else '/apis/') + g
                    or path.startswith(('/api/' if g == 'v1' else '/apis/') + g + '/')), None)
        if api is None:
            return self.error(404, 'NotFound')
        prefix = ('/api/' if api == 'v1' else '/apis/') + api
        if path == prefix:
            return self.reply({'kind': 'APIResourceList', 'groupVersion': api, 'resources': [
                {'name': plural, 'kind': kind, 'namespaced': True,
                 'verbs': ['get', 'list', 'create', 'patch', 'delete']}
                for plural, kind in GROUPS[api].items()]})
        parts = path[len(prefix):].strip('/').split('/')
        if len(parts) < 3 or parts[0] != 'namespaces':
            return self.error(403, 'NamespaceRequired')
        namespace, plural = parts[1:3]
        kind = GROUPS[api][plural]
        name = parts[3] if len(parts) > 3 else body.get('metadata', {}).get('name')
        key = kind, namespace, name
        current = self.server.objects.get(key)
        if self.command == 'GET':
            if name:
                return self.reply(current) if current else self.error(404, 'NotFound')
            return self.reply({'apiVersion': api, 'kind': kind + 'List', 'metadata': {'resourceVersion': '1'},
                'items': [copy.deepcopy(o) for (k, ns, _), o in self.server.objects.items()
                          if k == kind and ns == namespace]})
        if self.command == 'POST':
            if current:
                return self.error(409, 'AlreadyExists')
            self.server.events.append(('create', key))
            return self.reply(self.server.put(body), 201)
        if self.command == 'PATCH':
            if not current:
                return self.error(404, 'NotFound')
            try:
                updated = jsonpatch.apply_patch(copy.deepcopy(current), body)
            except jsonpatch.JsonPatchException:
                return self.error(409, 'Conflict')
            updated['metadata']['resourceVersion'] = str(int(current['metadata']['resourceVersion']) + 1)
            self.server.events.append(('patch', key))
            return self.reply(self.server.put(updated))
        if self.command == 'DELETE':
            if not current:
                return self.error(404, 'NotFound')
            if self.server.renew_on_delete == name:
                current['metadata']['annotations']['sharing.igou.io/expires-at'] = '2099-01-01T00:00:00Z'
                current['metadata']['resourceVersion'] = str(int(current['metadata']['resourceVersion']) + 1)
                self.server.renew_on_delete = None
            if self.server.fail_delete == name:
                return self.error(500, 'FixtureFailure')
            expected = body.get('preconditions', {})
            if any(expected.get(k) != current['metadata'][k] for k in ('uid', 'resourceVersion')):
                self.server.events.append(('conflict', key))
                return self.error(409, 'Conflict')
            self.server.events.append(('delete', key))
            del self.server.objects[key]
            return self.reply({'apiVersion': 'v1', 'kind': 'Status', 'status': 'Success', 'code': 200})
        return self.error(405, 'MethodNotAllowed')

    do_GET = handle_request
    do_POST = handle_request
    do_PATCH = handle_request
    do_DELETE = handle_request
