# mvm-images

The system-image train for [mvm](https://github.com/tinylabscom/mvm): the guest
images an mvm host boots, built, verified, signed and published here, and
consumed there through one digest-pinned lock file.

This repository is being populated by the migration described in
[`specs/plans/2026-09-16-image-repository-extraction.md`](https://github.com/tinylabscom/mvm/blob/main/specs/plans/2026-09-16-image-repository-extraction.md)
in the `mvm` repository. Governance comes first, sources follow, and the trust
root moves last. The image sources are here now and build in CI; until the
migration reaches its publication workstream, the canonical images are still
published from `mvm`'s `boot-image/vN` releases, and nothing here publishes
anything.

## Architecture: images are produced here

`mvm-images` is the canonical producer of every base image `mvm` needs:
kernels, root filesystems, runtime overlays, initramfs images, builder images
and bootstrap inputs. `mvm` consumes released image sets through its
digest-pinned image lock; it does not own a second canonical image definition
or publication path. During the extraction there are temporary source mirrors,
but image reproducibility is checked entirely against the canonical
definitions here. The dependency is one way:

```text
mvm source revision ──► mvm-images builds and publishes an image set
                                      │
                                      ▼
                          mvm verifies and consumes it
```

Application and template repositories describe workloads. They do not move
workload-specific image variants into this repository. When several workloads
need the same guest capability, this repository provides a generic base image
and advertises that capability in the image-set manifest; consumers select it
by contract rather than by a workload name.

### Base workload images

The image set has two distinct workload postures:

- **`default-tenant`** is the smallest sealed workload image. Its entrypoint is
  unprivileged, but its kernel deliberately omits facilities that a single
  admitted process does not need, including namespace and cgroup hierarchies.
- **`rootless-tenant`** is the generic base for a workload that runs its own
  rootless container stack or in-guest supervisor. It still runs workload code
  as the unprivileged workload identity and preserves the same verified-boot,
  vsock, storage, egress and admission boundaries, while adding the generic
  kernel/userspace capability floor for rootless process isolation (user,
  mount, PID, IPC and UTS namespaces), cgroup v2 delegation and controllers,
  container filesystems, PTYs and container storage on an attached writable
  volume.

**No guest NIC, ever.** Neither base image may create or expect a NIC, TAP,
TUN, bridge, veth pair, macvlan, SLIRP, passt, vpnkit, CNI dataplane or other
guest packet-networking path. Guest loopback exists only for local adapters.
Proxy-aware TCP and UDP, controlled DNS, mediated ping, typed connectors and
declared ingress all cross the single authenticated FlowMux session over vsock;
the host endpoint applies signed policy and opens any external socket. There is
no guest firewall, NAT or routing fallback because there is no guest NIC for
one to govern. This is the permanent networking contract documented in
[`mvm`'s networking guide](https://github.com/tinylabscom/mvm/blob/main/public/src/content/docs/guides/networking.md).

`rootless-tenant` is not a Kubernetes image. It contains no Kubernetes package,
service, configuration or naming. Kubernetes, build farms and other consumers
compose their own software in their own repositories on top of the same
generic rootless base. Keeping it separate from `default-tenant` avoids
silently widening the kernel and attack surface of ordinary single-process
workloads. A consumer that assumes CNI, pod bridges, veth pairs, NodePort or a
guest-side NAT is incompatible with `mvm`; it must use loopback adapters and
the admitted vsock path instead.

The rootless image ships the workload-neutral userspace floor: `crun` and
`fuse-overlayfs`, alongside the existing BusyBox init/runtime surface. Its
kernel adds user, mount, PID, IPC and UTS namespaces, unified cgroup v2
controllers and delegation, PTYs, overlay/FUSE storage and filesystem event
notification. It deliberately omits `CONFIG_NET_NS` as well as every network
device. Consumers use an attached writable volume for container state and the
same loopback-plus-FlowMux path as `default-tenant` for all external traffic.

## What this repository will own

| Role | Artifacts |
|---|---|
| Builder VM | kernel and rootfs the Nix build jobs run inside |
| Workload kernel | the kernel a workload microVM boots |
| Workload rootfs | the verity-sealed default rootfs, its hash tree and root hash |
| Rootless tenant | generic rootless-container-capable kernel and sealed rootfs |
| Runtime overlay | the guest runtime binaries overlaid at launch |
| SDK sidecars | the in-guest host-services library, one per C library |
| Stage 0 seeds | the bootstrap kernel and Nix seed a cold host starts from |
| QEMU/WebAssembly smoke pack | the browser-tier smoke artifacts |

Guest architectures: `x86_64` and `aarch64`. Artifacts describe the *guest* —
architecture, boot protocol, format, required devices — never the host
operating system. A host backend declares which of those contracts it can
satisfy, so Firecracker on Linux and HVF on macOS consume the same bytes.

## What it does not own

Host CLI and runtime source, the guest agent, artifact acquisition and
verification code, admission, and the code that boots a builder — Stage 0
orchestration, the builder runner and the VMM drivers — all stay in `mvm`.
Stage 0's *seed inputs* move here; the code that runs Stage 0 does not.
Workload-specific packages, services and configuration stay with their
application or template repository. In particular, this repository may provide
a generic rootless base but must not grow a Kubernetes-named image or kernel.

## Release contract

One release publishes one atomic image set. The manifest is the root object and
individual assets are never selected by asking GitHub for "latest". A set that
is missing an architecture or a role does not publish.

Schema v2 qualifies each workload kernel and rootfs role with its generic
profile: `default_tenant` or `rootless_tenant`. Both pairs are required for
both guest architectures, and consumers select a kernel and rootfs from one
profile atomically. The profile is not a backend or workload name; it is the
declared base-image capability floor.

Consumers pin a set by digest in `mvm`'s checked-in image lock, which records
the repository, the immutable release tag, the manifest digest and the expected
signing identity. Rolling back is a lock change to a previously verified set,
never a mutation of a published one.

Releases are immutable. A bad set is superseded by publishing revocation
metadata and a new set, never by deleting or overwriting an existing one.

### Cadence and retention

Image sets are published when their inputs change — a kernel bump, a Nix input
update, a guest ABI change — rather than on a calendar. Published releases and
their assets are retained indefinitely, because old `mvm` versions resolve the
exact set they were built against.

## Security

- No secrets and no customer data, ever, in this repository or its artifacts.
  The only credentials any workflow holds are the short-lived OIDC tokens
  Sigstore keyless signing mints.
- Signing happens only in a protected release environment, from a protected tag
  namespace. An untrusted branch cannot mint the allow-listed release identity.
- Third-party actions are pinned by immutable commit SHA.
  `scripts/check-action-pins.sh` refuses anything else on every pull request.
- Report a suspected compromise of a published artifact or signing identity as
  described in [SECURITY.md](SECURITY.md). Response is to publish revocation
  metadata and a superseding set, then move consumers by lock update.

## Layout

```text
justfile                contributor front door: build, pair, gate and manifest recipes
flake.nix, flake.lock   the one flake: every image, one mvm pin
images/
  builder-vm/image.nix       builder VM kernel + rootfs, Stage 0 rootfs, kernel attrs
  default-tenant/image.nix   default workload microVM (verity-sealed prod, dev)
  rootless-tenant/image.nix  generic rootless OCI base (verity-sealed prod, smoke)
  runtime-overlay/image.nix  runtime overlay and the glibc/musl SDK sidecars
  initramfs/image.nix        universal initramfs
kernel/                 builder, workload and rootless kernel configs; standalone flake
qemu-wasm/              QEMU/WebAssembly engine, smoke image and browser pack
packaging/              static checks run on staged artifacts
scripts/                kernel, host-binary, QEMU-wasm and drift tooling
sources/                what was copied from mvm, and the documented rewrites
SOURCES.md              provenance of every copied file
```

The root flake exposes each image role as an attribute set carrying the
attribute names `mvm` used, so `mvm`'s `nix/images/<role>#packages.<system>.<attr>`
is `.#legacyPackages.<system>.<role>.<attr>` here — for example
`.#legacyPackages.x86_64-linux.builder-vm.default` or
`.#legacyPackages.aarch64-linux.runtime-overlay.sdk-sidecar-image-musl`. The
rootless base is `.#legacyPackages.<system>.rootless-tenant.default`. The
QEMU/WebAssembly outputs are under `qemu-wasm`. The kernel needs nothing from
`mvm`, so `kernel/` stays a flake of its own (`nix build ./kernel#workload-vmlinux`).

## Where the mvm code comes from

The binaries inside the images — the guest agent, the runtime helpers, the SDK
library, the builder's init — are compiled from `mvm` source against `mvm`'s
`Cargo.lock`. Their program source stays in `mvm`; the canonical image
composition, image-specific build policy and published outputs belong here.
During extraction, this repository takes `mvm` as a flake input pinned to one
exact commit (`flake.nix`, recorded in `flake.lock`) and still evaluates some
of `mvm`'s Nix recipes so the moved images can be compared byte-for-byte. That
is a migration mechanism, not a second ownership path: the end state removes
canonical image construction from `mvm`, and `mvm` consumes the generated
image set from this repository. Nothing tracks `mvm`'s `main`, and nothing
looks for a checkout next to this one. [SOURCES.md](SOURCES.md) records which
files were copied from `mvm` and every intended difference;
`scripts/check-source-drift.sh` fails when a copy differs from `mvm` at the
pinned commit by anything else.

### Building locally

Every documented command below is also a `just` recipe (`just --list`):
`just build <role> [attr]` for the plain Nix builds, `just builder-vm [mvm-checkout]`
for the builder image, `just with-mvm <checkout> <role>` for a paired checkout,
`just image-set [mvm-checkout] <role>` on a host without Nix, and
`just release-check` for the pre-publish gates.

The images are Linux artifacts, so build them on Linux (or through a Linux
remote builder):

```sh
nix build .#legacyPackages.x86_64-linux.runtime-overlay.default
nix build .#legacyPackages.x86_64-linux.initramfs.default
nix build .#legacyPackages.x86_64-linux.default-tenant.default --impure
nix build .#legacyPackages.x86_64-linux.rootless-tenant.default --impure
nix build ./kernel#workload-vmlinux
nix build ./kernel#rootless-vmlinux
```

The builder VM also needs three static host binaries built outside Nix. Build
them from the pinned commit, then point the image at them:

```sh
scripts/install-host-toolchain.sh          # zig + cargo-zigbuild at mvm's pins
scripts/build-host-binaries.sh x86_64      # prints MVM_HOST_BIN_DIR=...
MVM_HOST_BIN_DIR=... nix build .#legacyPackages.x86_64-linux.builder-vm.default --impure
```

`MVM_WORKSPACE_PATH` must be unset; the flake refuses to evaluate with it,
because `mvm`'s `nix/flake.nix` would otherwise build from whatever checkout it
names. If Nix has a GitHub token configured that the `tinylabscom` organisation
refuses, fetch anonymously with `NIX_CONFIG='access-tokens ='`.

CI builds every image on both architectures on pull requests that touch the
image sources (`.github/workflows/build.yml`) and keeps the results as workflow
artifacts. It never releases or signs. The QEMU/WebAssembly job boots its pack
directly in headless Chromium. The x86 rootless job directly boots the actual
rootless smoke variant in QEMU and waits for its capability witness.

### Tests

The test stack is self-contained in this repository. It never starts `mvm`,
`mvmctl`, `bin/dev`, or another process from a sibling `mvm` checkout:

```sh
just test                         # Python unit tests + no-device contract
just bdd                          # executable Gherkin scenarios
just e2e-plan /path/to/artifacts # inspect QEMU + Firecracker plans
just e2e-qemu /path/to/artifacts
just e2e-firecracker /path/to/artifacts
just e2e-qemu-wasm /path/to/pack /path/to/chromium
just e2e-rootless-plan /path/to/rootless-smoke /path/to/runtime-overlay.ext4
just e2e-rootless-qemu /path/to/rootless-smoke /path/to/runtime-overlay.ext4
just e2e-rootless-firecracker /path/to/rootless-smoke /path/to/runtime-overlay.ext4
```

The native boot commands take the output of
`.#legacyPackages.x86_64-linux.qemu-wasm.qemu-wasm-smoke-image`, a directory
containing the QEMU `kernel.img`, Firecracker `vmlinux`, and shared
`rootfs.bin`. They launch the VMM directly and wait for
`QEMU-WASM-SMOKE-READY`. QEMU runs with `-nodefaults` and an explicit
block/serial/vsock device list. Firecracker receives the ELF kernel and a
generated configuration with block and vsock devices and no
`network-interfaces` section. The harness copies the rootfs out of the
immutable Nix store before mounting it read/write. Missing support for the
requested VMM/accelerator is a hard failure, not a silent skip; QEMU uses KVM
by default and accepts explicit `--accel tcg` for hardware-independent probes.

The rootless commands take the `rootless-tenant.smoke` output plus the canonical
`runtime-overlay.default` `overlay.ext4`, attach that overlay read-only as a
second block device, and wait for `MVM-ROOTLESS-READY`. Before emitting it, the
real sealed guest verifies that
its entrypoint is uid 1000, user/mount/PID namespaces work, its delegated
cgroup v2 subtree is writable, `crun` and `fuse-overlayfs` execute,
`/dev/net/tun` does not exist, and loopback is the only interface. QEMU and
Firecracker still receive explicit block/serial/vsock-only plans; the overlay
is a block device, and no `mvm` process or network device participates.

`features/standalone_images.feature` holds the human-readable BDD contract.
`scripts/check_no_network_devices.py` checks kernel, browser, and direct-VMM
definitions for forbidden network devices. Both architecture jobs also feed
the resolved builder, workload and rootless `.config` files to that checker, proving
Kconfig did not restore a built-in or modular network device. The fast suite
runs on every pull request; the browser boot is the hardware-independent E2E
lane, while native QEMU and Firecracker commands are for KVM-capable runners.

`.github/workflows/reproduce.yml` (`scripts/check-reproducible.sh`) rebuilds
the canonical `builder-vm`, `default-tenant`, and `rootless-tenant` roles on
both architectures. Nix rebuilds each final derivation instead of accepting
its registered output and fails if the bytes differ. The pinned inputs write
every filesystem and verity superblock from fixed seeds and UUIDs
([tinylabscom/mvm#3499](https://github.com/tinylabscom/mvm/issues/3499)), so a
second build of the same derivation gives the same bytes.
The lane never invokes `mvm` or compares against its retired in-tree image
recipes; those recipes are not a second source of truth.

### Advancing the mvm pin

1. Change the commit in the `mvm` input URL in `flake.nix` and run
   `nix flake lock` (use a full 40-character SHA of a commit on `mvm`'s `main`).
2. Run `scripts/check-source-drift.sh`. Anything `mvm` changed in the copied
   files since the last pin is reported; carry each change over, or record why
   not in `SOURCES.md`. Before moving, `--against <ref> --mvm-git-dir <clone>`
   shows the same report for a candidate commit.
3. If `mvm` bumped nixpkgs or microvm.nix in its image flakes, update the
   matching input here to the same revision; the drift check compares them.
4. Open a pull request. The build workflow rebuilds everything from the new
   commit.

## Working on images

Paired local development with `mvm` is a supported workflow, not an escape
hatch. The intended checkout layout is siblings:

```text
mvmco/
  mvm/
  mvm-images/
```

Nothing looks for that sibling. A build names the mvm checkout it uses, by
overriding the flake's `mvm` input with its canonical path:

```sh
mvm=$(cd ../mvm && pwd -P)
nix build .#legacyPackages.x86_64-linux.runtime-overlay.default \
  --override-input mvm "path:$mvm"
```

Every role builds this way, and every guest binary then comes from that
checkout, uncommitted changes included. For an unchanged tree the override
gives exactly the pinned derivations; `scripts/check-local-mvm-override.sh`
proves both halves and runs in CI. `MVM_WORKSPACE_PATH` stays refused: the
override is the one way in. Two things to know about `path:`:

- it copies the whole directory into the Nix store, ignored files included, so
  keep a large `target/` out of the checkout (set `CARGO_TARGET_DIR`) before
  overriding with it;
- it has no commit, so the default microVM's sidecar records an empty
  `generatorRev`, as `mvm`'s own `path:` builds do.

The builder VM's host binaries come from the same checkout:

```sh
scripts/install-host-toolchain.sh --mvm-checkout "$mvm"
scripts/build-host-binaries.sh --mvm-checkout "$mvm" x86_64   # prints MVM_HOST_BIN_DIR=...
MVM_HOST_BIN_DIR=... nix build .#legacyPackages.x86_64-linux.builder-vm.default \
  --impure --override-input mvm "path:$mvm"
```

`scripts/emit-local-manifest.py` then describes what was built, in the image-set
manifest schema `mvm` parses for a released set:

```sh
out=$(nix build .#legacyPackages.x86_64-linux.runtime-overlay.default \
  --override-input mvm "path:$mvm" --no-link --print-out-paths)
scripts/emit-local-manifest.py --mvm-checkout "$mvm" --arch x86_64 \
  --builder-cache-contract 1 --out /tmp/local-set \
  --artifact runtime_overlay ext4 "$out/overlay.ext4" \
  --artifact runtime_overlay verity_hash_tree "$out/overlay.verity" \
  --artifact runtime_overlay verity_root_hash "$out/overlay.roothash" \
  --capability runtime_overlay virtio_blk --capability runtime_overlay dm_verity
```

Workload artifacts use the profile-qualified emitter roles
`default_tenant_workload_{kernel,rootfs}` and
`rootless_tenant_workload_{kernel,rootfs}`. They serialize as, for example,
`{"workload_kernel":"rootless_tenant"}` so two profiles for one architecture
cannot collide or be confused by a consumer.

It copies each artifact into the new directory and writes `image-set.json`
beside them, recording both checkouts' commits and working-tree state (a dirty
tree by the fingerprint `mvm` computes), every artifact's digest and size, the
guest architecture and each artifact's role. Its producer is
`local_checkouts` and nothing else: no repository, workflow, tag, revocation
channel, pack hash or SBOM, so it can never be read as a release. `mvm`
parses it with the same parser as a released set, classifies it as the
`local-dev` trust tier, and refuses it when either checkout has moved on since
it was written. A release build of `mvmctl` refuses a local image checkout
outright, and so does a production admission.
