# Local checkout identity V2 — coordination contract

This migration is unreleased. It coordinates mvm-images #67 with mvm #4412;
implementation, platform and paired-consumer acceptance remain separate gates.
It does not authorize an image build, publication, lock advance or merge-queue
dependency.

## Wire boundary

New producer manifests remain schema 3/base-image contract 1, with images-only
`local_checkouts` and no guest-runtime roles. Checkout identities contain a full
SHA-1 commit and one of these exact worktree shapes:

```json
{"state":"clean"}
{"state":"dirty","fingerprint_version":2,"fingerprint":"<lowercase SHA-256>"}
```

Clean records omit version/fingerprint fields; explicit null is not omission.
Historical V1 dirty hashes cannot be reinterpreted as V2: the strict consumer
requires rebuilding those local sets. Legacy clean records remain readable
where their identity can still be verified safely.

The canonical record encoder and fixed literal vectors are exercised in
`scripts/tests/test_local_mvm.py`; the paired Rust specification is
`crates/mvm-build/src/image_source/checkout-identity-v2.md` in mvm. The schema-2
wording in the old handoff is not the current producer contract. Both sides
must agree on the same encoding and configuration semantics before acceptance.

## Default source context: no user environment setup

V2 internally selects **NoSystem configuration and attributes by default**.
This is a deliberate versioned policy, not a discovery failure fallback and not
a claim to reproduce native Git's entire ambient configuration stack.

Repository rules, `.git/info` rules, and declared HOME/XDG/global configuration
continue to contribute. Unknown/malformed/unsupported contributing data refuses;
it never becomes a clean observation. No Git executable, utility process,
environment mutation, or guessed POSIX installation prefix is used.

Configuration and attributes are resolved independently:

| Control | Meaning |
| --- | --- |
| All system controls unset | Exclude system config and system attributes |
| Accessible `GIT_CONFIG_SYSTEM` supplied | Opt into exactly that config data file; attributes stay excluded |
| `GIT_CONFIG_NOSYSTEM` explicitly true | Exclude system config, taking precedence over `GIT_CONFIG_SYSTEM` |
| `GIT_ATTR_NOSYSTEM` explicitly true | Exclude system attributes independently |
| Either NOSYSTEM control explicitly false | Request its implicit system source; refuse if its prefix cannot be established |

Controls use Git-compatible boolean semantics: case-insensitive textual aliases,
empty false, valueless configuration true, bounded base-0 integers and supported
k/m/g units. Invalid values refuse. `core.autocrlf=input` is handled separately
and case-insensitively. Contributing boolean settings are validated even in
unchanged or empty checkouts.

A supplied config file does not supply an installation prefix or opt into
system attributes. POSIX compiled prefix/sysconfdir cannot be inferred from
executable paths, Homebrew links, Nix wrappers or config parents; unresolved
`%(prefix)` paths refuse with absolute-data-path guidance. Known Windows
installation-data layouts remain available for explicitly requested sources or
actually configured prefix-dependent resources. Default NoSystem observation
does not need that discovery.

Resolve one immutable system context at each repository observation boundary
and share it with configuration, root and rule readers. A recursive submodule
lookup is a separate observation. Do not mix newly resolved contexts within one
lookup or silently approximate unsupported include/filter/encoding behavior.

## Artifact and evidence boundary

Every supplied workload rootfs member needs exactly one actual ext4 root with
a regular mode-0444 `/etc/mvm/base-image-contract` containing exact `1\n`.
Inspection is in-process over copied staging bytes; invalid supplied roots
never fall back to the source declaration. Partial sets without workload-rootfs
members retain the expected source compatibility declaration.

The producer rechecks staged artifact digests and before/after checkout
identities, preserving existing output and removing private staging on refusal.
Boot/build diagnostics and host checks are not signed-release or installed-boot
evidence. Published CLI/runtime prerequisites and historical signed sets remain
unchanged.
