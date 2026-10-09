# Development-only invocation-attribution kernel spike

This is an **unbuilt, unbooted feasibility configuration**, not a production
profile, release role, or security guarantee. It wraps `workload.nix`, not
`rootless.nix`, and reuses `base.nix`'s exact Linux **6.12.111** source and the
existing `kernel/flake.lock` toolchain. No production recipe, image-set member,
manifest capability, rootfs, runtime, or lock changes. It adds no mvm-source
dependency. Its only outputs are explicitly named development packages for
`aarch64-linux` and `x86_64-linux`; release scripts do not select them.

## Dependency audit against the pinned source

The following are from upstream stable **v6.12.111**, not inferred from a
distribution config. Links name the exact tag; the actual build still uses the
hash-pinned source in `base.nix`.

| Requirement | Source and necessary configuration |
| --- | --- |
| Unified cgroup hierarchy | [`init/Kconfig`](https://github.com/gregkh/linux/blob/v6.12.111/init/Kconfig): `CGROUPS` selects `KERNFS`; cgroup v2 has no separate `CGROUP_V2` option. `CGROUP_BPF` lives inside the cgroup menu, depends on `BPF_SYSCALL`, selects `SOCK_CGROUP_DATA`. No namespaces or resource controllers are required. |
| connect4/connect6 | [`include/linux/bpf_types.h`](https://github.com/gregkh/linux/blob/v6.12.111/include/linux/bpf_types.h) gates `BPF_PROG_TYPE_CGROUP_SOCK_ADDR` on `CONFIG_CGROUP_BPF`; [`net/core/filter.c`](https://github.com/gregkh/linux/blob/v6.12.111/net/core/filter.c) implements its verifier and attach types. `INET` and `IPV6` retain both socket families. There are no separate `CONFIG_CONNECT4/6` flags and no device/firewall requirement. |
| syscall and LSM | [`kernel/bpf/Kconfig`](https://github.com/gregkh/linux/blob/v6.12.111/kernel/bpf/Kconfig): `BPF_SYSCALL` selects `BPF`, `IRQ_WORK`, tasks RCU, `BINARY_PRINTF`, and (with NET) socket-message/XGRESS/page-pool support. `BPF_LSM` **requires `BPF_EVENTS`, `BPF_SYSCALL`, `SECURITY`, and `BPF_JIT`**. |
| BPF event prerequisites | [`kernel/trace/Kconfig`](https://github.com/gregkh/linux/blob/v6.12.111/kernel/trace/Kconfig): `BPF_EVENTS` is inside `FTRACE`, requires `(KPROBE_EVENTS || UPROBE_EVENTS) && PERF_EVENTS` plus `BPF_SYSCALL`. Choose KPROBES, not UPROBES: `KPROBE_EVENTS` requires `KPROBES` and architecture register/stack access and selects tracing/probe infrastructure. [`arch/Kconfig`](https://github.com/gregkh/linux/blob/v6.12.111/arch/Kconfig): `KPROBES` selects `KALLSYMS`, `EXECMEM`, tasks RCU. Both target architectures support this path; resolution must still prove it. |
| BTF | [`lib/Kconfig.debug`](https://github.com/gregkh/linux/blob/v6.12.111/lib/Kconfig.debug): debug-information choice requires `DEBUG_KERNEL`; choose DWARF4, selecting `DEBUG_INFO`. `DEBUG_INFO_BTF` requires `BPF_SYSCALL`, pahole >=116, no split/reduced debug info, no RANDSTRUCT unless compile-test. DWARF4 avoids the DWARF5 pahole >=121 alternative. Add pinned build-platform pahole to the **config** derivation, because Kconfig probes it; the existing nixpkgs kernel builder already supplies it for compilation. |
| Landlock and activation list | [`security/Kconfig`](https://github.com/gregkh/linux/blob/v6.12.111/security/Kconfig): `SECURITY` needs `SYSFS` and `MULTIUSER`; [`security/landlock/Kconfig`](https://github.com/gregkh/linux/blob/v6.12.111/security/landlock/Kconfig) selects `SECURITY_NETWORK` and `SECURITY_PATH`. Resolve `LSM="landlock,bpf"` explicitly. A boot `lsm=` parameter can override this; runtime activation must be checked separately. |

`experimental-attribution.json` is the single request/assertion contract.
The wrapper removes only its explicit enables from the production disable
lists. All other base guards survive. After `olddefconfig`, both the existing
enable guard and the experiment's checker run; a dropped required symbol,
module instead of built-in, restored forbidden option, or wrong LSM list fails
the derivation. Kconfig-selected dependencies are left to Kconfig, not fabricated.

## Deliberate exposure and limitations

* An interpreter-only full BPF LSM experiment is **impossible** with this pinned
  Kconfig: JIT is mandatory, not a performance optimization selected casually.
  Only the experiment enables it. This adds native-code generation, executable
  memory, verifier/loader, tracing, perf, and BTF surfaces. There is no security
  or performance equivalence claim. `BPF_JIT_ALWAYS_ON` remains off;
  architecture defaults may enable JIT at runtime.
* `BPF_UNPRIV_DEFAULT_OFF=y` initializes `kernel.unprivileged_bpf_disabled=2`,
  which privileged code can reverse. Before starting untrusted work, the
  runtime probe must set it to **1**, verify readback, and prevent privileged
  workload access. Configuration alone cannot guarantee that lifecycle.
* Unlike the production baseline, this experiment explicitly disables
  `IO_URING`: ring-FD transfer can bypass a simplistic SCM_RIGHTS socket guard.
  This may constrain applications; it is not a production compatibility change.
* No namespace/container posture is added. Resource controllers are disabled;
  only the cgroup hierarchy and BPF hook support are required. No NIC, TAP,
  TUN, veth, bridge, macvlan, netfilter, packet socket, or traffic-control path.
  Loopback and authenticated FlowMux/vsock remain the networking boundary.
* Config capability does not establish attribution or prove `file_receive`
  semantics. Real production-shaped BPF programs must be compiled, loaded,
  attached, and exercised for both connect families, cgroup inheritance,
  socket/FD transfer and reuse, activation ordering, and failure behavior.
  Confirm BTF availability and active LSMs in the running guest, not just this
  file. An agent-readiness boot benchmark **does not measure attribution
  activation**. Measure startup activation and steady-state overhead separately.

## Commands and remaining validation

The isolated `Experimental attribution config` Actions workflow runs on pushes
to `experiment/kernel-attribution` or manual dispatch. Native Linux runners
evaluate the kernel derivation and generate/check each architecture's resolved
config, retaining configuration evidence for seven days. It builds no kernel
image, creates no release, and is not called by the publication workflows.

Pure native checks (including on macOS; no Nix evaluation required):

```sh
python3 -m unittest discover -s scripts/tests -p 'test_experimental_attribution.py'
```

On a **Linux** machine with Nix and matching local/remote builders, from the repo
root (do not run these Nix commands on macOS):

```sh
for system in x86_64-linux aarch64-linux; do
  nix build --no-write-lock-file \
    "path:./kernel#packages.${system}.experimental-attribution-configfile" \
    --out-link "result-attribution-config-${system}"
  python3 kernel/check-experimental-attribution.py \
    --contract kernel/experimental-attribution.json \
    "result-attribution-config-${system}"
  nix build --no-write-lock-file \
    "path:./kernel#packages.${system}.experimental-attribution-vmlinux" \
    --out-link "result-attribution-kernel-${system}"
done
```

`path:./kernel` includes the new files even before they are Git-tracked. This is
not cross-compilation: each target needs a matching Linux builder or explicitly
configured emulation. x86 output contains `bzImage` and extracted ELF `vmlinux`;
arm64 output contains `Image`. The config output is a file, not a directory.
No experimental manifest or image-set is generated.

Native contract tests freeze pre-spike production recipe/output definitions
(subtracting only the two marked additive development blocks), locks and
publication entrypoints. Those snapshots require explicit review if production
definitions intentionally change later. They prove source isolation, not
derivation identity or kernel bytes. Linux Nix evaluation/build, both resolved
configs, compiled artifacts, boot, program loading and enforcement probes remain
required before drawing feasibility conclusions. Do not publish these artifacts.
