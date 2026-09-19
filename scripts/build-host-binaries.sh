#!/usr/bin/env bash
# Cross-compile the builder VM's host binaries from mvm source.
#
# Usage: scripts/build-host-binaries.sh <aarch64|x86_64> [work-dir]
#        scripts/build-host-binaries.sh --mvm-checkout <dir> <aarch64|x86_64>
#
# The builder VM rootfs installs three static musl binaries that are built
# outside Nix: mvm-host-vm-init, mvm-egress-proxy and mvm-builderd. The builder
# image reads them from $MVM_HOST_BIN_DIR under `--impure`. This script builds
# them exactly as mvm's release-boot-image.yml does — `cargo zigbuild --release
# --locked` in a checkout of the source — and prints the directory to use as
# MVM_HOST_BIN_DIR (appending it to $GITHUB_ENV when that is set).
#
# By default the source is the commit flake.lock pins. The checkout goes to
# work-dir (default: ${XDG_CACHE_HOME:-~/.cache}/mvm-images/mvm-<rev>). It is
# fetched from the repository flake.lock names, never found next to this one,
# and its tree is checked against flake.lock's narHash before anything is
# compiled, so the binaries come from the same bytes the flake evaluates.
#
# --mvm-checkout builds from a local mvm checkout instead: the one a paired
# image build names with `--override-input mvm path:<dir>`, so the builder's
# host binaries and its guest binaries come from the same tree. The path is
# canonicalised and must be the root of an mvm git checkout; nothing looks for
# one. It is built in place, into whatever target directory cargo resolves for
# it (so a CARGO_TARGET_DIR the caller set is honoured), and uncommitted
# changes are built as they are — which is the point — and reported.
#
# Toolchain, as release-boot-image.yml has it: zig and cargo-zigbuild must match
# [workspace.metadata.mvm.toolchain] in the source's Cargo.toml; rustc is
# whatever the source's rust-toolchain.toml selects, because that is the
# compiler a plain `cargo zigbuild` in the checkout runs.

set -euo pipefail

# shellcheck source=scripts/mvm-source.sh
. "$(dirname "${BASH_SOURCE[0]}")/mvm-source.sh"

usage() {
  echo "usage: build-host-binaries.sh <aarch64|x86_64> [work-dir]" >&2
  echo "       build-host-binaries.sh --mvm-checkout <dir> <aarch64|x86_64>" >&2
  exit 2
}

local_checkout=""
if [ "${1:-}" = "--mvm-checkout" ]; then
  [ $# -ge 2 ] || usage
  local_checkout=$2
  shift 2
fi
[ $# -ge 1 ] || usage
arch=$1
case "$arch" in
  aarch64|x86_64) ;;
  *) echo "unsupported architecture: $arch" >&2; exit 2 ;;
esac

if [ -n "$local_checkout" ]; then
  if [ $# -gt 1 ]; then
    echo "--mvm-checkout builds in the checkout itself and takes no work-dir" >&2
    exit 2
  fi
  src=$(mvm_local_checkout "$local_checkout")
  rev=$(mvm_git -C "$src" rev-parse --verify 'HEAD^{commit}')
  if [ -n "$(mvm_git -C "$src" status --porcelain --untracked-files=all)" ]; then
    state="with uncommitted changes"
  else
    state="clean"
  fi
  label="the local mvm checkout $src"
  echo "building host binaries from $label at $rev ($state)" >&2
else
  [ $# -le 2 ] || usage
  rev=$(mvm_rev)
  src="${2:-${XDG_CACHE_HOME:-$HOME/.cache}/mvm-images/mvm-$rev}"
  label="mvm $rev"

  if [ ! -d "$src/.git" ]; then
    rm -rf "$src"
    mkdir -p "$src"
    git -C "$src" init -q
    git -C "$src" remote add origin "$(mvm_repo_url)"
    git -C "$src" fetch -q --depth 1 origin "$rev"
    git -C "$src" checkout -q --detach FETCH_HEAD
  fi
  if [ "$(git -C "$src" rev-parse HEAD)" != "$rev" ]; then
    echo "$src is at $(git -C "$src" rev-parse HEAD), flake.lock pins $rev" >&2
    exit 1
  fi
  mvm_assert_tree_matches_lock "$src"
fi

# Read one key of [workspace.metadata.mvm.toolchain] (or a sub-table) without
# a TOML parser: the same awk mvm's install-zigbuild action uses.
toolchain_pin() { # table key
  awk -v table="$1" -v key="$2" '
    $0 == "[" table "]" { t = 1; next }
    /^\[/ { t = 0 }
    t && $1 == key { gsub(/"/, "", $3); print $3 }' "$src/Cargo.toml"
}

zig_pin=$(toolchain_pin workspace.metadata.mvm.toolchain zig)
zigbuild_pin=$(toolchain_pin workspace.metadata.mvm.toolchain cargo-zigbuild)
target=$(toolchain_pin workspace.metadata.mvm.toolchain.targets "$arch")
for v in zig_pin zigbuild_pin target; do
  if [ -z "${!v}" ]; then
    echo "no $v in [workspace.metadata.mvm.toolchain] of $label" >&2
    exit 1
  fi
done

zig_have=$(zig version 2>/dev/null || true)
if [ "$zig_have" != "$zig_pin" ]; then
  echo "zig $zig_pin required ($label pins it), found '${zig_have:-none}'" >&2
  exit 1
fi
zigbuild_have=$(cargo-zigbuild --version 2>/dev/null | awk '{print $2}' || true)
if [ "$zigbuild_have" != "$zigbuild_pin" ]; then
  echo "cargo-zigbuild $zigbuild_pin required ($label pins it), found '${zigbuild_have:-none}'" >&2
  echo "install with: cargo install cargo-zigbuild --version $zigbuild_pin --locked" >&2
  exit 1
fi

(
  cd "$src"
  cargo zigbuild --release --locked --target "$target" \
    -p mvm-build --bin mvm-host-vm-init --bin mvm-egress-proxy \
    --bin mvm-builderd
)

# Where cargo put them, as cargo resolves it for this source, rather than a
# guess at `$src/target` that a configured target directory would make wrong.
target_dir=$(cd "$src" && cargo metadata --format-version 1 --no-deps | jq -er .target_directory)
bin_dir="$target_dir/$target/release"
for b in mvm-host-vm-init mvm-egress-proxy mvm-builderd; do
  [ -x "$bin_dir/$b" ] || { echo "cargo zigbuild produced no $bin_dir/$b" >&2; exit 1; }
done

echo "MVM_HOST_BIN_DIR=$bin_dir"
if [ -n "${GITHUB_ENV:-}" ]; then
  echo "MVM_HOST_BIN_DIR=$bin_dir" >> "$GITHUB_ENV"
fi
