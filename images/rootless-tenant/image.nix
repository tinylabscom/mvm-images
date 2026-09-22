{
  description = "mvm generic rootless tenant image — sealed, NIC-less OCI capability floor";

  # Reuse the byte-for-byte tenant assembly and sealing path. The rootless flag
  # changes only the named image posture: its kernel delta, generic userspace
  # tools, image name, and the smoke variant used by direct-VMM CI.
  outputs = args:
    (import ../default-tenant/image.nix).outputs (args // { rootless = true; });
}
