# In-guest-datapath workload kernel — slim custom Linux 6.12.
#
# = the shared base (`kernel/base.nix`) + the dm-verity delta + the generic
# rootless capability floor (`kernel/rootless.nix` carries the same list) +
# an *in-guest datapath*: bridge/veth/virtual-tunnel adapters, a network
# namespace, and the netfilter/conntrack/iptables cluster to program them.
#
# This is a third, explicitly opt-in workload posture, not a widening of
# either base image:
#
#   - There is still NO guest NIC, no TAP, no TUN, no macvlan, no host-
#     facing device of any kind. `VIRTIO_NET`, `TUN`, `MACVLAN` stay
#     disabled — the permanent no-network-device contract is unchanged and
#     the resolved config is checked for it.
#   - The datapath is reachable only inside the guest. A consumer wires
#     cluster-internal adapters (bridge + veth pairs between network
#     namespaces); nothing it attaches reaches the host or the network —
#     every external byte still crosses the single authenticated FlowMux
#     session over vsock, and host ingress still arrives only at
#     pre-declared signed ports.
#   - The netfilter cluster programs that in-guest datapath (service
#     routing, port mapping between adapters). It is not a guest firewall:
#     there is no guest-facing interface for one to govern.
#
# Why a consumer wants this: an in-guest orchestrator that places workloads
# in their own network namespaces and connects them with a bridge/veth
# datapath — the generic shape of a single-node cluster's pod network. The
# name is the capability (an in-guest datapath), not any consumer.
#
# Kept disabled from the rootless posture's safe cuts: md-raid, loop,
# eBPF syscall interface, perf, checkpoint/restore, 9p/btrfs, IPv6
# tunnel/IPsec families, VLAN tagging. `NETFILTER` is NOT cut here the way
# `rootless.nix` cuts it — programming the datapath is the point of this
# posture.

{
  pkgs,
  base,
  optimizeForSize ? true,
}:

base.mkKernel {
  extraEnables = [
    # ── dm-verity + volume transport delta (same as workload/rootless) ──
    "MD"
    "BLK_DEV_DM"
    "DM_VERITY"
    "VIRTIO_FS"
    "FUSE_FS"
    "MIGRATION"
    "MEMORY_HOTPLUG"
    "MEMORY_HOTREMOVE"
    "SPARSEMEM_VMEMMAP"
    "ZONE_DEVICE"
    "FS_DAX"
    "FUSE_DAX"
    "IPV6"

    # ── namespace floor (rootless.nix's list + NET_NS) ──
    # USER_NS is what makes an unprivileged in-guest orchestrator possible;
    # NET_NS is the delta this posture exists for: every workload gets its
    # own network namespace on the in-guest datapath.
    "NAMESPACES"
    "UTS_NS"
    "IPC_NS"
    "USER_NS"
    "PID_NS"
    "NET_NS"

    # ── cgroup v2 hierarchy + controllers ──
    "CGROUPS"
    "MEMCG"
    "BLK_CGROUP"
    "CGROUP_SCHED"
    "FAIR_GROUP_SCHED"
    "CGROUP_PIDS"
    "CGROUP_FREEZER"
    "CGROUP_CPUACCT"
    "CPUSETS"

    # ── rootless runtime floor ──
    "UNIX98_PTYS"
    "INOTIFY_USER"
    "FANOTIFY"

    # ── in-guest datapath ──
    # NETDEVICES is the umbrella menu bridge/veth live under; base.nix
    # disables it (with VIRTIO_NET/TUN) as part of the no-network-device
    # contract. This posture re-enables the umbrella and the cluster-internal
    # adapters while VIRTIO_NET/TUN/MACVLAN stay cut — no host-facing device
    # exists, so the contract's guarantee is preserved, not weakened.
    "NETDEVICES"
    "BRIDGE"
    "VETH"
    "VXLAN"
    "BRIDGE_NETFILTER"

    # ── the netfilter cluster that programs the datapath ──
    # rootless.nix force-drops NETFILTER; this posture needs the tables an
    # in-guest service router installs (filter/NAT/masquerade between
    # adapters, conntrack for flow state). In-guest-only: the datapath has
    # no upstream interface.
    "NETFILTER"
    "NETFILTER_ADVANCED"
    "NETFILTER_XTABLES"
    "NF_CONNTRACK"
    "IP_NF_IPTABLES"
    "IP_NF_FILTER"
    "IP_NF_NAT"
    "NF_NAT_MASQUERADE"
    "IP_NF_TARGET_MASQUERADE"

    # ── container runtime basics base.nix cuts ──
    # POSIX mqueue mounts for containers in their own IPC namespaces.
    "POSIX_MQUEUE"
  ]
  ++ pkgs.lib.optionals optimizeForSize [ "CC_OPTIMIZE_FOR_SIZE" ];

  # Same safe cuts as the rootless posture, MINUS NETFILTER (kept, above).
  extraDisables = [
    # IPv6 itself selects only the SHA-1 library; every one of these is an
    # IPsec-for-v6 option that `olddefconfig` would enable alongside it and
    # that drags in the XFRM transform framework. None is reachable on the
    # in-guest datapath — there is no tunnel endpoint and no upstream — and
    # XFRM/XFRM_ALGO/XFRM_USER stay in the required-disable set, so the
    # config guard is what proves they are absent rather than a comment.
    "IPV6_MIP6"
    "IPV6_VTI"
    "INET6_AH"
    "INET6_ESP"
    "INET6_ESP_OFFLOAD"
    "INET6_ESPINTCP"
    "INET6_IPCOMP"
    "INET6_XFRM_TUNNEL"
    "INET6_TUNNEL"
    "IPV6_SIT"
    "BLK_DEV_MD"
    "BLK_DEV_LOOP"
    "BPF_SYSCALL"
    "PERF_EVENTS"
    "PROFILING"
    "IKCONFIG"
    "IKCONFIG_PROC"
    "CHECKPOINT_RESTORE"
    "NET_9P"
    "BTRFS_FS"
    "GNSS"
    "VLAN_8021Q"
  ]
  ++ pkgs.lib.optionals optimizeForSize [ "CC_OPTIMIZE_FOR_PERFORMANCE" ];

  # No required cuts: unlike rootless.nix (which requires NET_NS off), this
  # posture's reason to exist is NET_NS on.
  requiredExtraDisables = [ ];

  # BRIDGE/NETDEVICES/VETH are base required-disables (the no-network-device
  # contract, scoped to base postures) and POSIX_MQUEUE is a base best-effort
  # cut. The exemptions re-enable exactly the in-guest datapath symbols; the
  # base contract's host-facing devices (VIRTIO_NET, TUN, MACVLAN) stay
  # disabled and are asserted by the repository's contract check.
  disableExemptions = [
    "BRIDGE"
    "NETDEVICES"
    "POSIX_MQUEUE"
    "VETH"
  ];
}
