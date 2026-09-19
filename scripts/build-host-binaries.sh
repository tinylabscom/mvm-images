#!/usr/bin/env bash
# Cross-compile the builder VM's host binaries from the pinned mvm commit.
#
# Usage: scripts/build-host-binaries.sh <aarch64|x86_64> [work-dir]
#
# The builder VM rootfs installs three static musl binaries that are built
# outside Nix: mvm-host-vm-init, mvm-egress-proxy and mvm-builderd. The builder
# image reads them from $MVM_HOST_BIN_DIR under `--impure`. This script builds
# them exactly as mvm's release-boot-image.yml does — `cargo zigbuild --release
# --locked` in a checkout of the source — from the commit flake.lock pins, and
# prints the directory to use as MVM_HOST_BIN_DIR (appending it to
# $GITHUB_ENV when that is set).
#
# The checkout goes to work-dir (default: ${XDG_CACHE_HOME:-~/.cache}/mvm-images/
# mvm-<rev>). It is fetched from the repository flake.lock names, never found
# next to this one, and its tree is checked against flake.lock's narHash before
# anything is compiled, so the binaries come from the same bytes the flake
# evaluates.
#
# Toolchain, as release-boot-image.yml has it: zig and cargo-zigbuild must match
# [workspace.metadata.mvm.toolchain] in the checkout's Cargo.toml; rustc is
# whatever the checkout's rust-toolchain.toml selects, because that is the
# compiler a plain `cargo zigbuild` in the checkout runs.

set -euo pipefail

# shellcheck source=scripts/mvm-source.sh
. "$(dirname "${BASH_SOURCE[0]}")/mvm-source.sh"

arch="${1:?usage: build-host-binaries.sh <aarch64|x86_64> [work-dir]}"
case "$arch" in
  aarch64|x86_64) ;;
  *) echo "unsupported architecture: $arch" >&2; exit 2 ;;
esac

rev=$(mvm_rev)
work="${2:-${XDG_CACHE_HOME:-$HOME/.cache}/mvm-images/mvm-$rev}"

if [ ! -d "$work/.git" ]; then
  rm -rf "$work"
  mkdir -p "$work"
  git -C "$work" init -q
  git -C "$work" remote add origin "$(mvm_repo_url)"
  git -C "$work" fetch -q --depth 1 origin "$rev"
  git -C "$work" checkout -q --detach FETCH_HEAD
fi
if [ "$(git -C "$work" rev-parse HEAD)" != "$rev" ]; then
  echo "$work is at $(git -C "$work" rev-parse HEAD), flake.lock pins $rev" >&2
  exit 1
fi
mvm_assert_tree_matches_lock "$work"

# Read one key of [workspace.metadata.mvm.toolchain] (or a sub-table) without
# a TOML parser: the same awk mvm's install-zigbuild action uses.
toolchain_pin() { # table key
  awk -v table="$1" -v key="$2" '
    $0 == "[" table "]" { t = 1; next }
    /^\[/ { t = 0 }
    t && $1 == key { gsub(/"/, "", $3); print $3 }' "$work/Cargo.toml"
}

zig_pin=$(toolchain_pin workspace.metadata.mvm.toolchain zig)
zigbuild_pin=$(toolchain_pin workspace.metadata.mvm.toolchain cargo-zigbuild)
target=$(toolchain_pin workspace.metadata.mvm.toolchain.targets "$arch")
for v in zig_pin zigbuild_pin target; do
  if [ -z "${!v}" ]; then
    echo "no $v in [workspace.metadata.mvm.toolchain] of mvm $rev" >&2
    exit 1
  fi
done

zig_have=$(zig version 2>/dev/null || true)
if [ "$zig_have" != "$zig_pin" ]; then
  echo "zig $zig_pin required (mvm $rev pins it), found '${zig_have:-none}'" >&2
  exit 1
fi
zigbuild_have=$(cargo-zigbuild --version 2>/dev/null | awk '{print $2}' || true)
if [ "$zigbuild_have" != "$zigbuild_pin" ]; then
  echo "cargo-zigbuild $zigbuild_pin required (mvm $rev pins it), found '${zigbuild_have:-none}'" >&2
  echo "install with: cargo install cargo-zigbuild --version $zigbuild_pin --locked" >&2
  exit 1
fi

(
  cd "$work"
  cargo zigbuild --release --locked --target "$target" \
    -p mvm-build --bin mvm-host-vm-init --bin mvm-egress-proxy \
    --bin mvm-builderd
)

bin_dir="$work/target/$target/release"
for b in mvm-host-vm-init mvm-egress-proxy mvm-builderd; do
  [ -x "$bin_dir/$b" ] || { echo "cargo zigbuild produced no $bin_dir/$b" >&2; exit 1; }
done

echo "MVM_HOST_BIN_DIR=$bin_dir"
if [ -n "${GITHUB_ENV:-}" ]; then
  echo "MVM_HOST_BIN_DIR=$bin_dir" >> "$GITHUB_ENV"
fi
