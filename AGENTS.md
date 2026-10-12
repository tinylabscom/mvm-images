# mvm-images repository instructions

## Ownership

Build only the canonical Linux layer: builder/default/rootless kernels,
sealed generic workload bases, builder VM image, Stage 0 seeds and
QEMU/WebAssembly smoke pack. Nix reproduces local definitions. No mvm source
input, source mirror, source-drift or pin-advance procedure belongs here.
Never compare outputs with retired in-tree mvm image recipes.

The complete signed runtime ships version-locked with mvmctl: overlay,
initramfs, SDK sidecar, guest agent/helpers, mvm-setpriv and GPU shims.
Base images contain no mvm binaries or mvm-authored /init. Do not add runtime
roles. This repository is never part of mvm's merge queue; image events
follow Linux package/kernel/toolchain changes.

## Base and publication contracts

Maintain separate generic default-tenant/rootless-tenant profiles on x86_64
and aarch64. Select matching kernel/rootfs profiles by generic capabilities,
architecture, boot protocol and format, not backend/host OS/workload names.
Rootless supplies namespaces, delegated cgroup v2/controllers, PTYs,
container filesystems and writable-volume-backed storage without widening
the default kernel. Application packages/services/configuration belong in
application or template repositories.

New sets use schema_version 3/base_image_contract 1. Bases emit exact bytes
`1\n` to `/etc/mvm/base-image-contract` from images/base-image-contract.nix.
Fixed identities are 0/901/987/988/989; builder adds UID 902 and boot ABI 2.
Keep all 17 producer Linux members including dev variants; 13 is only the
consumer minimum. Publish both architectures atomically and immutably.
Preserve action pins, provenance, cosign identity, revocation and
reproduction. SLSA subjects derive from the assembler member table for
artifacts, pack manifests, SBOMs and root; never duplicate that list.
Attestation is not reproduction.

This is staged migration, not release evidence. No compatible CLI v0.23.1 is
published yet. Compatible consumer lands before CLI tag; CLI and signed full
runtime must actually publish and installed boots be witnessed before
Linux-only publication. Only afterward may mvm advance images.lock.
Historical signed schema 2 sets/current lock remain untouched. No artificial
set-version cutoff, invented release/run IDs or manual bypass.

## Permanent networking invariant

No guest NIC, TAP, TUN, bridge, veth, macvlan, SLIRP, passt, vpnkit, CNI
dataplane, raw-packet tunnel, guest NAT or guest firewall path. No fallback.
Loopback serves local adapters only. Proxy-aware TCP/UDP, controlled DNS,
mediated ping, typed connectors and ingress use a single authenticated
FlowMux session over vsock; host endpoints own sockets and signed policy.
Rootless preserves verified boot, storage, egress and admission.
The canonical contract is the sibling
`../mvm/public/src/content/docs/guides/networking.md` and published
[networking guide](https://github.com/tinylabscom/mvm/blob/main/public/src/content/docs/guides/networking.md).

## Working tree and validation

Preserve unrelated changes; do not regenerate locks unless requested.
Host Python/static/just checks are fast validation, not Linux build/boot
evidence. Nix and real boots require a configured Linux execution boundary.
Report missing environment accurately. Tests never start mvm, mvmctl or
bin/dev. Boot tests invoke VMMs directly with explicit devices, vsock, no NIC,
a probe-owned init and no runtime overlay or mvm-built bytes. Rootless smoke
directly witnesses UID 901, namespaces, delegated cgroup v2, crun/
fuse-overlayfs and loopback-only networking.

New Linux roles/capabilities update README, producer, both-architecture tests,
build and reproduction workflows. Instruction edits invalidate detached
Sigstore bundles; require real authorized re-signing, never forge signatures.
