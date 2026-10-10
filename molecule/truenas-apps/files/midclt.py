#!/usr/bin/env python3
"""Stateful stub of the TrueNAS `midclt` client for molecule.

Supports exactly the calls playbooks/truenas/configure_docker_containers.yml
makes, and records evidence for verify.yml under /var/lib/molecule-midclt/:

  calls.log                     one JSON line per invocation (argv)
  apps.json                     apps "registered" by app.create, replayed by
                                app.query — makes a second converge idempotent
  app.create.<name>.files.json  sorted relative listing of every file present
                                in the app's dataset directory AT THE MOMENT
                                app.create ran — the #330 ordering evidence

Real TrueNAS deploys/starts the compose stack during app.create; Docker
auto-creates missing bind-mount sources as directories. The snapshot lets
verify assert every mounted file already existed instead."""

import json
import os
import pathlib
import sys

STATE = pathlib.Path("/var/lib/molecule-midclt")


def dataset_listing(dataset_dir):
    if not os.path.isdir(dataset_dir):
        return []
    return sorted(
        os.path.relpath(os.path.join(root, name), dataset_dir)
        for root, _dirs, files in os.walk(dataset_dir)
        for name in files
    )


def main(argv):
    if len(argv) < 2 or argv[0] != "call":
        sys.stderr.write("midclt stub: unsupported invocation %r\n" % (argv,))
        return 2
    method = argv[1]

    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "calls.log").open("a") as log:
        log.write(json.dumps(argv) + "\n")

    apps_file = STATE / "apps.json"
    apps = json.loads(apps_file.read_text()) if apps_file.exists() else []

    if method == "app.query":
        print(json.dumps(apps))
        return 0

    if method == "app.create":
        payload = json.loads(argv[2])
        name = payload["app_name"]
        include = payload["custom_compose_config"]["include"][0]
        snapshot = dataset_listing(os.path.dirname(include))
        (STATE / ("app.create.%s.files.json" % name)).write_text(
            json.dumps(snapshot)
        )
        if name not in [app["name"] for app in apps]:
            apps.append({"name": name})
        apps_file.write_text(json.dumps(apps))
        print(json.dumps({"name": name}))
        return 0

    if method == "app.redeploy":
        print("null")
        return 0

    sys.stderr.write("midclt stub: unsupported method %s\n" % method)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
