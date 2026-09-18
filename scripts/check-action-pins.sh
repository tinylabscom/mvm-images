#!/bin/sh
# Refuse any workflow step that runs a remote action by a mutable reference.
#
# Usage: scripts/check-action-pins.sh [workflow-dir]   (default: .github/workflows)
#        scripts/check-action-pins.sh --self-test
#
# Everything this repository publishes is release-bearing, so a workflow that
# runs `owner/action@v4` runs whatever that tag points at on the day it runs.
# A remote action must name a full 40-character commit SHA. Local actions
# (`./path`) are part of this tree and are exempt; `docker://` images must be
# pinned by `@sha256:` digest. Dependabot keeps the pins current, which is why
# pinning costs nothing but review.

set -eu

# Print one finding per unpinned `uses:` in the given files, as file:line: ref.
findings() {
  for file in "$@"; do
    grep -n -E '^[[:space:]]*(-[[:space:]]+)?uses:' "$file" | while IFS= read -r hit; do
      line=${hit%%:*}
      ref=$(printf '%s\n' "${hit#*:}" \
        | sed -E 's/^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*//; s/[[:space:]]+#.*$//; s/^["'\'']//; s/["'\'']$//')
      case "$ref" in
        ./*) continue ;;
        docker://*@sha256:*)
          digest=${ref##*@sha256:}
          if printf '%s' "$digest" | grep -qE '^[0-9a-f]{64}$'; then continue; fi ;;
        docker://*) ;;
        *@*)
          sha=${ref##*@}
          if printf '%s' "$sha" | grep -qE '^[0-9a-f]{40}$'; then continue; fi ;;
      esac
      printf '%s:%s: %s\n' "$file" "$line" "$ref"
    done
  done
}

workflow_files() {
  find "$1" -type f \( -name '*.yml' -o -name '*.yaml' \) | sort
}

self_test() {
  dir=$(mktemp -d)
  trap 'rm -rf "$dir"' EXIT
  cat > "$dir/pinned.yml" <<'YAML'
jobs:
  a:
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: ./.github/actions/local
      - name: quoted
        uses: "owner/action@0123456789abcdef0123456789abcdef01234567"
      - uses: docker://alpine@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
YAML
  cat > "$dir/unpinned.yml" <<'YAML'
jobs:
  a:
    steps:
      - uses: actions/checkout@v7
      - name: branch
        uses: owner/action@main
      - uses: owner/action@3d3c42e
      - uses: owner/action
      - uses: docker://alpine:3
YAML
  pinned=$(findings "$dir/pinned.yml")
  if [ -n "$pinned" ]; then
    echo "self-test: pinned refs were reported:" >&2
    printf '%s\n' "$pinned" >&2
    exit 1
  fi
  unpinned=$(findings "$dir/unpinned.yml" | wc -l | tr -d ' ')
  if [ "$unpinned" != 5 ]; then
    echo "self-test: expected 5 findings in the unpinned fixture, got $unpinned:" >&2
    findings "$dir/unpinned.yml" >&2
    exit 1
  fi
  echo "check-action-pins --self-test: every unpinned shape was refused, every pinned one admitted"
}

if [ "${1:-}" = "--self-test" ]; then
  self_test
  exit 0
fi

dir=${1:-.github/workflows}
files=$(workflow_files "$dir")
if [ -z "$files" ]; then
  echo "check-action-pins: no workflow files under $dir; a pin gate over nothing checks nothing" >&2
  exit 1
fi

# shellcheck disable=SC2086 # word splitting over the file list is intended
out=$(findings $files)
if [ -n "$out" ]; then
  echo "check-action-pins: remote actions must be pinned by full commit SHA:" >&2
  printf '%s\n' "$out" | sed 's/^/  /' >&2
  exit 1
fi
count=$(printf '%s\n' "$files" | wc -l | tr -d ' ')
echo "check-action-pins: clean ($count workflow file(s))"
