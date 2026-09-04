#!/usr/bin/env bash
# hack/ao-export.sh — export a published Automation Orchestrator workflow
# version to YAML. Invoked by `make ao-export WF=<workflow-id> VER=<version>`.
#
# AO has no config-as-code story yet: workflows are built in the UI and live
# only in its database. This pulls a published version out through the REST
# API so the definition can be reviewed in a diff and committed next to the
# playbooks the workflow dispatches.
#
# Auth is the built-in `admin` account, password from 1Password
# (op://lab_aap/automation-orchestrator-admin-password/password). The password
# is only ever held in a shell variable and handed to curl on stdin — never on
# a command line, never echoed, never written to disk.
#
# Output: the disk-utilization workflow lands next to its playbooks at
# playbooks/linux/disk/orchestrator-workflow.yml; anything else lands at
# ./ao-workflow-<id>-v<ver>.yml in the current directory. YAML is emitted with
# indented sequences and a leading `---` so it passes the repo's yamllint.
#
# Usage:
#   make ao-export WF=9c0c6ed9-d5db-4190-be78-6cdb60480cf9 VER=6
#   AO_API=https://other.example/api/v1 make ao-export WF=... VER=...
set -euo pipefail

AO_API="${AO_API:-https://orchestrator.apps.ocp.igou.systems/api/v1}"
AO_ADMIN_USER="${AO_ADMIN_USER:-admin}"
AO_PW_REF="${AO_PW_REF:-op://lab_aap/automation-orchestrator-admin-password/password}"
# The disk-utilization workflow; its export is versioned alongside the
# playbooks its steps dispatch.
AO_DISK_WF="${AO_DISK_WF:-9c0c6ed9-d5db-4190-be78-6cdb60480cf9}"
AO_DISK_OUT="playbooks/linux/disk/orchestrator-workflow.yml"
# The casval lease workflow; keep its durable export beside casval_scale.
AO_CASVAL_WF="${AO_CASVAL_WF:-1d7fe45e-3f3f-48f4-8663-aeffd7f5ac0a}"
AO_CASVAL_OUT="playbooks/openshift/casval-orchestrator-workflow.yml"

WF="${WF:-}"
VER="${VER:-}"
if [[ -z "${WF}" || -z "${VER}" ]]; then
  echo "usage: make ao-export WF=<workflow-id> VER=<version>" >&2
  exit 2
fi

# op read emits a trailing newline; a newline inside the JSON password would
# fail the login with a bare 401 and no hint why.
pw="$(op read "${AO_PW_REF}")"
pw="${pw%$'\n'}"

token="$(
  AO_USER="${AO_ADMIN_USER}" AO_PW="${pw}" python3 -c \
    'import json, os, sys; sys.stdout.write(json.dumps({"username": os.environ["AO_USER"], "password": os.environ["AO_PW"]}))' |
    curl -sSk -X POST "${AO_API}/auth/login" \
      -H 'Content-Type: application/json' --data-binary @- |
    python3 -c 'import json, sys; sys.stdout.write(json.load(sys.stdin).get("access_token", ""))'
)"
unset pw

if [[ -z "${token}" ]]; then
  echo "ao-export: login to ${AO_API} as ${AO_ADMIN_USER} failed" >&2
  exit 1
fi

if [[ "${WF}" == "${AO_DISK_WF}" ]]; then
  out="${AO_DISK_OUT}"
elif [[ "${WF}" == "${AO_CASVAL_WF}" ]]; then
  out="${AO_CASVAL_OUT}"
else
  out="./ao-workflow-${WF}-v${VER}.yml"
fi

curl -sSkf "${AO_API}/workflows/${WF}/versions/${VER}/export" \
  -H "Authorization: Bearer ${token}" |
  python3 -c '
import json
import sys

import yaml


class IndentedDumper(yaml.SafeDumper):
    """Indent block sequences under their key (repo yamllint: indent-sequences: true)."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


sys.stdout.write("---\n")
yaml.dump(
    json.load(sys.stdin),
    sys.stdout,
    Dumper=IndentedDumper,
    default_flow_style=False,
    sort_keys=False,
    width=4096,
    allow_unicode=True,
)
' >"${out}"

echo "ao-export: wrote ${out} (workflow ${WF} version ${VER})"
