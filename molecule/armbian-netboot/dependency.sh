#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
collection_root=${ARMBIAN_COLLECTION_SOURCE:?Set ARMBIAN_COLLECTION_SOURCE to the collection checkout with lab_netboot}
test -f "${collection_root}/extensions/molecule/lab_netboot/create.yml"
bash "${project_root}/molecule/dependency.sh"
artifact_directory="${MOLECULE_EPHEMERAL_DIRECTORY:?}/artifacts"
mkdir -p "$artifact_directory"
rm -f "${artifact_directory}"/david_igou-armbian-*.tar.gz
ansible-galaxy collection build "$collection_root" --output-path "$artifact_directory" --force
ANSIBLE_COLLECTIONS_PATH="${project_root}/.ansible/collections" \
  ansible-galaxy collection install "${artifact_directory}"/david_igou-armbian-*.tar.gz --force --no-deps
