# mvm-images guidance

Read and follow `AGENTS.md` for the complete repository workflow.

The essential architecture is:

- `mvm-images` builds and publishes the Linux layer only: the kernels, the
  base `default-tenant` and `rootless-tenant` root filesystems, the builder VM
  image and the Stage 0 seeds, as one reproduced, signed image set.
- `mvm` owns the guest runtime (runtime overlay, initramfs, SDK sidecar, guest
  agent and helpers, `mvm-setpriv`, GPU shims). It ships with each `mvmctl`
  release and `mvmctl` assembles it at boot
  ([tinylabscom/mvm#4100](https://github.com/tinylabscom/mvm/issues/4100)).
- Today this repository still takes `mvm` as a pinned flake input. From it, it
  builds the guest-runtime roles and the builder's baked `mvm-setpriv`, and
  composes both tenant root filesystems with `mkGuest`, mvm binaries and
  `/init` included. Removing that is
  [tinylabscom/mvm-images#49](https://github.com/tinylabscom/mvm-images/issues/49).
  Add no new dependency on `mvm`'s source; shrink the existing ones.
- Base root filesystems must not carry mvm binaries or an mvm-authored
  `/init`. The existing images still do until #49 lands; do not add more.
- Image sets are to gain a build-provenance attestation in SLSA's format; they
  carry none today
  ([tinylabscom/mvm-images#50](https://github.com/tinylabscom/mvm-images/issues/50)).
- This repository is never part of `mvm`'s merge queue. An image set changes
  when a kernel, package or toolchain moves, not when an `mvm` change merges.
  `mvm`'s merge-group lane still builds the runtime overlay from a checkout
  here until [tinylabscom/mvm#4108](https://github.com/tinylabscom/mvm/issues/4108);
  add no other such edge.
- `mvm` consumes signed, digest-pinned generated image sets; it does not keep a
  second image source or canonical build path.
- Reproducibility rebuilds the canonical image definitions here; never compare
  output bytes against retired in-tree image recipes in `mvm`.
- Maintain distinct `default-tenant` and generic `rootless-tenant` base-image
  postures for both guest architectures.
- Keep image-set workload roles profile-qualified. A manifest identifies the
  default and rootless kernel/rootfs pairs separately for each architecture so
  a consumer cannot mix the two profiles.
- `rootless-tenant` supplies reusable rootless-container capabilities while
  preserving verified boot and the normal vsock, storage, egress and admission
  boundaries.
- No guest NIC, TAP, TUN, bridge, veth, macvlan, SLIRP, passt, vpnkit, CNI
  dataplane, raw-packet tunnel, guest NAT or guest firewall path is ever
  permitted. Do not add a networking-device fallback for compatibility.
- Guest loopback adapters connect proxy-aware traffic, DNS, mediated ping,
  typed connectors and declared ingress to the single authenticated FlowMux
  session over vsock. The host endpoint owns external sockets and policy.
- Never put Kubernetes or another consumer-specific package, service,
  configuration, image name or kernel name in this repository. Those belong
  in the relevant application or template repository.
- Select images through generic manifest capabilities, not workload names or
  backend-specific forks.

The canonical networking contract is
`../mvm/public/src/content/docs/guides/networking.md` in a sibling checkout and
the published
[`mvm` networking guide](https://github.com/tinylabscom/mvm/blob/main/public/src/content/docs/guides/networking.md).

Do not overwrite unrelated working-tree changes or regenerate pins and locks
unless the task explicitly requires it.

The test suite is standalone: never run `mvm`, `mvmctl` or `bin/dev` from a
BDD or E2E test. Direct boot tests invoke QEMU, Firecracker or the browser VMM
against artifacts built here, with explicit device lists, vsock, and no
network interface.
The rootless smoke must additionally witness unprivileged namespace and cgroup
operation plus `crun`/`fuse-overlayfs`, while asserting that loopback is the
only interface and `/dev/net/tun` is absent.
