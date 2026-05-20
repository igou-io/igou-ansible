#!/usr/bin/env bash
# hack/pac-sim.sh — fake a Pipelines-as-Code push trigger from this checkout.
#
# Reads .tekton/ from the worktree, resolves it with tkn-pac, inlines the
# Repository CR's spec.params (both literal and secret_ref) the way PaC would
# at trigger time, and creates the PipelineRun in the tenant namespace.
#
# The rendered git-clone pulls from --url, which defaults to the Repository's
# spec.url (the Forgejo mirror). So the build runs against whatever the
# mirror has — not your dirty worktree — while the pipeline DEFINITION is
# the one on disk. That makes the script the right tool both for "I pushed
# to GitHub, kick off CI before the next mirror cron" and for iterating on
# .tekton/ YAML without committing.
#
# Useful when the upstream mirror sync doesn't fire push webhooks (Forgejo
# mirrors don't, by default).
#
# Usage:
#   hack/pac-sim.sh [-n NS] [-r REF] [-u URL] [-s]
#     -n  tenant namespace               (default: ci-igou-ansible)
#     -r  revision/branch to build       (default: main)
#     -u  override repo_url passed to git-clone
#     -s  POST mirror-sync to Forgejo before creating the PipelineRun

set -euo pipefail

NS=ci-igou-ansible
REF=main
URL=""
SYNC=0

usage() { sed -n '2,/^$/p' "$0"; exit "${1:-0}"; }

while getopts n:r:u:sh opt; do
  case $opt in
    n) NS=$OPTARG ;;
    r) REF=$OPTARG ;;
    u) URL=$OPTARG ;;
    s) SYNC=1 ;;
    h) usage 0 ;;
    *) usage 1 ;;
  esac
done

for bin in tkn-pac oc jq curl; do
  command -v "$bin" >/dev/null || { echo "missing required binary: $bin" >&2; exit 1; }
done

repo=$(oc get repository -n "$NS" -o json | jq '.items[0]')
[[ "$repo" == "null" ]] && { echo "no Repository CR in $NS" >&2; exit 1; }
: "${URL:=$(jq -r '.spec.url' <<<"$repo")}"

cd "$(git rev-parse --show-toplevel)"

if (( SYNC )); then
  api=${URL%/*/*}
  path=${URL#"$api"/}
  tok=$(oc get secret -n "$NS" forgejo-webhook-config \
        -o jsonpath='{.data.provider\.token}' | base64 -d)
  curl -ksS -X POST -H "Authorization: token $tok" \
    "$api/api/v1/repos/$path/mirror-sync" -w 'mirror-sync HTTP:%{http_code}\n'
fi

out=$(tkn-pac resolve -f .tekton/ --no-secret \
        -p "revision=$REF" -p "repo_url=$URL")

# Inline Repository.spec.params the way PaC does at trigger time.
while IFS=$'\t' read -r name kind a b; do
  if [[ "$kind" == "secret" ]]; then
    a=$(oc get secret -n "$NS" "$a" -o jsonpath="{.data.$b}" | base64 -d)
  fi
  esc=$(printf '%s' "$a" | sed -e ':a;N;$!ba;s/[\/&]/\\&/g;s/\n/\\n/g')
  out=$(printf '%s' "$out" | sed "s|{{ $name }}|$esc|g")
done < <(jq -r '
  .spec.params[]?
  | if .value
      then [.name, "literal", .value, ""] | @tsv
      else [.name, "secret",  .secret_ref.name, .secret_ref.key] | @tsv
    end' <<<"$repo")

printf '%s\n' "$out" | oc create -n "$NS" -f -
