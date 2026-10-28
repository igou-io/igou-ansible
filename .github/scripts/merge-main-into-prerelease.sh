#!/usr/bin/env bash
# Run in a disposable prerelease checkout. Resolve only the Core pin specially;
# every other merge conflict remains visible for manual resolution.
set -euo pipefail

if [[ "${1:-}" == --merge-driver ]]; then
  base=$2
  ours=$3
  theirs=$4
  requirement=$(awk '
    /^[[:space:]]*(package_pip:[[:space:]]*)?ansible-core==/ {
      print ($1 == "package_pip:" ? $2 : $1); count++
    }
    END { if (count != 1) exit 1 }
  ' "$ours")
  for file in "$base" "$theirs"; do
    temporary=$(mktemp)
    awk -v requirement="$requirement" '
      /^[[:space:]]*(package_pip:[[:space:]]*)?ansible-core==/ {
        sub(/ansible-core==[^[:space:]]+/, requirement)
      }
      { print }
    ' "$file" > "$temporary"
    cat "$temporary" > "$file"
    rm -f "$temporary"
  done
  exec git merge-file "$ours" "$base" "$theirs"
fi

script=$(realpath "${BASH_SOURCE[0]}")
attributes=$(git rev-parse --git-path info/attributes)
mkdir -p "$(dirname "$attributes")"
printf '%s\n' \
  'execution-environments/igou-awx-ee/execution-environment.yml merge=prerelease-core' \
  'requirements-molecule.txt merge=prerelease-core' >> "$attributes"
printf -v driver 'bash %q --merge-driver %%O %%A %%B' "$script"
git -c "merge.prerelease-core.driver=$driver" merge --no-ff \
  -m 'Merge main into prerelease, preserving its Ansible Core pin' "${1:-origin/main}"
