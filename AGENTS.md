# mvm-images repository instructions

## Ownership

This repository is the canonical source, builder and publisher for every base
image `mvm` consumes: kernels, workload root filesystems, builder images,
runtime overlays, initramfs images and bootstrap inputs. `mvm` is a consumer of
the generated, signed, digest-pinned image set. Do not add or preserve a second
canonical image definition or publication path in `mvm`.

Program source for the guest agent, runtime helpers and other `mvm` binaries
may remain in `mvm`; image composition and image-specific build/release policy
belong here. Transitional source mirrors and same-commit comparison lanes must
be removed when their extraction workstream completes rather than becoming a
permanent reverse dependency.

Workload-specific packages, services, configuration and tests belong in their
application or template repository. Do not create workload-named images or
kernels here. Express reusable needs as generic image capabilities.

## Workload base-image postures

Maintain two separate generic workload bases for both `x86_64` and `aarch64`:

- `default-tenant`: the smallest sealed image for one admitted workload. Its
  entrypoint is unprivileged and its kernel may omit namespace, cgroup and
  in-guest container-networking facilities.
- `rootless-tenant`: a sealed, unprivileged base for workloads that run their
  own rootless container stack or supervisor. It provides the generic
  capability floor for user/mount/PID/IPC/UTS namespaces, cgroup v2 delegation
  and controllers, container filesystems, PTYs, and writable-volume-backed
  container storage.

The rootless image must preserve the ordinary verified-boot, vsock, storage,
egress and admission boundaries. It must not contain Kubernetes or any other
consumer-specific package, service, configuration or name. Keep it separate
from `default-tenant`; do not widen the default kernel merely because a
rootless consumer needs more facilities.

## Permanent networking invariant

No workload or builder base image has a guest NIC. Ever. Do not add, enable,
attach or assume a NIC, TAP, TUN, bridge, veth pair, macvlan, SLIRP, passt,
vpnkit, CNI dataplane, raw-packet tunnel, guest NAT or guest firewall path.
Do not add a second networking implementation as a development fallback.

Guest loopback is only for local adapters. Proxy-aware TCP/UDP, controlled DNS,
mediated ping, typed connectors and declared ingress use the single
authenticated FlowMux session over vsock. The host endpoint owns external
sockets and applies signed policy. This must match the canonical contract in
the sibling checkout at `../mvm/public/src/content/docs/guides/networking.md`
and in the published
[`mvm` networking guide](https://github.com/tinylabscom/mvm/blob/main/public/src/content/docs/guides/networking.md).

A rootless consumer must use the NIC-less model (for example, host networking
inside its process namespace plus the injected loopback proxy). Never make an
otherwise incompatible CNI or container network work by adding guest devices.

## Image contract

- Publish roles atomically in one immutable image set for both guest
  architectures.
- Describe selection through manifest capabilities, architecture, boot
  protocol and artifact format—not host OS, backend name or workload name.
- Do not advertise or require a network-device capability. The networking
  contract is always loopback adapters plus FlowMux over vsock.
- Make `mvm` select and verify published roles through its image lock. A host
  or CLI change must not silently rebuild a canonical base image.
- Preserve reproducibility, source provenance, immutable action pins, signing
  identity, revocation behavior and cross-backend boot evidence.
- Any new role or capability must update the manifest producer, build and
  reproduction workflows, both-architecture tests, and `README.md`.

## Working tree

Preserve unrelated user changes. In particular, do not overwrite a local mvm
pin advance or regenerate lock files unless the task explicitly requires it.
