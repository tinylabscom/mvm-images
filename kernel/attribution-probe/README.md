# Partial native hook viability probe — not production semantics

This image-owned development guest tests the actual connect4/connect6 verifier,
attach path and BPF LSM `file_receive` hook on the experimental Linux 6.12.111
kernel. It is not PS13 completion and **must not be integrated into production**
on the strength of a partial-pass marker. No `mvm` checkout, binary, runtime,
overlay, manifest or image lock participates. No production recipe changes.

## Deliberately restrictive policy

The trusted PID 1 creates a root-owned, mode 0700, nondelegated invocation
cgroup. Before any uid-1000 tool starts, it irreversibly sets and reads back
`kernel.unprivileged_bpf_disabled=1`, loads the BPF object, attaches both connect
programs at the cgroup root, and attaches the LSM program. The map initially
contains zero (deny); admission then selects one kernel cgroup ID, never a PID
or a process-inspection result. Only that exact cgroup may connect. Descendant
processes inherit its membership; nested/delegated cgroups are not supported.

**Every socket received through `file_receive` is refused, including same-
invocation transfers.** This is a deliberately over-restrictive viability policy,
not approved final FD-transfer semantics. Non-socket file transfers are not
tested. The program uses CO-RE `file.f_inode.i_mode` and rejects unreadable mode;
it does not guess offsets or cast a `struct file` into a BPF socket. Invocation
identity comes from `bpf_get_current_cgroup_id`, not procfs. A map of counters
must witness actual allowed/denied connect hooks and socket-specific LSM denies:
an attach success or a denial caused by an unrelated error cannot pass alone.

## Executed scenarios if the guest passes

* Real IPv4 and IPv6 TCP loopback connections from the admitted tool and its
  forked descendant; the supervisor accepts all four connections.
* Outside-cgroup connect attempts return EPERM for both families, before
  admission and while the legitimate invocation is admitted.
* The admitted tool sends both connected socket FDs using SCM_RIGHTS to a
  separate outside-cgroup uid-1000 tool. Linux must deliver payload but truncate
  ancillary rights (`MSG_CTRUNC`, no FD). The LSM socket-denial counter must
  increment twice.
* Every tool is separately executed from the sealed, root-owned **0551** file
  `/tool`, checks that reading that file fails, sets and verifies nondumpable,
  and checks zero effective, permitted, inheritable, bounding and ambient
  capabilities. Admission still works through the kernel cgroup helper.
* Unprivileged BPF map creation returns EPERM. Opening either root or invocation
  `cgroup.procs` for writing returns EACCES; no cgroup delegation is performed.
* After children exit, admission is cleared, the cgroup is removed and the same
  path recreated. A fresh process inside the recreated cgroup cannot connect.
  This does not simulate a kernel cgroup-ID wraparound.
* Missing and malformed ELF objects are refused by libbpf before workload
  launch. The actual program's load and every attach are mandatory: any failure
  aborts the guest, never launches an unprotected tool and never prints PASS.

The main watchdog is 60 seconds; each executed tool has a 20-second watchdog.
PID 1 failures exit/panic and emit `ATTRIBUTION-PROBE:FAIL:...` with errno.
The host enforces an independent timeout and requires a clean VMM exit and both
exact PASS/UNSUPPORTED markers. Diagnostics and verifier messages remain in the
serial log. An unavailable BTF, syscall, helper, verifier operation, attach type
or hook is a failing result to investigate, not a skip or fallback.

## Exact 6.12 assumptions to discover at runtime

The experimental kernel inherits `CONFIG_SECURITYFS=n`. Active LSM membership
is checked using `lsm_list_modules`, not `/sys/kernel/security/lsm`. In upstream
v6.12.111 [`security/Makefile`](https://github.com/gregkh/linux/blob/v6.12.111/security/Makefile)
builds `lsm_syscalls.o` under `CONFIG_SECURITY`, already required by the config
contract; there is no additional `LSM_SYSCALL` switch.
[`lsm_syscalls.c`](https://github.com/gregkh/linux/blob/v6.12.111/security/lsm_syscalls.c)
takes `u64 *ids`, `u32 *size` (bytes), flags zero, returning the number of IDs.
`LSM_ID_BPF` is 109 in the matching UAPI. The pinned Nix Linux headers supply
both architectures' syscall numbers; there is no hardcoded fallback.

The unresolved runtime gates are BPF socket-address helper availability,
CO-RE relocation against the built kernel BTF, the LSM trampoline signature,
verifier acceptance, attach permission, exact SCM detachment behavior, and
cgroup identity matching. Compilation and static tests do not establish them.

## Explicitly unsupported, even after partial PASS

* Lifetime-bound socket ownership and revocation of already-connected sockets;
  additional socket-use hooks; cross-invocation reuse under concurrent teardown.
* FD inheritance policy, ptrace/pidfd avenues, established socket use after
  revocation, socket cloning, UDP, or transports other than this TCP probe.
* Claim protocol, an authenticated connector, external networking, and actual
  executable identity/content inspection. The 0551/nondumpable test establishes
  **cgroup observability**, not executable attestation.
* Verifier-rejected instruction injection, forced attach failure, resource-
  exhaustion recovery, performance/overhead or startup benchmarks.
* Production verified boot: this standalone test ext2 image is mounted read-only,
  but is not a signed or dm-verity-authenticated deployment image.

## Build and boot (Linux only)

Do not evaluate or build Nix on macOS. On each matching Linux architecture:

```sh
system=x86_64-linux # repeat separately with aarch64-linux
nix build --no-write-lock-file \
  "path:./kernel#packages.${system}.experimental-attribution-probe" \
  --out-link result-attribution-probe
nix build --no-write-lock-file \
  "path:./kernel#packages.${system}.experimental-attribution-probe-rootfs" \
  --out-link result-attribution-probe-rootfs
```

The first output has `bin/init` and `share/probe.bpf.o`; the second has
`rootfs.ext2`, including the native executable and its pinned Nix runtime
closure. The BPF ELF retains BTF/CO-RE metadata. Root ownership is normalized
inside the ext2 image without privileged build operations. The guest mounts
this journal-free ext2 format through the baseline's built-in ext4 driver;
it does not require a separately enabled ext2 driver or filesystem alias.

Boot against the matching experimental kernel from the **same revision**.
For QEMU x86 use `bzImage` (not the artifact's extracted `vmlinux`);
for arm64 use `Image`:

```sh
python3 scripts/experimental_attribution_boot.py \
  --arch x86_64 --kernel result-attribution-kernel-x86_64-linux/bzImage \
  --rootfs result-attribution-probe-rootfs/rootfs.ext2 --accel kvm --timeout 90
python3 scripts/experimental_attribution_boot.py \
  --arch aarch64 --kernel result-attribution-kernel-aarch64-linux/Image \
  --rootfs result-attribution-probe-rootfs/rootfs.ext2 --accel kvm --timeout 90
```

This is direct QEMU, explicit block/serial/vsock devices, no NIC. An accessible
`/dev/vhost-vsock` is mandatory, even for explicit `--accel tcg`; lack of vsock
is a failure, never permission to omit it. KVM also requires `/dev/kvm` access.
Use `--dry-run` to inspect either architecture's plan without booting.

Pure checks available on macOS (not runtime evidence):

```sh
python3 -m unittest discover -s scripts/tests -p 'test_attribution_probe.py'
python3 -m unittest discover -s scripts/tests -p 'test_experimental_attribution.py'
```
