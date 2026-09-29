#!/usr/bin/env bash
# Rebuild one canonical mvm-images role on one architecture and require the
# final derivation to be byte-for-byte reproducible.
#
# Usage: scripts/check-reproducible.sh \
#   <aarch64|x86_64> <builder-vm|default-tenant|rootless-tenant> <out-dir>
#
# The builder role evaluates pure: at boot ABI 1 it bakes no mvm host
# binaries — the boot contract supplies them from mvmctl's payload — so it
# reads no environment. The tenant roles read MVM_BOOT_IMAGE_TAG and stay
# impure. This script never invokes mvm or compares against mvm's retired
# in-tree image recipes: mvm-images is the canonical image producer.

set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
arch="${1:?usage: check-reproducible.sh <arch> <role> <out-dir>}"
role="${2:?role}"
out="${3:?out dir}"
system="${arch}-linux"

case "$role" in
  # ABI 1: the builder image reads no environment, so its eval is pure.
  builder-vm)
    attrs=(default)
    impure=()
    ;;
  default-tenant)
    attrs=(default dev)
    impure=(--impure)
    ;;
  rootless-tenant)
    attrs=(default)
    impure=(--impure)
    ;;
  *) echo "unsupported role: $role" >&2; exit 2 ;;
esac

unset MVM_WORKSPACE_PATH
mkdir -p "$out"
nix_cmd=(nix --extra-experimental-features "nix-command flakes")

files_digest() { # store-path
  (cd "$1" && find -L . -type f -print0 | sort -z | xargs -0 sha256sum)
}

for attr in "${attrs[@]}"; do
  ref="${root}#legacyPackages.${system}.${role}.${attr}"
  witness="${out}/${role}.${attr}.${arch}"

  drv=$("${nix_cmd[@]}" eval "${impure[@]}" --raw "${ref}.drvPath")
  first=$("${nix_cmd[@]}" build "${impure[@]}" --no-link --print-out-paths -L "$ref" | head -1)
  printf '%s\n' "$drv" > "${witness}.drvpath"
  files_digest "$first" > "${witness}.sha256"

  echo "== ${role}.${attr} (${system})"
  echo "derivation: $drv"
  cat "${witness}.sha256"

  # --rebuild ignores an existing result and asks Nix to compare the new bytes
  # with the registered output, failing if the final derivation is not stable.
  "${nix_cmd[@]}" build "${impure[@]}" --no-link --rebuild -L "$ref"
  echo "rebuild: bit-identical"

  # Preserve directly bootable evidence alongside the digest witness.
  stage="$out/stage/${role}-${attr}-${arch}"
  mkdir -p "$stage"
  for file in \
    vmlinux kernel.img rootfs.ext4 rootfs.verity rootfs.roothash \
    mvm-meta.json cmdline.txt manifest.json
  do
    if [ -e "$first/$file" ]; then
      cp -L "$first/$file" "$stage/$file"
    fi
  done
done
