#!/usr/bin/env bash
set -euo pipefail

repository_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export ANSIBLE_COLLECTIONS_PATH="${repository_root}/.ansible/collections"
export ANSIBLE_ROLES_PATH="${repository_root}/.ansible/roles"

ansible-galaxy role install --no-deps --role-file "${repository_root}/requirements.yml"
ansible-galaxy collection install --no-deps --requirements-file "${repository_root}/requirements.yml"
