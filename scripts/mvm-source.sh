# shellcheck shell=bash
# Sourced, not executed: the one way scripts here find the mvm source they
# build from.
#
# The mvm commit is pinned exactly once, by the `mvm` input in flake.nix and its
# entry in flake.lock. Nothing here keeps a second copy: every function reads
# the lock, and every tree it hands back is checked against the lock's narHash,
# so a script cannot build from bytes other than the ones the flake evaluates.
#
# The one alternative is a local mvm checkout the caller names explicitly,
# validated by `mvm_local_checkout`. It is never found by looking next to this
# repository.

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

# git, answering for the directory passed with -C and nothing else: the
# variables that would redirect it to another repository are cleared, and
# optional locks are off so a probe never contends with the contributor's own
# git in the same checkout.
mvm_git() {
  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY \
    -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_COMMON_DIR -u GIT_NAMESPACE \
    GIT_OPTIONAL_LOCKS=0 git "$@"
}

# A local mvm checkout, named explicitly by the caller and never looked for.
# Prints its canonical root: absolute, symlinks resolved, and the root of its
# own git work tree, so neither a subdirectory nor a link into one can stand
# for a checkout. The files that make it an mvm checkout must be regular files
# inside it, not links to somewhere else.
mvm_local_checkout() {
  local given=$1 root top f
  if [ ! -d "$given" ]; then
    echo "$given: not a directory" >&2
    return 1
  fi
  root=$(cd "$given" && pwd -P)
  if ! top=$(mvm_git -C "$root" rev-parse --show-toplevel 2>/dev/null); then
    echo "$root: not a git checkout" >&2
    return 1
  fi
  top=$(cd "$top" && pwd -P)
  if [ "$top" != "$root" ]; then
    echo "$root: not the root of its git checkout (the root is $top)" >&2
    return 1
  fi
  for f in Cargo.toml Cargo.lock nix/flake.nix crates/mvm-build/Cargo.toml; do
    if [ -L "$root/$f" ] || [ ! -f "$root/$f" ]; then
      echo "$root: not an mvm checkout (no regular file $f)" >&2
      return 1
    fi
  done
  printf '%s\n' "$root"
}
