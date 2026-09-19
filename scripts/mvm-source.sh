# shellcheck shell=bash
# Sourced, not executed: the one way scripts here find the mvm source they
# build from.
#
# The mvm commit is pinned exactly once, by the `mvm` input in flake.nix and its
# entry in flake.lock. Nothing here keeps a second copy: every function reads
# the lock, and every tree it hands back is checked against the lock's narHash,
# so a script cannot build from bytes other than the ones the flake evaluates.

MVM_IMAGES_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)

# If a configured GitHub token is refused (an organisation policy rejects it
# even for a public repository), run with NIX_CONFIG='access-tokens =' to fetch
# anonymously.
MVM_NIX=(nix --extra-experimental-features "nix-command flakes")

mvm_lock_field() {
  jq -er --arg f "$1" '.nodes.mvm.locked[$f]' "$MVM_IMAGES_ROOT/flake.lock"
}

# The pinned mvm commit, as a full 40-character SHA.
mvm_rev() {
  local rev
  rev=$(mvm_lock_field rev)
  if ! printf '%s' "$rev" | grep -qE '^[0-9a-f]{40}$'; then
    echo "flake.lock pins mvm to '$rev', not a full commit SHA" >&2
    return 1
  fi
  printf '%s\n' "$rev"
}

mvm_repo_url() {
  printf 'https://github.com/%s/%s\n' "$(mvm_lock_field owner)" "$(mvm_lock_field repo)"
}

# The pinned mvm tree in the Nix store, read-only. Prints its store path.
mvm_source_dir() {
  local rev narhash json
  rev=$(mvm_rev)
  narhash=$(mvm_lock_field narHash)
  json=$("${MVM_NIX[@]}" flake prefetch --json \
    "github:$(mvm_lock_field owner)/$(mvm_lock_field repo)/$rev")
  if [ "$(jq -r .hash <<<"$json")" != "$narhash" ]; then
    echo "mvm $rev fetched with hash $(jq -r .hash <<<"$json"), flake.lock says $narhash" >&2
    return 1
  fi
  jq -r .storePath <<<"$json"
}

# Assert a directory holds exactly the pinned mvm tree (ignoring .git).
mvm_assert_tree_matches_lock() {
  local dir=$1 narhash staged got
  narhash=$(mvm_lock_field narHash)
  staged=$(mktemp -d)
  git -C "$dir" archive --format=tar HEAD | tar -x -C "$staged"
  got=$("${MVM_NIX[@]}" hash path --type sha256 --sri "$staged")
  rm -rf "$staged"
  if [ -n "$(git -C "$dir" status --porcelain)" ]; then
    echo "$dir has uncommitted changes; refusing to build from it" >&2
    return 1
  fi
  if [ "$got" != "$narhash" ]; then
    echo "$dir hashes to $got, flake.lock pins mvm at $narhash" >&2
    return 1
  fi
}
