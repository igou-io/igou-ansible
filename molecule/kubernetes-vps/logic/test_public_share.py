"""Run production Ansible through its EE against an isolated local API, never a cluster."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import unittest

from public_share_api import Fixture

ROOT = Path(__file__).resolve().parents[3]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.api = Fixture()
        self.thread = Thread(target=self.api.serve_forever, daemon=True)
        self.thread.start()
        url = f'http://127.0.0.1:{self.api.server_port}'
        connection = {'apiVersion': 'v1', 'kind': 'Config', 'current-context': 'fixture',
                      'contexts': [{'name': 'fixture', 'context': {'cluster': 'fixture', 'user': 'fixture'}}],
                      'clusters': [{'name': 'fixture', 'cluster': {'server': url}}],
                      'users': [{'name': 'fixture', 'user': {'token': 'fixture-only'}}]}
        self.inputs = {'public_share_vps_kubeconfig_lookup': connection,
                       'public_share_ocp_kubeconfig_lookup': connection, 'awx_user_id': 7, 'awx_job_id': 42,
                       'public_share_backend': 'arena', 'public_share_protocols': ['HTTP', 'TCP', 'UDP'],
                       'public_share_tcp_port': 27010, 'public_share_udp_port': 27018,
                       'public_share_expires_at': '2099-01-01T00:00:00Z',
                       'public_share_backends': {'arena': {'service': 'arena-public',
                           'tailnet_fqdn': 'ocp-public-arena.example.ts.net',
                           'ports': {'HTTP': 8080, 'TCP': 27015, 'UDP': 27016}}}}
        (ROOT / '.ansible').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.ansible')

    def tearDown(self):
        self.api.shutdown()
        self.api.server_close()
        self.thread.join()
        self.temp.cleanup()

    def run_play(self, action, success=True, **overrides):
        inputs = dict(self.inputs, public_share_action=action, **overrides)
        config = Path(self.temp.name) / 'vars.json'
        config.write_text(json.dumps(inputs))
        args = ['ansible-navigator', 'run', 'playbooks/kubernetes/public-share.yaml',
                '--mode', 'stdout', '--execution-environment-image',
                os.environ.get('PUBLIC_SHARE_TEST_EE', 'localhost/igou-awx-ee:latest'),
                '--pull-policy', 'never', '--container-options=--network=host', '-e', '@' + str(config)]
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=180)
        Path(self.temp.name, f'{action}.log').write_text(result.stdout + result.stderr)
        self.assertNotIn('fixture-only', result.stdout + result.stderr)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout[-6000:] + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout[-6000:])
        return result

    def shares(self):
        return [o for o in self.api.objects.values()
                if o['metadata']['labels'].get('app.kubernetes.io/managed-by') == 'public-share-aap']

    def test_create_extend_cleanup_and_owner_boundaries(self):
        self.run_play('create')
        self.assertEqual(len(self.shares()), 7)
        original_specs = {o['metadata']['name'] + o['kind']: copy.deepcopy(o['spec']) for o in self.shares()}
        self.run_play('extend', public_share_id='ps-42', public_share_expires_at='2099-02-01T00:00:00Z')
        for obj in self.shares():
            self.assertEqual(obj['metadata']['annotations']['sharing.igou.io/expires-at'], '2099-02-01T00:00:00Z')
            self.assertEqual(obj['spec'], original_specs[obj['metadata']['name'] + obj['kind']])
        patch_count = sum(e[0] == 'patch' for e in self.api.events)
        self.run_play('extend', public_share_id='ps-42', public_share_expires_at='2099-02-01T00:00:00Z')
        self.assertEqual(sum(e[0] == 'patch' for e in self.api.events), patch_count)
        self.run_play('remove', public_share_id='ps-42', awx_user_id=8)
        self.assertEqual(len(self.shares()), 7)
        self.run_play('cleanup')
        self.assertEqual(len(self.shares()), 7)
        for obj in self.shares():
            obj['metadata']['annotations']['sharing.igou.io/expires-at'] = '2000-01-01T00:00:00Z'
        self.api.renew_on_delete = 'ps-42-http'
        self.run_play('cleanup')
        self.assertEqual([o['metadata']['name'] for o in self.shares()], ['ps-42-http'])
        self.assertTrue(any(e[0] == 'conflict' for e in self.api.events))
        self.assertEqual(len(self.api.objects), 3)  # backend and Gateway survive.
        self.run_play('remove', public_share_id='ps-42')
        self.assertEqual(self.shares(), [])
        self.run_play('cleanup')

    def test_bad_dates_and_partial_failure_do_not_stop_other_cleanup(self):
        self.run_play('create')
        objects = self.shares()
        for obj in objects:
            obj['metadata']['annotations']['sharing.igou.io/expires-at'] = '2000-01-01T00:00:00Z'
        objects[0]['metadata']['annotations']['sharing.igou.io/expires-at'] = 'malformed'
        self.api.fail_delete = 'ps-42-http'
        self.run_play('cleanup', success=False)
        self.assertEqual({o['metadata']['name'] for o in self.shares()}, {'ocp-ps-42', 'ps-42-http'})
        self.api.fail_delete = None
        objects[0]['metadata']['annotations']['sharing.igou.io/expires-at'] = '2000-01-01T00:00:00Z'
        self.run_play('cleanup')
        self.assertEqual(self.shares(), [])


if __name__ == '__main__':
    unittest.main()
