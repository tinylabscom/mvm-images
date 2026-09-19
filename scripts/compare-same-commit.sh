#!/usr/bin/env bash
# Build one image role twice on this host — from mvm's own in-tree image flake
# at the pinned commit, and from this repository — and compare the results.
#
# Usage: scripts/compare-same-commit.sh <aarch64|x86_64> <builder-vm|default-tenant> <mvm-checkout> <out-dir>
#
# <mvm-checkout> must be a git checkout of exactly the commit flake.lock pins
# (scripts/build-host-binaries.sh leaves one behind). For builder-vm,
# MVM_HOST_BIN_DIR must name the host binaries built from it; both sides read
# the same directory, as both of mvm's producers do.
#
# For every attribute the role publishes this prints both derivation paths and
# whether they are equal, builds both, records a sha256 of every file in each
# output, and rebuilds this repository's output once more with `--rebuild` so a
# non-deterministic final step is reported rather than hidden behind a
# substituted path. It exits nonzero if any comparison differs.

set -euo pipefail

# shellcheck source=scripts/mvm-source.sh
. "$(dirname "${BASH_SOURCE[0]}")/mvm-source.sh"

arch="${1:?usage: compare-same-commit.sh <arch> <role> <mvm-checkout> <out-dir>}"
role="${2:?role}"
mvm="${3:?mvm checkout}"
out="${4:?out dir}"
system="${arch}-linux"

case "$role" in
  builder-vm) attrs=(default) ;;
  default-tenant) attrs=(default dev) ;;
  *) echo "unsupported role: $role" >&2; exit 2 ;;
esac

rev=$(mvm_rev)
if [ "$(git -C "$mvm" rev-parse HEAD)" != "$rev" ]; then
  echo "$mvm is not at the pinned mvm commit $rev" >&2
  exit 1
fi
mvm_assert_tree_matches_lock "$mvm"
unset MVM_WORKSPACE_PATH

# A shallow clone is enough for the in-tree flake, but Nix only accepts one
# when told so.
upstream="git+file://$(cd "$mvm" && pwd -P)?dir=nix/images/${role}&shallow=1"
here="$MVM_IMAGES_ROOT"
mkdir -p "$out"
status=0

files_digest() { # store-path
  (cd "$1" && find -L . -type f -print0 | sort -z | xargs -0 sha256sum)
}

for attr in "${attrs[@]}"; do
  up_ref="${upstream}#packages.${system}.${attr}"
  here_ref="${here}#legacyPackages.${system}.${role}.${attr}"

  up_drv=$("${MVM_NIX[@]}" eval --impure --raw "${up_ref}.drvPath")
  here_drv=$("${MVM_NIX[@]}" eval --impure --raw "${here_ref}.drvPath")
  echo "== ${role}.${attr} (${system})"
  echo "mvm in-tree drv:  ${up_drv}"
  echo "mvm-images drv:   ${here_drv}"
  if [ "$up_drv" = "$here_drv" ]; then
    echo "derivations: identical"
  else
    echo "derivations: DIFFER"
    status=1
  fi

  up_out=$("${MVM_NIX[@]}" build --impure --no-link --print-out-paths -L "$up_ref" | head -1)
  here_out=$("${MVM_NIX[@]}" build --impure --no-link --print-out-paths -L "$here_ref" | head -1)
  echo "mvm in-tree out:  ${up_out}"
  echo "mvm-images out:   ${here_out}"
  files_digest "$up_out" > "$out/${role}.${attr}.${arch}.mvm.sha256"
  files_digest "$here_out" > "$out/${role}.${attr}.${arch}.mvm-images.sha256"
  if diff -u "$out/${role}.${attr}.${arch}.mvm.sha256" \
      "$out/${role}.${attr}.${arch}.mvm-images.sha256"; then
    echo "output files: identical"
  else
    echo "output files: DIFFER"
    status=1
  fi
  cat "$out/${role}.${attr}.${arch}.mvm-images.sha256"

  # Same derivation means Nix built it once above. Build it again from its
  # inputs and compare, so "identical" is about the bytes and not the path.
  # Reported, not enforced: the prod default image's final step writes a
  # random dm-verity superblock UUID (tinylabscom/mvm#3499). Make this fail
  # once that fix reaches the pin.
  if "${MVM_NIX[@]}" build --impure --no-link --rebuild -L "$here_ref"; then
    echo "rebuild of the final derivation: bit-identical"
  else
    echo "rebuild of the final derivation: DIFFERS (non-deterministic final step; tinylabscom/mvm#3499)"
  fi

  # Stage the files a boot needs, under the names the release publishes, so
  # the lane's artifacts can be booted as well as compared.
  stage="$out/stage/${role}-${attr}-${arch}"
  mkdir -p "$stage"
  for f in vmlinux rootfs.ext4 rootfs.verity rootfs.roothash mvm-meta.json cmdline.txt manifest.json; do
    if [ -e "$here_out/$f" ]; then
      cp -L "$here_out/$f" "$stage/$f"
    fi
  done
done

exit "$status"
