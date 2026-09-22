# Generic rootless workload kernel.
#
# This is the ordinary verified workload kernel plus the namespace, cgroup v2,
# PTY and filesystem-notification floor needed by an unprivileged OCI runtime
# or supervisor. It intentionally does not enable NET_NS or any guest network
# device: rootless workloads use the shared loopback namespace and FlowMux over
# AF_VSOCK exactly like every other mvm workload.

{ pkgs, base }:

import ./workload.nix {
  inherit pkgs base;
  rootless = true;
}
