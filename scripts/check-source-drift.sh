#!/usr/bin/env bash
# Refuse any difference between the files copied from mvm and their originals,
# other than the rewrites this repository documents.
#
# Usage: scripts/check-source-drift.sh
#          compare against mvm at the commit flake.lock pins (what CI runs)
#        scripts/check-source-drift.sh --against <ref> --mvm-git-dir <dir>
#          compare against another mvm ref in an explicit local clone, to see
#          what advancing the pin would bring in
#
# For each line of sources/files.tsv the original is read from mvm, the
# rewrite in sources/rewrites/<path here>.patch (if any) is applied to it, and
# the result must equal the file here byte for byte. It also checks that the
# nixpkgs and microvm.nix pins in flake.lock are the ones mvm's image flakes
# lock, and reports files that appeared in mvm's nix/images/ without being
# accounted for here. `sources/ignored.tsv` explicitly accounts for upstream
# workload-specific files that are intentionally not base-image roles.
#
# To record a new intentional rewrite: edit the file here, then regenerate its
# patch with --write-patches and describe the change in SOURCES.md.

set -euo pipefail

# shellcheck source=scripts/mvm-source.sh
. "$(dirname "${BASH_SOURCE[0]}")/mvm-source.sh"
cd "$MVM_IMAGES_ROOT"

against=""
git_dir=""
write_patches=0
while [ $# -gt 0 ]; do
  case "$1" in
    --against) against=${2:?--against needs a ref}; shift 2 ;;
    --mvm-git-dir) git_dir=${2:?--mvm-git-dir needs a directory}; shift 2 ;;
    --write-patches) write_patches=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -n "$against" ]; then
  [ -n "$git_dir" ] || { echo "--against needs an explicit --mvm-git-dir" >&2; exit 2; }
  git -C "$git_dir" rev-parse --verify -q "$against^{commit}" >/dev/null \
    || { echo "$against is not a commit in $git_dir" >&2; exit 2; }
  label="mvm $against ($(git -C "$git_dir" rev-parse --short=12 "$against^{commit}"))"
  upstream_cat() { git -C "$git_dir" show "$against:$1" 2>/dev/null; }
  upstream_ls() { git -C "$git_dir" ls-tree -r --name-only "$against" -- "$1"; }
  [ "$write_patches" = 0 ] || { echo "--write-patches only against the pinned commit" >&2; exit 2; }
else
  src=$(mvm_source_dir)
  label="mvm $(mvm_rev) (pinned)"
  upstream_cat() { [ -f "$src/$1" ] && cat "$src/$1"; }
  upstream_ls() { (cd "$src" && find "$1" -type f | sort); }
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
drift=0
report() { echo "DRIFT: $*"; drift=1; }

listed=()
while IFS=$'\t' read -r theirs ours; do
  case "$theirs" in ''|'#'*) continue ;; esac
  listed+=("$theirs")
  patchfile="sources/rewrites/$ours.patch"
  if ! upstream_cat "$theirs" > "$tmp/orig"; then
    report "$theirs no longer exists in $label (copied here as $ours)"
    continue
  fi
  if [ "$write_patches" = 1 ]; then
    mkdir -p "$(dirname "$patchfile")"
    if cmp -s "$tmp/orig" "$ours"; then
      rm -f "$patchfile"
    else
      # Zero context keeps the checked-in patch free of whitespace-only
      # context lines while still describing the exact pinned-source rewrite.
      diff -U0 --label "a/$theirs" --label "b/$ours" "$tmp/orig" "$ours" > "$patchfile" || true
    fi
    continue
  fi
  expected="$tmp/orig"
  if [ -f "$patchfile" ]; then
    rm -f "$tmp/expected"
    if ! patch -s -o "$tmp/expected" "$tmp/orig" < "$patchfile" > "$tmp/patch.log" 2>&1; then
      report "$ours: the documented rewrite no longer applies to $theirs in $label"
      sed 's/^/    /' "$tmp/patch.log"
      continue
    fi
    expected="$tmp/expected"
  fi
  if ! cmp -s "$expected" "$ours"; then
    report "$ours differs from $theirs in $label beyond its documented rewrite:"
    diff -u --label "expected ($theirs + rewrite)" --label "$ours" "$expected" "$ours" \
      | sed 's/^/    /' || true
  fi
done < sources/files.tsv

ignored=()
while IFS=$'\t' read -r theirs reason; do
  case "$theirs" in ''|'#'*) continue ;; esac
  [ -n "$reason" ] || { echo "sources/ignored.tsv: $theirs has no reason" >&2; exit 2; }
  ignored+=("$theirs")
  if ! upstream_cat "$theirs" > /dev/null; then
    report "$theirs is ignored but no longer exists in $label"
  fi
done < sources/ignored.tsv

if [ "$write_patches" = 1 ]; then
  echo "rewrote sources/rewrites/ from the working tree; describe any change in SOURCES.md"
  exit 0
fi

# Files mvm grew under nix/images/ that this repository has not accounted for.
# The per-image flake.lock files are replaced by the root flake.lock, whose
# pins are compared below.
while IFS= read -r f; do
  case "$f" in
    nix/images/builder-vm/flake.lock|nix/images/default-tenant/flake.lock|\
    nix/images/runtime-overlay/flake.lock|nix/images/initramfs/flake.lock) continue ;;
  esac
  found=0
  for l in "${listed[@]}"; do [ "$l" = "$f" ] && found=1 && break; done
  if [ "$found" = 0 ]; then
    for l in "${ignored[@]}"; do [ "$l" = "$f" ] && found=1 && break; done
  fi
  [ "$found" = 1 ] || report "$f exists in $label but is not in sources/files.tsv"
done < <(upstream_ls nix/images)

# The root lock must pin what mvm's image flakes lock.
lock_pin() { # lock-json node
  jq -c --arg n "$2" '.nodes[.nodes.root.inputs[$n]].locked | {rev, narHash}' <<<"$1"
}
ours_lock=$(cat flake.lock)
check_pin() { # our-input their-lock their-input
  local theirs_json
  if ! theirs_json=$(upstream_cat "$2"); then
    report "$2 no longer exists in $label"
    return
  fi
  if [ "$(lock_pin "$ours_lock" "$1")" != "$(lock_pin "$theirs_json" "$3")" ]; then
    report "flake.lock input '$1' is $(lock_pin "$ours_lock" "$1"), $2 in $label locks $(lock_pin "$theirs_json" "$3")"
  fi
}
for role in builder-vm default-tenant runtime-overlay; do
  check_pin nixpkgs "nix/images/$role/flake.lock" nixpkgs
done
for role in builder-vm default-tenant; do
  check_pin microvm "nix/images/$role/flake.lock" microvm
done
check_pin nixpkgs-initramfs nix/images/initramfs/flake.lock nixpkgs

if [ "$drift" != 0 ]; then
  echo "check-source-drift: the copies here have drifted from $label" >&2
  exit 1
fi
echo "check-source-drift: clean against $label (${#listed[@]} copied, ${#ignored[@]} intentionally ignored, flake.lock pins match)"
