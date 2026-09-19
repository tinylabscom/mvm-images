#!/usr/bin/env bash
# Hold `--override-input mvm path:<checkout>` to what it promises.
#
# Usage: scripts/check-local-mvm-override.sh [system]   (default x86_64-linux)
#
# A contributor pairs a local mvm checkout with this repository by overriding
# the `mvm` input; nothing else selects one. This evaluates every image role
# three ways — from the pin, from a writable copy of the pinned tree passed as
# a `path:` override, and from that copy after a one-line edit to guest-agent
# source — and refuses unless:
#
#   1. the override of an unchanged tree yields the pin's derivations, so an
#      override introduces no difference of its own. The one exception is the
#      default microVM, whose sidecar records the input's commit: a `path:`
#      input has none, so the sidecar says "" there, and that string is the
#      only difference allowed;
#   2. the edit changes every role compiled from mvm's Rust workspace, so the
#      images really are built from the checkout the override names. The
#      QEMU/WebAssembly pack takes only a Nix helper from mvm and must not
#      change;
#   3. MVM_WORKSPACE_PATH is still refused with the override present.
#
# Evaluation only: nothing is built, so it runs on any host with Nix.

set -euo pipefail

# shellcheck source=scripts/mvm-source.sh
. "$(dirname "${BASH_SOURCE[0]}")/mvm-source.sh"
cd "$MVM_IMAGES_ROOT"

system="${1:-x86_64-linux}"
case "$system" in
  x86_64-linux|aarch64-linux) ;;
  *) echo "unsupported system: $system" >&2; exit 2 ;;
esac

# Roles compiled from mvm's Rust workspace, then the one that is not.
rust_roles=(
  builder-vm.default
  default-tenant.default
  runtime-overlay.default
  runtime-overlay.sdk-sidecar-image
  runtime-overlay.sdk-sidecar-image-musl
  initramfs.default
)
helper_only_roles=(qemu-wasm.qemu-wasm-smoke-pack)

work=$(mktemp -d)
trap 'chmod -R u+w "$work" 2>/dev/null; rm -rf "$work"' EXIT

pinned_src=$(mvm_source_dir)
rev=$(mvm_rev)
cp -R "$pinned_src" "$work/unchanged"
chmod -R u+w "$work/unchanged"
cp -R "$work/unchanged" "$work/edited"
printf '// local edit\n' >> "$work/edited/crates/mvm-agentd/src/lib.rs"
unchanged=$(cd "$work/unchanged" && pwd -P)
edited=$(cd "$work/edited" && pwd -P)

# The builder image reads its three host binaries from MVM_HOST_BIN_DIR at
# evaluation time. Evaluation copies them and nothing runs them, so stand-ins
# with the right names are enough.
mkdir "$work/host-bins"
for b in mvm-host-vm-init mvm-egress-proxy mvm-builderd; do
  printf 'evaluation stand-in\n' > "$work/host-bins/$b"
done

# Nix narrates every override on stderr; keep that for the evaluations that fail.
drv_path() { # attr [override-dir]
  local args=() out
  [ -z "${2:-}" ] || args=(--override-input mvm "path:$2")
  if ! out=$(MVM_HOST_BIN_DIR="$work/host-bins" "${MVM_NIX[@]}" eval --impure --raw \
    ".#legacyPackages.$system.$1.drvPath" "${args[@]}" 2>"$work/eval.err"); then
    cat "$work/eval.err" >&2
    return 1
  fi
  printf '%s\n' "$out"
}

# The derivation as JSON, across both `nix derivation show` layouts.
drv_json() {
  "${MVM_NIX[@]}" derivation show "$1" | jq -S '(.derivations // .) | to_entries[0].value'
}

failed=0
fail() { echo "FAIL: $*"; failed=1; }

for attr in "${rust_roles[@]}" "${helper_only_roles[@]}"; do
  pin=$(drv_path "$attr")
  same=$(drv_path "$attr" "$unchanged")
  changed=$(drv_path "$attr" "$edited")

  if [ "$pin" = "$same" ]; then
    echo "ok: $attr: overriding with the pinned tree gives the pinned derivation"
  elif [ "$attr" = default-tenant.default ]; then
    # Everything but the sidecar's generatorRev must be the same derivation:
    # the same inputs, and the same build command once the pinned commit is
    # replaced by the empty revision a path input reports.
    expected=$(drv_json "$pin" \
      | jq --arg rev "$rev" '{inputDrvs, inputSrcs, builder, args,
          cmd: (.env.buildCommand | gsub("\"generatorRev\":\"" + $rev + "\""; "\"generatorRev\":\"\""))}')
    got=$(drv_json "$same" | jq '{inputDrvs, inputSrcs, builder, args, cmd: .env.buildCommand}')
    if [ "$expected" = "$got" ]; then
      echo "ok: $attr: overriding with the pinned tree differs only in the sidecar's generatorRev"
    else
      fail "$attr: overriding with the pinned tree changes more than the sidecar's generatorRev"
      diff <(printf '%s\n' "$expected") <(printf '%s\n' "$got") | head -40 || true
    fi
  else
    fail "$attr: overriding with the pinned tree gives $same, the pin gives $pin"
  fi

  if [[ " ${helper_only_roles[*]} " == *" $attr "* ]]; then
    if [ "$changed" = "$same" ]; then
      echo "ok: $attr: does not depend on mvm's Rust sources"
    else
      fail "$attr: changed with a guest-agent source edit it should not consume"
    fi
  elif [ "$changed" != "$same" ]; then
    echo "ok: $attr: built from the overriding checkout's sources"
  else
    fail "$attr: unchanged by a guest-agent source edit, so it is not built from the override"
  fi
done

if MVM_WORKSPACE_PATH="$unchanged" drv_path runtime-overlay.default "$unchanged" \
  >/dev/null 2>"$work/refusal"; then
  fail "MVM_WORKSPACE_PATH was accepted alongside the override"
elif grep -q 'MVM_WORKSPACE_PATH is set' "$work/refusal"; then
  echo "ok: MVM_WORKSPACE_PATH is refused with the override present"
else
  fail "MVM_WORKSPACE_PATH evaluation failed, but not with the refusal:"
  tail -5 "$work/refusal"
fi

exit "$failed"
