"""Retry Windows' transient registry-hive fault before a task can execute."""

import time

from ansible import constants as C
from ansible.errors import AnsibleConnectionFailure
from ansible.plugins.loader import connection_loader
from ansible.utils.display import Display
from pypsrp.exceptions import WSManFaultError

# Resolve the qualified builtin to avoid recursively loading this local psrp.
BuiltinPSRPConnection = connection_loader.get("ansible.builtin.psrp", class_only=True)
DOCUMENTATION = """
name: psrp
short_description: Retry transient Windows session-launch fault 1018 in Molecule
description:
  - Wraps P(ansible.builtin.psrp#connection), accepting all its connection options.
  - Retries only WSMan fault 1018 before session creation completes.
  - Uses a 30-second retry window; individual attempts use normal PSRP timeouts.
author: igou-io
"""
display = Display()


class Connection(BuiltinPSRPConnection):
    """Keep builtin PSRP behavior except for session-open WSMan fault 1018."""

    def __init__(self, *args, **kwargs):
        # Reuse the pinned builtin's option schema, including credential vars.
        C.config.initialize_plugin_configuration_definitions(
            "connection", self._load_name,
            C.config.get_configuration_definitions("connection", BuiltinPSRPConnection._load_name),
        )
        super().__init__(*args, **kwargs)

    def _connect(self):
        deadline = time.monotonic() + 30
        delay = 2
        while True:
            try:
                return super()._connect()
            except AnsibleConnectionFailure as error:
                fault = error.__context__
                if not isinstance(fault, WSManFaultError) or fault.code != 1018:
                    raise

                # Release any partially created shell and its HTTP client,
                # then create a fresh session on the next attempt.
                failed_pool = self.runspace
                self.runspace = None
                self._connected = False
                if failed_pool is not None:
                    try:
                        failed_pool.close()
                    finally:
                        failed_pool.connection.close()

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                pause = min(delay, remaining)
                display.warning(
                    f"Windows refused a new PSRP session (WSMan 1018); retrying in {pause:g} seconds."
                )
                time.sleep(pause)
                if time.monotonic() >= deadline:
                    raise
                delay = min(delay * 2, 8)
