#!/usr/bin/env bash
set -euo pipefail

repository_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export ANSIBLE_COLLECTIONS_PATH="${repository_root}/.ansible/collections"
export ANSIBLE_ROLES_PATH="${repository_root}/.ansible/roles"

ansible-galaxy role install --no-deps --role-file "${repository_root}/requirements.yml"
# Install-only exception for prometheus.prometheus's Core 2.21 cap.
ANSIBLE_COLLECTIONS_ON_ANSIBLE_VERSION_MISMATCH=ignore \
  ansible-galaxy collection install --no-deps --requirements-file "${repository_root}/requirements.yml"
