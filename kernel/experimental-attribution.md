# Development-only invocation-attribution kernel spike

The [native partial hook probe](./attribution-probe/README.md) adds isolated
development executable/rootfs outputs and a direct bounded QEMU test. Its
blanket socket-transfer refusal is not approved production policy; its partial
PASS does not establish full PS13 attribution or lifecycle security.

This is an **experimental feasibility configuration**, not a production
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
The wrapper removes its explicit enables from the production disable lists
and its explicit disables from the inherited enable list. All unrelated base
guards survive. After `olddefconfig`, both the existing
enable guard and the experiment's checker run; a dropped required symbol,
module instead of built-in, restored forbidden option, or wrong LSM list fails
the derivation. Kconfig-selected dependencies are left to Kconfig, not fabricated.

### LSM attach failure: load support is not trampoline support

[Run 37983480539](https://github.com/tinylabscom/mvm-images/actions/runs/37983480539),
revision `a8edb57f99273afa8b7e38a931c6e1e27daa171d`, built both kernels/rootfs
and reached `receive` attachment after object load, but x86_64 returned
`EBUSY` (16) and arm64 `ENOTSUPP` (524). The downloaded
`experimental-config-{x86_64,aarch64}-linux/kernel.config` artifacts both have
`BPF_LSM`, `BPF_EVENTS`, `BPF_JIT`, `BPF_JIT_DEFAULT_ON` and `FTRACE` set to
`y`, but `FUNCTION_TRACER` unset and no enabled `DYNAMIC_FTRACE*` symbols.
Both use GCC and `CC_OPTIMIZE_FOR_SIZE=y`. This is not evidence of an occupied
LSM hook or missing JIT; the pinned source explains the architecture-specific
errors:

* Without `DYNAMIC_FTRACE`,
  [`ftrace_location()`](https://github.com/gregkh/linux/blob/v6.12.111/include/linux/ftrace.h#L954-L966)
  is a stub returning zero.
  [`register_fentry()`](https://github.com/gregkh/linux/blob/v6.12.111/kernel/bpf/trampoline.c#L209-L232)
  therefore bypasses `register_ftrace_direct()` and calls
  `bpf_arch_text_poke(ip, BPF_MOD_CALL, NULL, new_addr)`.
* On x86,
  [`__bpf_arch_text_poke()`](https://github.com/gregkh/linux/blob/v6.12.111/arch/x86/net/bpf_jit_comp.c#L575-L615)
  expects a five-byte NOP when `old_addr` is NULL. The instruction comparison
  at lines 604–607 returns `-EBUSY` on a mismatch. With function-entry tracing
  disabled, the LSM kernel function has no promised patchable entry.
* On arm64,
  [`bpf_arch_text_poke()`](https://github.com/gregkh/linux/blob/v6.12.111/arch/arm64/net/bpf_jit_comp.c#L2566-L2587)
  returns `-ENOTSUPP` for an address not belonging to BPF text. Its comment
  explicitly requires ftrace for kernel functions, including this LSM target.

These are source/config-based error-path diagnoses, not an instrumented kernel
stack trace. The minimal shared attach contract now requests and checks
`FUNCTION_TRACER`, `DYNAMIC_FTRACE`, `DYNAMIC_FTRACE_WITH_ARGS`, and
`DYNAMIC_FTRACE_WITH_DIRECT_CALLS`, all built-in.
[`kernel/trace/Kconfig`](https://github.com/gregkh/linux/blob/v6.12.111/kernel/trace/Kconfig#L245-L285)
requires function tracing for dynamic ftrace; direct calls require the
architecture capability and either REGS or ARGS. Merely enabling function
tracing is insufficient:
[`trampoline.c`](https://github.com/gregkh/linux/blob/v6.12.111/kernel/bpf/trampoline.c#L155-L164)
allocates `tr->fops` only with direct calls; `register_fentry()` returns
`-ENOTSUPP` if a traced location exists but `tr->fops` does not.

In particular,
[`arm64/Kconfig`](https://github.com/gregkh/linux/blob/v6.12.111/arch/arm64/Kconfig#L203-L213)
selects direct-call capability only with ARGS and CALL_OPS; CALL_OPS requires
ARGS, no Clang CFI, and `(CC_IS_CLANG || !CC_OPTIMIZE_FOR_SIZE)`. The archived
arm64 config already has `GCC_SUPPORTS_DYNAMIC_FTRACE_WITH_ARGS=y`, but GCC's
size optimization blocks CALL_OPS. The experiment therefore switches its shared
optimization choice to `CC_OPTIMIZE_FOR_PERFORMANCE=y` and
`CC_OPTIMIZE_FOR_SIZE=n`. This deliberately keeps one two-architecture contract,
rather than adding architecture-specific overrides; x86 does not itself
require that optimization switch. Kconfig must derive the architecture
capabilities and arm64 CALL_OPS; no `HAVE_*` capability is forced.

Function-entry instrumentation and performance optimization may increase image
size and boot cost. They affect only the experimental wrapper, not production
recipes. JIT runtime availability, ftrace initialization and successful direct
attachment remain runtime gates; neither the old successful load nor the new
static contract proves them. Fresh Linux CI must resolve both configurations,
rebuild, attach `receive`, and execute the SCM_RIGHTS denial/counter checks before
claiming even the partial hook PASS. No new boot or performance result is claimed.

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
* x86_64 defconfig requests `BLK_DEV_IO_TRACE`. Enabling the tracing
  prerequisites makes that request effective again, and it selects `DEBUG_FS`.
  Explicitly disable block-I/O tracing in the experiment; it is unrelated to
  attribution and must not defeat the inherited debugfs prohibition.
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

The isolated `Experimental attribution kernel` Actions workflow runs on pushes
to `experiment/kernel-attribution` or manual dispatch. Native Linux runners
evaluate the kernel derivation and generate/check each architecture's resolved
config before building either experimental kernel. It checks kernel formats
and retains the images, resolved configs, source commits and SHA-256 hashes
as workflow artifacts for seven days. It creates no release and is not called
by the publication workflows. Successful compilation alone does not prove
bootability, hook behavior, enforcement, or performance.

Native Linux jobs compile the standalone probe rootfs before the kernel so
probe compiler errors fail quickly. After both builds pass, boot jobs verify
the retained kernel/rootfs hashes and run QEMU with explicit TCG and mandatory vsock.
This is a functional hook-viability gate, not a boot-latency measurement.
Guest logs retain the exact partial-success and unsupported-coverage markers;
the probe's blanket socket-FD receipt denial is not a production policy.

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
