#!/usr/bin/env python3
"""Validate the ghapp broker from a terminal container dispatched by the Hermes
platform's OWN terminal backend (tools.terminal_tool._create_environment +
DockerEnvironment.execute) — the exact code path the agent uses to run a shell
command. Reads the real `terminal:` config from the Hermes config (as written
by configure.yml), so it exercises the production container wiring: the broker
socket mount + GHAPP_BROKER_SOCKET. Mints a token via the broker from inside
that container and asserts a real installation token comes back.

Exit 0 on success, non-zero otherwise. Run as the hermes user with
HERMES_DOCKER_BINARY and XDG_RUNTIME_DIR set, from a hermes-accessible cwd.
"""
import json
import os
import sys

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
AGENT_SRC = os.path.join(HERMES_HOME, "hermes-agent")
sys.path.insert(0, AGENT_SRC)

import yaml  # noqa: E402
from tools.terminal_tool import _create_environment  # noqa: E402

REPO = os.environ.get("GHAPP_TEST_REPO", "igou-io/igou-infrastructure")


def load_terminal_config():
    cfg_path = os.path.join(HERMES_HOME, "config.yaml")
    with open(cfg_path) as fh:
        cfg = yaml.safe_load(fh) or {}
    term = cfg.get("terminal") or {}
    if term.get("backend") != "docker":
        raise SystemExit(f"terminal.backend is {term.get('backend')!r}, expected 'docker'")
    return term


def main():
    term = load_terminal_config()
    image = term["docker_image"]
    # container_config keys consumed by _create_environment, taken verbatim from
    # the production terminal config so the broker wiring is what configure.yml set.
    cc = {
        k: term[k]
        for k in (
            "container_cpu", "container_memory", "container_disk", "container_persistent",
            "docker_volumes", "docker_forward_env", "docker_env", "docker_extra_args",
        )
        if k in term
    }
    env_socket = (term.get("docker_env") or {}).get("GHAPP_BROKER_SOCKET")
    if not env_socket:
        raise SystemExit("terminal.docker_env.GHAPP_BROKER_SOCKET is not set — broker not wired")

    env = _create_environment(
        "docker", image, term.get("cwd", "/workspace"),
        int(term.get("timeout", 180)), container_config=cc,
    )
    mint = (
        'curl -s -o /tmp/tok -w "http=%{http_code}" '
        '--unix-socket "$GHAPP_BROKER_SOCKET" -X POST http://broker/token '
        '-H "Content-Type: application/json" '
        + "-d '" + json.dumps({"repo": REPO, "permissions": {"contents": "read"}}) + "'; "
        'echo; cat /tmp/tok'
    )
    try:
        result = env.execute(mint)
        out = result.get("output", "") if isinstance(result, dict) else str(result)
    finally:
        try:
            env.cleanup(force_remove=True)
        except Exception:
            pass

    print("--- dispatched-container output ---")
    print(out)
    if "http=200" not in out:
        raise SystemExit("broker mint from the Hermes-dispatched container did NOT return http=200")
    try:
        tok = json.loads(out.split("http=200", 1)[1].strip()).get("token", "")
    except Exception:
        tok = ""
    if not tok.startswith("gh"):
        raise SystemExit("broker response had no installation token")
    print(f"OK: Hermes-dispatched container minted a real token ({tok[:8]}...) for {REPO}")


if __name__ == "__main__":
    main()
