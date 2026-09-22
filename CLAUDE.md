# mvm-images guidance

Read and follow `AGENTS.md` for the complete repository workflow.

The essential architecture is:

- `mvm-images` owns and publishes every canonical base image needed by `mvm`.
- `mvm` consumes signed, digest-pinned generated image sets; it does not keep a
  second image source or canonical build path.
- Reproducibility rebuilds the canonical image definitions here; never compare
  output bytes against retired in-tree image recipes in `mvm`.
- Maintain distinct `default-tenant` and generic `rootless-tenant` base-image
  postures for both guest architectures.
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
