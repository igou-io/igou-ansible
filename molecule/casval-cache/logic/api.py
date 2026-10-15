"""Local Kubernetes/CDI simulator for exercising the production cache tasks.

No cluster credentials or external services are used. The real kubernetes.core
modules perform discovery and CRUD; this fixture models completed/failed CDI
copies, dependent deletion, and legacy CDI cleanup-label semantics.
"""
import copy
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4

import yaml

NS = 'openshift-virtualization-os-images'
GROUPS = {
    'v1': {'configmaps': 'ConfigMap', 'nodes': 'Node', 'persistentvolumeclaims': 'PersistentVolumeClaim'},
    'cdi.kubevirt.io/v1beta1': {'dataimportcrons': 'DataImportCron', 'datasources': 'DataSource', 'datavolumes': 'DataVolume'},
    'lvm.topolvm.io/v1alpha1': {'lvmclusters': 'LVMCluster'},
    'snapshot.storage.k8s.io/v1': {'volumesnapshots': 'VolumeSnapshot'},
}
OBJECTS = {}
EVENTS = []
FAIL = False
CLOCK = '2026-01-03T00:00:00Z'


def put(kind, name, spec=None, status=None, labels=None, namespace=NS, created=None, **extra):
    api = next(api for api, resources in GROUPS.items() if kind in resources.values())
    obj = dict(apiVersion=api, kind=kind, metadata=dict(name=name, namespace=namespace,
               uid=str(uuid4()), resourceVersion='1', creationTimestamp=created or CLOCK,
               labels=labels or {}, annotations={}), spec=spec or {}, **extra)
    if status is not None:
        obj['status'] = status
    OBJECTS[(kind, namespace, name)] = obj
    return obj


def reset(config):
    global CLOCK, FAIL
    OBJECTS.clear()
    EVENTS.clear()
    FAIL = False
    CLOCK = '2026-01-03T00:00:00Z'
    put('ConfigMap', 'casval-image-cache', data=config['data'])
    node = put('Node', 'casval.igou.systems', namespace=None, created='2026-01-02T00:00:00Z')
    node['metadata']['annotations']['molecule.igou.systems/cache-node-uid'] = node['metadata']['uid']
    put('LVMCluster', 'lvmcluster', namespace='openshift-storage', status={
        'deviceClassStatuses': [{'nodeStatus': [{'node': 'casval.igou.systems', 'status': 'Ready'}]}]})
    for definition in yaml.safe_load(config['data']['seeds.yml']):
        seed, source = definition['name'], definition['source']
        put('PersistentVolumeClaim', source, spec={
            'storageClassName': 'shared', 'volumeMode': 'Block', 'accessModes': ['ReadWriteOnce']},
            status={'phase': 'Bound'})
        ready = {'conditions': [{'type': 'Ready', 'status': 'True'}]}
        put('DataSource', source, spec={'source': {'pvc': {'name': source, 'namespace': NS}}}, status=ready)
        labels = {'cdi.kubevirt.io/dataImportCron': seed, 'cdi.kubevirt.io/dataImportCron.cleanup': 'true'}
        put('DataImportCron', seed, spec={'schedule': '13 * * * *', 'retentionPolicy': 'None',
            'template': {'spec': {'storage': {'storageClassName': 'lvms-casval'}}}},
            labels={'molecule.igou.systems/image-cache': 'casval'})
        put('DataSource', seed, spec={'source': {'pvc': {'name': seed + '-legacy', 'namespace': NS}}},
            status=ready, labels=labels.copy())
        put('DataVolume', seed + '-legacy', spec={'storage': {'storageClassName': 'lvms-casval'}},
            status={'phase': 'Succeeded'}, labels=labels.copy())
        put('PersistentVolumeClaim', seed + '-legacy', spec={'storageClassName': 'lvms-casval',
            'volumeMode': 'Block', 'accessModes': ['ReadWriteOnce']}, status={'phase': 'Bound'}, labels=labels.copy())
    seed = 'centos-stream10-casval'
    labels = {'cdi.kubevirt.io/dataImportCron': seed, 'cdi.kubevirt.io/dataImportCron.cleanup': 'true'}
    put('DataVolume', seed + '-pending', spec={'storage': {'storageClassName': 'lvms-casval'}}, labels=labels)
    put('PersistentVolumeClaim', seed + '-pending', spec={'storageClassName': 'lvms-casval'},
        status={'phase': 'Pending'}, labels=labels.copy())


def merge(target, patch):
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict):
            merge(target.setdefault(key, {}), value)
        else:
            target[key] = value


def delete(key):
    obj = OBJECTS.pop(key, None)
    if not obj:
        return
    EVENTS.append({'action': 'delete', 'kind': obj['kind'], 'name': obj['metadata']['name']})
    if obj['kind'] == 'DataImportCron':
        protected = [o for o in OBJECTS.values() if o['metadata']['name'].startswith(obj['metadata']['name'])]
        EVENTS.append({'action': 'retired', 'protected': len(protected)})
        # CDI selects the persisted cleanup label, not the cron retentionPolicy.
        for other_key, other in list(OBJECTS.items()):
            labels = other['metadata']['labels']
            if labels.get('cdi.kubevirt.io/dataImportCron') == key[2] and labels.get('cdi.kubevirt.io/dataImportCron.cleanup') == 'true':
                EVENTS.append({'action': 'legacy_cleanup', 'name': other['metadata']['name']})
                delete(other_key)
    for other_key, other in list(OBJECTS.items()):
        if any(ref['uid'] == obj['metadata']['uid'] for ref in other['metadata'].get('ownerReferences', [])):
            delete(other_key)


class API(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, data, code=200):
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def handle_request(self):
        global FAIL, CLOCK
        from urllib.parse import parse_qs, urlparse
        url = urlparse(self.path)
        path = url.path.rstrip('/')
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or '{}')
        if path == '/_stop':
            self.reply({'stopped': True})
            raise SystemExit
        if path == '/_test':
            if body.get('reset'):
                reset(CONFIG)
            if 'fail' in body:
                FAIL = body['fail']
            if body.get('revision'):
                OBJECTS[('PersistentVolumeClaim', NS, 'centos-stream10')]['metadata']['uid'] = str(uuid4())
            if body.get('reprovision'):
                CLOCK = '2026-01-05T00:00:00Z'
                node = OBJECTS[('Node', None, 'casval.igou.systems')]
                node['metadata'].update(uid=str(uuid4()), creationTimestamp='2026-01-04T00:00:00Z')
            if body.get('absent'):
                delete(('ConfigMap', NS, 'casval-image-cache'))
            if body.get('offline'):
                delete(('Node', None, 'casval.igou.systems'))
                delete(('LVMCluster', 'openshift-storage', 'lvmcluster'))
            if body.get('unsafe'):
                put('PersistentVolumeClaim', 'foreign', spec={'storageClassName': 'shared'}, status={'phase': 'Pending'},
                    labels={'molecule.igou.systems/image-cache': 'casval', 'molecule.igou.systems/seed': 'centos-stream10-casval'})
            if body.get('checkpoint'):
                EVENTS.clear()
            return self.reply({'objects': list(OBJECTS.values()), 'events': EVENTS})
        if path == '/version':
            return self.reply({'major': '1', 'minor': '35', 'gitVersion': 'v1.35.0'})
        if path == '/api':
            return self.reply({'kind': 'APIVersions', 'apiVersion': 'v1', 'versions': ['v1']})
        if path == '/apis':
            groups = [{'name': api.split('/')[0], 'versions': [{'groupVersion': api, 'version': api.split('/')[1]}],
                       'preferredVersion': {'groupVersion': api, 'version': api.split('/')[1]}} for api in GROUPS if '/' in api]
            return self.reply({'kind': 'APIGroupList', 'apiVersion': 'v1', 'groups': groups})
        api = next((api for api in GROUPS if path.startswith(('/api/' if api == 'v1' else '/apis/') + api)), None)
        if not api:
            return self.reply({'kind': 'Status', 'reason': 'NotFound', 'code': 404}, 404)
        prefix = ('/api/' if api == 'v1' else '/apis/') + api
        if path == prefix:
            return self.reply({'kind': 'APIResourceList', 'groupVersion': api, 'resources': [
                {'name': plural, 'kind': kind, 'namespaced': kind != 'Node',
                 'verbs': ['get', 'list', 'create', 'update', 'patch', 'delete']} for plural, kind in GROUPS[api].items()]})
        parts = path[len(prefix):].strip('/').split('/')
        namespace = parts[1] if parts[0] == 'namespaces' else None
        parts = parts[2:] if namespace else parts
        kind = GROUPS[api][parts[0]]
        name = parts[1] if len(parts) > 1 else body.get('metadata', {}).get('name')
        key = (kind, namespace, name)
        current = OBJECTS.get(key)
        if self.command == 'GET' and name:
            if current:
                return self.reply(current)
            return self.reply({'kind': 'Status', 'reason': 'NotFound', 'code': 404}, 404)
        if self.command == 'GET':
            selector = parse_qs(url.query).get('labelSelector', [''])[0]
            terms = re.split(r',\s*(?![^()]*\))', selector) if selector else []
            def matches(obj):
                labels = obj['metadata']['labels']
                for term in terms:
                    if ' in ' in term:
                        label, values = term.split(' in ', 1)
                        if labels.get(label) not in values.strip('()').split(','):
                            return False
                    elif '=' in term:
                        label, value = term.split('=', 1)
                        if labels.get(label) != value:
                            return False
                return True
            return self.reply({'apiVersion': api, 'kind': kind + 'List', 'metadata': {'resourceVersion': '1'},
                               'items': [o for (k, ns, _), o in OBJECTS.items() if k == kind and ns == namespace and matches(o)]})
        if self.command == 'DELETE':
            expected = body.get('preconditions', {}).get('uid')
            if current and expected and current['metadata']['uid'] != expected:
                return self.reply({'kind': 'Status', 'reason': 'Conflict', 'code': 409}, 409)
            delete(key)
            return self.reply({'kind': 'Status', 'status': 'Success', 'code': 200})
        old = copy.deepcopy(current)
        if current is not None and kind == 'DataImportCron' and 'spec' in body and body['spec'] != current['spec']:
            return self.reply({'kind': 'Status', 'status': 'Failure', 'reason': 'Invalid',
                               'message': 'Cannot update DataImportCron Spec', 'code': 422}, 422)
        if current is None:
            current = put(kind, name, namespace=namespace)
        merge(current, body)
        if kind == 'DataVolume' and old is None:
            failed = FAIL and name.startswith('centos-stream10-casval-')
            current['status'] = {'phase': 'Failed' if failed else 'Succeeded'}
            pvc = put('PersistentVolumeClaim', name, spec=current['spec']['storage'], status={'phase': 'Pending' if failed else 'Bound'})
            pvc['metadata']['ownerReferences'] = [{'uid': current['metadata']['uid']}]
            EVENTS.append({'action': 'copy', 'name': name, 'failed': failed})
        if kind == 'DataSource':
            pvc_name = current['spec']['source']['pvc']['name']
            assert OBJECTS[('PersistentVolumeClaim', namespace, pvc_name)]['status']['phase'] == 'Bound', 'Published incomplete seed'
            current['status'] = {'conditions': [{'type': 'Ready', 'status': 'True'}]}
        if old != current:
            EVENTS.append({'action': 'write', 'kind': kind, 'name': name})
        return self.reply(current, 201 if self.command == 'POST' else 200)

    do_GET = handle_request
    do_POST = handle_request
    do_PATCH = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request


if __name__ == '__main__':
    CONFIG = yaml.safe_load(Path(sys.argv[2]).read_text())
    reset(CONFIG)
    HTTPServer(('127.0.0.1', int(sys.argv[1])), API).serve_forever()
