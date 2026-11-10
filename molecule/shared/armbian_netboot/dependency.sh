#!/usr/bin/env bash
set -euo pipefail
repository_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
export ANSIBLE_COLLECTIONS_PATH="${repository_root}/.ansible/collections"
bash "${repository_root}/molecule/dependency.sh"

# Override the requirements pin only when testing a collection checkout.
if [[ -n ${ARMBIAN_COLLECTION_SOURCE:-} ]]; then
  collection_root=$(cd -- "$ARMBIAN_COLLECTION_SOURCE" && pwd)
  test -f "${collection_root}/galaxy.yml"
  artifact_directory="${MOLECULE_EPHEMERAL_DIRECTORY:?}/artifacts"
  mkdir -p "$artifact_directory"
  rm -f "${artifact_directory}"/david_igou-armbian-*.tar.gz
  ansible-galaxy collection build "$collection_root" --output-path "$artifact_directory" --force
  ansible-galaxy collection install "${artifact_directory}"/david_igou-armbian-*.tar.gz --force --no-deps
fi
