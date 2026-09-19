# Sources taken from mvm

The image recipes here were copied from
[tinylabscom/mvm](https://github.com/tinylabscom/mvm). This file records where
each came from and every intended difference, so a copy that has quietly
diverged from mvm can be told apart from one that was changed on purpose.

## The pin

All copies were taken at mvm commit
`6717e2451e155672fafc85a1a729094869af8dd8` (W4a of the image-repository
extraction plan, the commit that exports the guest recipes from mvm's
`nix/flake.nix`). The same commit is what the images build from: it is pinned
once, by the `mvm` input in [`flake.nix`](flake.nix) and its entry in
[`flake.lock`](flake.lock). Scripts read it from `flake.lock`; nothing else
stores it.

nixpkgs and microvm.nix are pinned to what mvm's image flakes lock at that
commit: `nixpkgs` and `microvm` from `nix/images/{builder-vm,default-tenant,
runtime-overlay}/flake.lock`, and `nixpkgs-initramfs` from
`nix/images/initramfs/flake.lock`, which pins a different nixpkgs revision.
`kernel/flake.lock` is copied unchanged.

## Copied files

The machine-readable list is [`sources/files.tsv`](sources/files.tsv).
[`scripts/check-source-drift.sh`](scripts/check-source-drift.sh) compares every
entry against mvm at the pinned commit, with only the patch under
[`sources/rewrites/`](sources/rewrites/) applied, and runs first in CI.

| mvm | here | changed |
|---|---|---|
| `nix/images/builder-vm/flake.nix` | `images/builder-vm/image.nix` | yes |
| `nix/images/default-tenant/flake.nix` | `images/default-tenant/image.nix` | yes |
| `nix/images/runtime-overlay/flake.nix` | `images/runtime-overlay/image.nix` | yes |
| `nix/images/initramfs/flake.nix` | `images/initramfs/image.nix` | yes |
| `nix/images/kernel/{README.md,base.nix,builder.nix,workload.nix,flake.nix,flake.lock}` | `kernel/` | no |
| `nix/packages/qemu-wasm.nix` | `qemu-wasm/qemu-wasm.nix` | yes |
| `nix/packages/qemu-wasm-smoke-image.nix` | `qemu-wasm/qemu-wasm-smoke-image.nix` | yes |
| `nix/packages/qemu-wasm-smoke-pack.nix` | `qemu-wasm/qemu-wasm-smoke-pack.nix` | no |
| `nix/packages/emscripten-cross.meson` | `qemu-wasm/emscripten-cross.meson` | no |
| `nix/packaging/release/assert-sidecar-coherent.sh` | `packaging/assert-sidecar-coherent.sh` | no |
| `scripts/build-kernel-artifacts.sh` | `scripts/build-kernel-artifacts.sh` | yes |
| `scripts/build-qemu-wasm-smoke-pack.sh` | `scripts/build-qemu-wasm-smoke-pack.sh` | no |
| `scripts/run-qemu-wasm-{demo,smoke}-chromium.py`, `run-qemu-wasm-smoke-suite.py` | `scripts/` | no |
| `scripts/serve-qemu-wasm-smoke-pack.py` | `scripts/` | no |

## Rewrites

Every change is one of these, and nothing else.

1. **The image flakes are no longer flakes.** Each `flake.nix` became
   `images/<role>/image.nix` and lost its `inputs` block. The root `flake.nix`
   supplies nixpkgs, microvm.nix and the mvm source to each one's `outputs`,
   the way mvm's builder-vm flake already called the runtime-overlay flake's
   `outputs`. One lock then pins everything, instead of one lock per image each
   carrying the mvm commit.
2. **`workspaceRoot` is the pinned mvm source.** In mvm it was `../../..`, or
   `$MVM_WORKSPACE_PATH` when set. Here it is `mvm-src.outPath`, the store path
   of the `mvm` input. Everything that still comes from mvm — `nix/flake.nix`
   (mkGuest, the guest recipes, the host-binary manifest) and
   `nix/lib/workspace-filter.nix` — is read from it unchanged, so the filtered
   source store path, and every guest derivation built from it, is the one mvm
   produces for that commit. The root flake refuses to evaluate when
   `MVM_WORKSPACE_PATH` is set: mvm's `nix/flake.nix` would otherwise swap in
   whatever checkout it names.
3. **The kernel and the runtime overlay are local.** `builder-vm` and
   `default-tenant` import `../../kernel/*.nix` instead of
   `workspaceRoot + "/nix/images/kernel/*.nix"`, `builder-vm` imports
   `../runtime-overlay/image.nix` instead of the mvm copy, and
   `qemu-wasm-smoke-image.nix` imports `../kernel/base.nix` instead of
   `../images/kernel/base.nix`.
4. **`default-tenant`'s `generatorRev` is the mvm commit.** mvm used
   `self.rev`, which in a release checkout is the mvm commit whose
   `mk-guest.nix` built the rootfs. Here `self` would be this repository, which
   is neither where `mk-guest.nix` lives nor stable across unrelated commits, so
   it is `mvm-src.rev`. For the same mvm commit the sidecar says the same thing.
5. **`qemu-wasm.nix` takes the mvm source as an argument** and imports mvm's
   `nix/lib/crates-io.nix` from it, since that helper stays in mvm.
6. **`build-kernel-artifacts.sh`** builds `.#legacyPackages.<system>.builder-vm.*`
   instead of `./nix/images/builder-vm#packages.<system>.*`, and reads the
   kernel config budget from `xtask/src/check_kernel_config_budget.rs` in the
   pinned mvm source rather than keeping a second copy of the numbers.
7. Comments that described the old wiring were updated to describe the new one.

## Deliberately not copied

- The guest recipes (`nix/packages/mvm-guest-agent*.nix`, `mvm-setpriv.nix`, …),
  `nix/lib/*` including `workspace-filter.nix`, `mvm-host-binaries.nix` and
  `static-crates-cargo-deps.nix`, and the host packages. They are compiled
  against mvm's `Cargo.lock` and stay beside it; the images consume them
  through the pinned input.
- The per-image `flake.lock` files, replaced by the root `flake.lock`. The
  drift check compares its pins against them.
- `nix/packaging/release/assert-init-shebang.sh` and `assert-kernel-format.sh`,
  which the plan does not move. The build workflow runs them from the pinned
  mvm source.

## Known limitations of the copies

These are copied as they are so the drift check tracks them; fixing them is
follow-up work, not part of the move.

- `scripts/build-qemu-wasm-smoke-pack.sh` drives a Lima VM (`limactl`). mvm
  removed Lima, so the script cannot run in either repository. The pack builds
  directly with `nix build .#legacyPackages.x86_64-linux.qemu-wasm.qemu-wasm-smoke-pack`.
- `scripts/run-qemu-wasm-demo-chromium.py` starts
  `../web/weblinux-demo/serve.py`, which is part of mvm's site and is not here.
- The builder's host binaries are compiled with the Rust toolchain mvm's
  `rust-toolchain.toml` selects, because that is what the `cargo zigbuild` in
  `release-boot-image.yml` runs. mvm's `build.rs` embeds its copies with the
  `rust` pin in `[workspace.metadata.mvm.toolchain]` instead, so the two mvm
  producers do not use the same compiler. `scripts/build-host-binaries.sh`
  follows the release workflow.
