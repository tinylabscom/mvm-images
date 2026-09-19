#!/usr/bin/env bash
# Install the zig and cargo-zigbuild that scripts/build-host-binaries.sh needs,
# at the versions the pinned mvm commit declares in
# [workspace.metadata.mvm.toolchain].
#
# Usage: scripts/install-host-toolchain.sh [prefix]   (default: ~/.local/mvm-images)
#
# zig lands in <prefix>/zig-<version>/ with a link in <prefix>/bin, which is
# appended to $GITHUB_PATH when that is set. rustc is not installed here: a
# `cargo zigbuild` in the mvm checkout runs the toolchain that checkout's
# rust-toolchain.toml selects, and rustup fetches it on first use.

set -euo pipefail

# shellcheck source=scripts/mvm-source.sh
. "$(dirname "${BASH_SOURCE[0]}")/mvm-source.sh"

prefix="${1:-$HOME/.local/mvm-images}"
cargo_toml="$(mvm_source_dir)/Cargo.toml"

pin() {
  awk -v key="$1" '
    $0 == "[workspace.metadata.mvm.toolchain]" { t = 1; next }
    /^\[/ { t = 0 }
    t && $1 == key { gsub(/"/, "", $3); print $3 }' "$cargo_toml"
}
zig_version=$(pin zig)
zigbuild_version=$(pin cargo-zigbuild)
[ -n "$zig_version" ] && [ -n "$zigbuild_version" ] \
  || { echo "no zig / cargo-zigbuild pin in mvm $(mvm_rev)" >&2; exit 1; }

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) platform=x86_64-linux ;;
  Linux-aarch64) platform=aarch64-linux ;;
  Darwin-arm64) platform=aarch64-macos ;;
  Darwin-x86_64) platform=x86_64-macos ;;
  *) echo "no zig release for $(uname -s) $(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "$prefix/bin"
if [ "$("$prefix/bin/zig" version 2>/dev/null || true)" != "$zig_version" ]; then
  # The release index names the tarball and its sha256; refuse a download that
  # does not match it.
  entry=$(curl -fsSL https://ziglang.org/download/index.json \
    | jq -er --arg v "$zig_version" --arg p "$platform" '.[$v][$p]')
  url=$(jq -er .tarball <<<"$entry")
  sha=$(jq -er .shasum <<<"$entry")
  tarball=$(mktemp)
  curl -fsSL -o "$tarball" "$url"
  echo "$sha  $tarball" | sha256sum -c - >/dev/null \
    || { echo "zig tarball $url does not match its published sha256" >&2; exit 1; }
  rm -rf "$prefix/zig-$zig_version"
  mkdir -p "$prefix/zig-$zig_version"
  tar -xJf "$tarball" -C "$prefix/zig-$zig_version" --strip-components=1
  rm -f "$tarball"
  ln -sf "$prefix/zig-$zig_version/zig" "$prefix/bin/zig"
fi
"$prefix/bin/zig" version

if [ "$(cargo zigbuild --version 2>/dev/null | awk '{print $2}' || true)" != "$zigbuild_version" ]; then
  for attempt in 1 2 3; do
    if CARGO_NET_RETRY=10 cargo install cargo-zigbuild --version "$zigbuild_version" --locked; then
      break
    fi
    [ "$attempt" -lt 3 ] || exit 1
    sleep "$((attempt * 10))"
  done
fi

if [ -n "${GITHUB_PATH:-}" ]; then
  echo "$prefix/bin" >> "$GITHUB_PATH"
fi
echo "zig $zig_version and cargo-zigbuild $zigbuild_version installed; zig is in $prefix/bin"
