# Sources taken from mvm

The image recipes here were copied from
[tinylabscom/mvm](https://github.com/tinylabscom/mvm). This file records where
each came from and every intended difference, so a copy that has quietly
diverged from mvm can be told apart from one that was changed on purpose.

## The pin

All copies were taken at mvm commit
`5460c6e11298082e754ce9433df9af9f61de71d5`, advanced from
`1f2db79b319c8e74be5e09eeb337fac6d2ddf788` (itself advanced from
`e97eea9ace29d831ee0c755fef758d7e289ca831`). The later pins carry the
follow-up that builds the GPU shims' musl variant without bootstrapping a
musl Rust/LLVM toolchain (tinylabscom/mvm#3617; the earlier pin's musl
stdenv rebuilt rustc from uncached sources on the image builders) and the
no-op soname-rename guard the first full overlay build exposed
(tinylabscom/mvm#3650). The
`e97eea9a` pin brought the runtime
overlay to parity with mvm's builders — the mediated `ping` binary joins the
staged set and the read-only overlay drops its ext4 journal to stay inside
its base budget (tinylabscom/mvm#3527) — and follows mvm's removal of the
dead qemu-wasm driver scripts (tinylabscom/mvm#3606): their copies and the
`run-qemu-wasm-smoke-suite.py` rewrite are deleted here rather than kept
against sources that no longer exist. It also exposes the guest GPU shim
packages (`mvm-gpu-shims-{glibc,musl}`, tinylabscom/mvm#3573), fixes the musl
shim build to use a dynamic-linking musl stdenv (tinylabscom/mvm#3611), and
includes the guest activation that arms them
(`mvm.gpu=1` cmdline token with per-libc LD_LIBRARY_PATH injection,
tinylabscom/mvm#3590, #3595). The rewrite ledger deliberately excludes the
Kubernetes-specific kernel work in the upstream history. The generic rootless posture belongs here and follows
the permanent NIC-less FlowMux/vsock contract in `README.md`; it is not an
import of `workload-k8s`. The copies were first taken at
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
[`sources/ignored.tsv`](sources/ignored.tsv) accounts for upstream files that
are intentionally not base-image sources and requires a reason for each one.

| mvm | here | changed |
|---|---|---|
| `nix/images/builder-vm/flake.nix` | `images/builder-vm/image.nix` | yes |
| `nix/images/default-tenant/flake.nix` | `images/default-tenant/image.nix` | yes |
| `nix/images/runtime-overlay/flake.nix` | `images/runtime-overlay/image.nix` | yes |
| `nix/images/initramfs/flake.nix` | `images/initramfs/image.nix` | yes |
| `nix/images/version.nix` | `images/version.nix` | no |
| `nix/images/kernel/{README.md,base.nix,builder.nix,workload.nix,flake.nix,flake.lock}` | `kernel/` | no |
| `nix/packages/qemu-wasm.nix` | `qemu-wasm/qemu-wasm.nix` | yes |
| `nix/packages/qemu-wasm-smoke-image.nix` | `qemu-wasm/qemu-wasm-smoke-image.nix` | yes |
| `nix/packages/qemu-wasm-smoke-pack.nix` | `qemu-wasm/qemu-wasm-smoke-pack.nix` | no |
| `nix/packages/emscripten-cross.meson` | `qemu-wasm/emscripten-cross.meson` | no |
| `nix/packaging/release/assert-sidecar-coherent.sh` | `packaging/assert-sidecar-coherent.sh` | no |
| `scripts/build-kernel-artifacts.sh` | `scripts/build-kernel-artifacts.sh` | yes |
| `scripts/run-qemu-wasm-smoke-chromium.py` | `scripts/` | no |
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
   `self.rev or self.dirtyRev or ""`, which in a release checkout is the mvm
   commit whose `mk-guest.nix` built the rootfs. Here `self` would be this
   repository, which is neither where `mk-guest.nix` lives nor stable across
   unrelated commits, so it is `mvm-src.rev or mvm-src.dirtyRev or ""`. For the
   same mvm commit the sidecar says the same thing, and a local checkout passed
   with `--override-input mvm path:<dir>`, which has no revision, leaves it
   empty exactly as mvm does for a `path:` build.
5. **`qemu-wasm.nix` takes the mvm source as an argument** and imports mvm's
   `nix/lib/crates-io.nix` from it, since that helper stays in mvm.
6. **`build-kernel-artifacts.sh`** builds `.#legacyPackages.<system>.builder-vm.*`
   instead of `./nix/images/builder-vm#packages.<system>.*`, and reads the
   kernel config budget from `xtask/src/check_kernel_config_budget.rs` in the
   pinned mvm source rather than keeping a second copy of the numbers.
7. Comments that described the old wiring were updated to describe the new one.
8. **Workload-specific upstream image files are not base-image roles.** The
   drift gate accounts for them through `sources/ignored.tsv` instead of
   silently copying them. At this pin that excludes the `llm-agent` example
   and the Kubernetes-specific `workload-k8s` kernel. The latter assumes
   guest bridge/veth-style networking that violates this repository's
   permanent NIC-less FlowMux/vsock contract. Generic rootless capabilities
   are designed and published here under workload-neutral names.
9. **Guest network devices are removed from the image sources.** The shared
   kernel explicitly disables `NETDEVICES`, `VIRTIO_NET`, `TUN`, `VETH`,
   bridges and macvlan. The QEMU/WebAssembly engine is built without libslirp,
   its smoke guest configures loopback only, and its launch plan contains no
   network device. These intentional differences implement this repository's
   permanent vsock-only contract and are exercised by the local BDD/E2E gates.
10. **The generic rootless posture is authored here.**
    `images/rootless-tenant/image.nix` and `kernel/rootless.nix` are new
    mvm-images sources, not copies of the rejected Kubernetes-specific image.
    Its separate kernel and image recipes add only workload-neutral namespace,
    cgroup, PTY and filesystem facilities. The rootfs adds `crun` and
    `fuse-overlayfs`; the direct-VMM smoke variant proves those facilities as
    uid 1000 while proving loopback-only, vsock-mediated networking. The
    `default-tenant` recipe remains independent and is not widened by the
    rootless capability floor.
11. **The runtime overlay stages the guest GPU shim sets.**
    `images/runtime-overlay/image.nix` adds the `mvm-gpu-shims-{glibc,musl}`
    packages from the pinned mvm source, staged at `gpu/<libc>/` under the
    mount point, and the overlay budget rises 16 → 24 MiB to hold both sets.
    The guest activation puts them on the loader path only when the boot arms
    the GPU plane (`mvm.gpu=1`), so an ordinary guest never picks the shim up
    and never dials a GPU endpoint that does not exist. mvm's own copy of the
    recipe does not stage them; composing the shim sets into the overlay is
    this repository's composition decision (tinylabscom/mvm-images#10).

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
- Files listed in `sources/ignored.tsv`. They are checked for explicit
  accounting but are neither copied nor published as base-image roles.

## Known limitations of the copies

These are copied as they are so the drift check tracks them; fixing them is
follow-up work, not part of the move.

- The builder's host binaries are compiled with the Rust toolchain mvm's
  `rust-toolchain.toml` selects, because that is what the `cargo zigbuild` in
  `release-boot-image.yml` runs. mvm's `build.rs` embeds its copies with the
  `rust` pin in `[workspace.metadata.mvm.toolchain]` instead, so the two mvm
  producers do not use the same compiler. `scripts/build-host-binaries.sh`
  follows the release workflow.
