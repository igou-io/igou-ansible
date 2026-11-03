"""Exercise fault recovery through the real builtin PSRP connection path."""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ansible.errors import AnsibleConnectionFailure
from ansible.playbook.play_context import PlayContext
from ansible.plugins.loader import connection_loader, init_plugin_loader
from pypsrp.exceptions import AuthenticationError, WSManFaultError

init_plugin_loader()
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "connection_plugins"
connection_loader.add_directory(str(PLUGIN_DIR))
Connection = connection_loader.get("psrp", class_only=True)
plugin = sys.modules[Connection.__module__]
builtin = sys.modules[plugin.BuiltinPSRPConnection.__module__]


def fault(code=1018):
    return WSManFaultError(code, "windows.invalid", "test fault", None, None, None)


class SessionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.connection = connection_loader.get("psrp", PlayContext(), None)
        self.connection.set_options(var_options={
            "ansible_host": "windows.invalid",
            "ansible_user": "test-user",
            "ansible_psrp_auth": "ntlm",
        })
        self.now = 0
        self.pools = []
        self.outcomes = []
        self.addCleanup(patch.stopall)
        patch.object(plugin.time, "monotonic", side_effect=lambda: self.now).start()
        self.sleep = patch.object(plugin.time, "sleep", side_effect=self.advance).start()
        self.warning = patch.object(plugin.display, "warning").start()
        self.clients = patch.object(builtin, "WSMan", side_effect=Mock).start()
        self.pool_factory = patch.object(builtin, "RunspacePool", side_effect=self.new_pool).start()

    def advance(self, seconds):
        self.now += seconds

    def new_pool(self, client, **kwargs):
        pool = Mock(connection=client)
        pool.open.side_effect = self.outcomes.pop(0) if self.outcomes else None
        self.pools.append(pool)
        return pool

    def test_molecule_override_preserves_builtin_and_options(self):
        self.assertEqual(Path(connection_loader.find_plugin("psrp")).parent, PLUGIN_DIR)
        original = connection_loader.get("ansible.builtin.psrp", class_only=True)
        self.assertIsNot(Connection, original)
        self.assertTrue(issubclass(Connection, original))
        self.assertEqual(self.connection.get_option("auth"), "ntlm")
        self.assertEqual(self.connection.get_option("remote_addr"), "windows.invalid")

    def test_healthy_session_has_no_retry_or_delay(self):
        self.assertIs(self.connection._connect(), self.connection)
        self.assertEqual(len(self.pools), 1)
        self.sleep.assert_not_called()
        self.warning.assert_not_called()
        self.pools[0].connection.close.assert_not_called()

    def test_1018_discards_failed_clients_and_opens_a_fresh_session(self):
        self.outcomes = [fault(), fault(), None]
        self.assertIs(self.connection._connect(), self.connection)
        self.assertEqual(len(self.pools), 3)
        self.assertIs(self.connection.runspace, self.pools[-1])
        for pool in self.pools[:-1]:
            pool.connection.close.assert_called_once()
        self.pools[-1].connection.close.assert_not_called()
        self.assertGreater(self.now, 0)
        self.assertLess(self.now, 30)

    def test_persistent_1018_stops_at_deadline_and_preserves_failure(self):
        self.outcomes = [fault()] * 20
        with self.assertRaises(AnsibleConnectionFailure) as caught:
            self.connection._connect()
        self.assertEqual(caught.exception.__context__.code, 1018)
        self.assertEqual(self.now, 30)
        self.assertIsNone(self.connection.runspace)
        self.assertFalse(self.connection._connected)
        for pool in self.pools:
            pool.connection.close.assert_called_once()

    def test_slow_failed_open_does_not_start_another_attempt_after_deadline(self):
        def slow_fault():
            self.advance(31)
            raise fault()

        self.outcomes = [slow_fault]
        with self.assertRaises(AnsibleConnectionFailure):
            self.connection._connect()
        self.assertEqual(len(self.pools), 1)
        self.sleep.assert_not_called()
        self.pools[0].connection.close.assert_called_once()

    def test_authentication_and_other_faults_are_not_retried(self):
        for error in (AuthenticationError("test authentication failure"), fault(5),
                      AnsibleConnectionFailure("unrelated connection failure")):
            with self.subTest(error=type(error).__name__, message=str(error)):
                self.connection.runspace = None
                self.outcomes = [error]
                before = len(self.pools)
                with self.assertRaises(AnsibleConnectionFailure):
                    self.connection._connect()
                self.assertEqual(len(self.pools), before + 1)
        self.sleep.assert_not_called()
        self.warning.assert_not_called()

    def test_failure_after_session_creation_does_not_replay_command(self):
        self.connection._connect()
        error = fault()
        with patch.object(self.connection, "_exec_psrp_script", side_effect=error) as execute:
            with self.assertRaises(WSManFaultError) as caught:
                self.connection.exec_command("Write-Output 'test'")
        self.assertIs(caught.exception, error)
        execute.assert_called_once()
        self.assertEqual(len(self.pools), 1)
        self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
