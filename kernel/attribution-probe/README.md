# Development TCP lifecycle and egress-label bridge — not production semantics

This image-owned development guest extends the connect4/connect6 and
`file_receive` partial pass from [run 37987291610](https://github.com/tinylabscom/mvm-images/actions/runs/37987291610)
at `305fb49` with socket-label generation and actual-use/revocation gates on
experimental Linux **6.12.111**. The earlier run does **not** establish these
new gates. The lifecycle extension at `536e20c` subsequently passed both
architectures in [run 37992460204](https://github.com/tinylabscom/mvm-images/actions/runs/37992460204).
That run does **not** establish the bridge gates added here. It is not PS13
completion and **must not be integrated into
production** on the strength of a partial-pass marker. No `mvm` checkout,
binary, runtime, overlay, manifest or image lock participates. No production
recipe, kernel configuration, output, NIC or vsock changes.

## Deliberately restrictive policy

The trusted PID 1 creates root-owned, mode 0700, nondelegated invocation
cgroups. Before any uid-1000 tool starts, it irreversibly sets and reads back
`kernel.unprivileged_bpf_disabled=1`, loads the BPF object, attaches both connect
programs at the cgroup root, and attaches all three LSM programs. The admission
hash map is initially empty (deny). Its key is the kernel cgroup ID and its
value is `{label, generation, active}`; no process scan establishes identity.
Only an active exact cgroup may connect to the lifecycle endpoint. The bridge
also allows the normal loopback port 1080 without admission; its narrow
unbound SEND rule is described below. Descendants inherit process membership;
nested/delegated invocation cgroups are not supported.

Non-sleepable `socket_connect` creates a `BPF_MAP_TYPE_SK_STORAGE` snapshot
`{cgroup, label, generation, infrastructure=0}`. Repeated connect cannot replace
an existing label; it must match the current generation. Sleepable-capable
`socket_sendmsg` allows only the same current cgroup with an active matching
label and generation. Removing admission, retiring it, or replacing its
generation makes the existing snapshot unusable without walking sockets or
holders. Storage follows the socket lifetime; this probe does not manually
delete and recreate a label to simulate revocation.

The only infrastructure send exemption is an explicit storage flag assigned by trusted PID 1
to predetermined **AF_UNIX/SOCK_SEQPACKET control socketpair endpoints**. PID 1
checks their domain/type and reads back storage after registration. There is
no UID, root-cgroup, AF_UNIX-wide or INET-wide exemption. Every tested TCP socket
must have a connect-created non-infrastructure label, also read back. All BPF,
map, link, listener and cgroup FDs are closed before tool exec; a lifecycle tool
inherits only standard streams, control FD 3 and the deliberately tested TCP
FD 4. These control sockets are probe infrastructure, not a product transport.

**Every socket received through `file_receive` is refused, including same-
invocation transfers.** This is a deliberately over-restrictive viability policy,
not approved final FD-transfer semantics. Non-socket file transfers are not
tested. The program uses CO-RE `file.f_inode.i_mode` and rejects unreadable mode;
it does not guess offsets or cast a `struct file` into a BPF socket. Invocation
identity comes from `bpf_get_current_cgroup_id`, not procfs. A map of counters
must witness actual allowed/denied connect hooks and socket-specific LSM denies.
Every scenario checks all counters for **exact equality**, not lower bounds.
Each transmission requires exactly one allow/deny send hook, plus two control
sends, and zero other hook changes. EPERM alone cannot count as hook evidence.

## Executed scenarios if the guest passes

* Real IPv4 and IPv6 TCP loopback connections from the admitted tool and its
  forked descendant; the supervisor accepts all four connections.
* Outside-cgroup connect attempts return EPERM for both families, before
  admission and while the legitimate invocation is admitted.
* The admitted tool sends both connected socket FDs using SCM_RIGHTS to a
  separate outside-cgroup uid-1000 tool. Linux must deliver payload but truncate
  ancillary rights (`MSG_CTRUNC`, no FD). The LSM socket-denial counter must
  increment twice.
* For **both IPv4 and IPv6**, each of `sendmsg`, `write`, `writev`, pipe-to-socket
  `splice`, and regular-file-to-socket `sendfile` sends exactly one witnessed
  byte while its generation is active. `sendfile` reads the first ELF byte of
  the read-only `/probe.bpf.o`; it needs no writable filesystem or new config.
* A same-invocation fork/`setsid`/exec descendant of `/tool` uses the inherited
  TCP socket successfully through all five APIs. The descendant repeats the
  sealed-executable/nondumpable/capability checks.
* Trusted PID 1 first connects from inside the admitted cgroup, returns to the
  root cgroup, then hands the same socket through **fork/exec inheritance** to
  both an inside and an outside tool. Each outside-cgroup API must return
  EPERM, increment exactly one send-deny counter, leave SCM-deny unchanged and
  deliver no peer data. The outside tool occupies a second sealed **active**
  invocation with a different label, not just an unadmitted cgroup. This misuse
  does not depend on SCM_RIGHTS being blocked.
* With the inside tool and TCP peer still alive, PID 1 sets admission inactive,
  then commands each API separately: all must be hook-denied. It then activates
  a newer generation with the same label: each API on the old socket remains
  denied. A new connection in that generation succeeds through all five APIs.
  Control acknowledgments delimit each map transition; no concurrent teardown
  or in-flight-send guarantee is inferred.
* Every tool is separately executed from the sealed, root-owned **0551** file
  `/tool`, checks that reading that file fails, sets and verifies nondumpable,
  and checks zero effective, permitted, inheritable, bounding and ambient
  capabilities. Admission still works through the kernel cgroup helper.
* Unprivileged BPF map creation returns EPERM. Opening root or either invocation
  `cgroup.procs` for writing returns EACCES, preventing migration through those
  paths; no cgroup delegation is performed.
* Against the known live, nondumpable same-UID sibling, `pidfd_open` succeeds
  but `pidfd_getfd` returns EPERM and opening its exact `/proc/<pid>/fd/4` path
  returns EACCES. Reopening one's own socket through that path returns ENXIO.
  There is no holder scan. `io_uring_setup` must return ENOSYS (the experimental
  kernel disables it). These are **access-control/absence witnesses, not LSM
  socket-denial claims**, and all non-control hook counters must stay unchanged.
* After children exit, admission is cleared, the cgroup is removed and the same
  path recreated. A fresh process inside the recreated cgroup cannot connect.
  This does not simulate a kernel cgroup-ID wraparound.
* Missing and malformed ELF objects are refused by libbpf before workload
  launch. The actual program's load and every attach are mandatory: any failure
  aborts the guest, never launches an unprotected tool and never prints PASS.

The main watchdog is 60 seconds; each executed tool has a 20-second watchdog.
PID 1 failures exit/panic and emit `ATTRIBUTION-PROBE:FAIL:...` with errno.
The host enforces an independent timeout and requires a clean VMM exit and all
seven exact markers (the earlier PASS markers are preserved):

```text
ATTRIBUTION-PROBE:PASS:connect4-connect6-file_receive-partial
ATTRIBUTION-PROBE:PASS:socket-generation-actual-use-revocation-partial
ATTRIBUTION-PROBE:PASS:egress-label-bridge-development-only
ATTRIBUTION-PROBE:CAPACITY:2:boot-local-tombstones:no-reuse:exhaustion-closed
ATTRIBUTION-PROBE:BOOTSTRAP-CAPS:NET_ADMIN12,PERFMON38,BPF39:no-SYS_ADMIN
ATTRIBUTION-PROBE:UNSUPPORTED:production-slot-reuse,host-FlowMux,exec-identity,snapshot-restore,production-loader
ATTRIBUTION-PROBE:UNSUPPORTED:production-egress-bridge,claim-protocol,verifier-faults,exec-identity,concurrent-teardown,non-TCP
```

The preserved lifecycle phase totals are: allowed connect4/connect6 **4/4**,
socket receive denies **2**, denied connect4/connect6 **3/3**, socket snapshots
**8**, allowed TCP sends **30**, denied TCP sends **30**, explicit infrastructure
sends **122**. With the bridge fixtures, expected final totals are allowed
connect4/connect6 **10/9**, receive denies **2**, ordinary connect denials **4/4**,
snapshots **27**, allowed sends **39**, denied sends **34**, infrastructure sends
**170**, private-port denials **12/12**, redirects **4/3** (counter keys 9–12).
The per-command checks are stronger than these totals: a missing denial on one
API cannot be hidden by extra hook calls on another.

The native ELF extension adds three required exact markers, without removing
or changing any earlier marker (including earlier `exec-identity` unsupported
claims, which still describe the earlier socket/bridge phases):

```text
ATTRIBUTION-PROBE:EXEC-HOOKS:19:allow=1:deny=18:PT_INTERP-native=1
ATTRIBUTION-PROBE:PASS:native-ELF-task-storage-one-shot-exec-admission-partial
ATTRIBUTION-PROBE:UNSUPPORTED:exec-byte-attestation,script-chains,production-exec-decision,concurrent-exec-revocation
```

Diagnostics and verifier messages remain in the
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

`socket_connect` is non-sleepable on this kernel; declaring it `lsm.s` would be
invalid. `socket_sendmsg` and `file_receive` are sleepable-capable. The new
programs use direct CO-RE `socket->sk` loads, rather than pretending a scalar
pointer from `bpf_probe_read_kernel` is a verifier-typed socket. **Verifier
acceptance of these typed sock arguments to `bpf_sk_storage_get` must be proved
by the real kernel load**, not assumed from helper availability. Userspace
FD-keyed SK_STORAGE registration/readback is another mandatory runtime gate.

Other gates include both-architecture CO-RE relocation, new LSM trampoline
attachment, each syscall actually reaching `socket_sendmsg`, exact hook counts,
and alternate-path errno/absence. Any unsupported operation or unexpected
error fails the probe; there is no skip/fallback. Compilation and static
Python source checks establish none of these runtime properties.

## Bounded development-only egress bridge

`bridge.c` is included by the existing native `init.c`; no new image role,
runtime dependency, production kernel configuration or lock is introduced.
The test still has only loopback and the mandatory vsock device, never a NIC,
firewall, new product transport or host-network connector.

* The two boot-local slots are protected ports **900/901**, reserved once by a
  monotonic capacity-two allocator. Retired slots are tombstones and are never
  reassigned, even to the same fixture. Exhaustion returns `ENOSPC` with no
  activation; production capacity, recovery and reuse are explicitly unsupported.
* Trusted bootstrap creates sealed cgroups and sets the privileged-port boundary
  to 1024. A separately executed **uid/gid 989** egress fixture binds IPv4 and
  IPv6 listeners with only `CAP_NET_BIND_SERVICE` (10). After exec it checks the
  effective/permitted/inheritable/bounding/ambient sets exactly. Tools have zero
  capabilities and cannot bind either protected port (`EACCES`, both families).
* Only after the egress listener-ready acknowledgment does PID 1 install each
  immutable private-listener-to-opaque-binding test fixture and activate the
  leaf's `BPF_MAP_TYPE_CGROUP_STORAGE` value. Storage keys include the cgroup
  inode and connect4/connect6 attachment type; both values are read back.
* Leaf connect4/connect6 hooks redirect ordinary **127.0.0.1:1080 / ::1:1080**
  to the active private slot. Root guards reject directly requested private
  ports for every cgroup and identity: trusted root, root-cgroup tool, both
  admitted invocation tools, egress and sibling egress. No privileged bypass.
* **Default Linux 6.12 effective program order is leaf-before-root.** The root
  guards use explicit `BPF_F_PREORDER` links, retaining multi-program inheritance,
  never override. The matching
  [`compute_effective_progs`](https://github.com/gregkh/linux/blob/v6.12.111/kernel/bpf/cgroup.c)
  orders these before leaf programs. Actual verifier load, PREORDER attach,
  successful private-listener accepts and exact redirect/denial counters are
  mandatory runtime gates. Unsupported flags/helpers fail, never fall back.
* Both active invocations select distinct opaque fixtures for both families.
  Fork descendants select the same slot. The egress fixture accepts the actual
  redirected TCP socket, reads the tool's witnessed SEND byte, and obtains its
  **actual local port with `getsockname`**. That port—not a simulated address or
  a caller-supplied binding—is the lookup key.
* A narrow test query uses the existing registered AF_UNIX control-pair style.
  Kernel `SCM_CREDENTIALS` must match uid/gid 989 **and the trusted launched PID**;
  `SO_PEERCRED` on a pre-created socketpair would incorrectly authenticate its
  creator. The root fixture answers only immutable live bindings; unknown,
  unauthenticated and retired queries return zero (unbound). A uid-1000 tool and
  a same-uid sibling egress get deliberately granted adversarial test channels
  and must receive zero for a guessed live port. No SCM_RIGHTS transfer is used;
  blanket socket `file_receive` denial remains active.
* The final connection is accepted only after slot release/tombstoning and
  before any query: its lookup returns zero. No reassignment occurs. This is
  ordered fixture behavior, not a concurrent teardown guarantee.
* Inactive slots initially leave normal 1080 unredirected (`ECONNREFUSED`).
  Separately, real ordinary 1080 listeners accept and read bytes from an
  unadmitted sibling in both families with **zero redirects/bindings**.
  Unbound SK_STORAGE has immutable owner cgroup and zero label/generation,
  never the infrastructure flag. It is created only for normal loopback 1080
  without any admission entry; SEND requires the same still-unadmitted owner
  and the socket's actual remote loopback address/1080 port. Installing even
  an inactive admission disables that exemption. Bound snapshots retain the
  previous generation/revocation rules unchanged.
* In each family the same successfully connected unbound socket first sends
  real DATA, then must fail `sendmsg` with exactly `EPERM` after its uid-1000
  owner moves to the independent active invocation. After moving back, installing
  an active admission for the original owner must also yield exactly `EPERM`.
  Each operation adds exactly one SEND denial, no allowed send or redirect;
  the peer must return `EAGAIN` with no DATA. Only the AF_UNIX control pair is
  infrastructure, used for synchronization; the TCP descriptor never is.
* After slot 0's leaf redirect is disabled and binding tombstoned, fresh
  IPv4 and IPv6 connects are attempted against real ordinary 1080 listeners.
  The original admission is retained but marked inactive before these attempts.
  **Connect must fail with exactly `EPERM` under the current policy**: the root
  guard denies a present inactive admission rather than allowing unbound fallback.
  Each family adds exactly one connect denial, no snapshot, SEND or redirect;
  the ordinary listener's nonblocking accept must return `EAGAIN`, and the slot
  remains without a live binding. Merely disabling the leaf while leaving admission
  active would allow ordinary 1080 connect and bound SEND; that is not retirement
  and is not accepted as fail-closed evidence here. The queued private
  connection's retired lookup is still checked separately. This proves bounded
  fixture fail-closed DATA behavior, not production retirement/readiness.

The root fixture owns setup and the lookup table; it is **bootstrap/test
infrastructure only**, not a long-lived production loader or host FlowMux.
A bounded separate child loads the actual object and attaches root/LSM hooks
with exactly `CAP_NET_ADMIN` (12), `CAP_PERFMON` (38), `CAP_BPF` (39), represented
as `uint64_t` and two cap words. Three negative trials omit one capability each
and must fail loading with `EPERM`; no `CAP_SYS_ADMIN` workaround. These gates
discover what the actual kernel needs; they have not been proved by source
checks. The child drops its permitted/effective mask and exits before tools;
each tool independently drops all capabilities, and egress never receives
bootstrap capabilities.

Accepted TCP sockets are **read-only fixtures**: the egress does not send on
them or mark them as infrastructure. There is no broad uid-989/INET SEND
exemption. Bidirectional proxy behavior, production claims, executable identity,
host FlowMux, snapshot/restore and production slot reuse are not proved.

## Bounded native ELF exec admission

The bridge/socket baseline at `910f1db` passed both architectures. That evidence
does **not** prove the new exec extension. `exec.c` runs only after every original
socket/bridge scenario; it attaches the new LSM and installs a dedicated scope
at that point. The original counter array is unchanged, and every exec
acknowledgment checks that **all original socket counters remain unchanged**.
There is no CI, production recipe, kernel config, lock or runtime-artifact change.

The sleepable `bprm_check_security` program makes one bounded rule: **inside the
explicit exec-probe cgroup**, every unknown executable is denied with EPERM.
Outside that scope, it does not impose an exec policy. A present inactive scope
is a denial tombstone. The trusted root fixture opens `/tool` using O_PATH,
checks its root-owned 0551 identity, checks the native ELF64 little-endian
machine and program headers, and installs its `{superblock dev, inode}` in a
capacity-one hash map. The map is frozen and an attempted update must fail
with EPERM. Linux internal `s_dev` is derived explicitly from `st_dev`'s
major/minor, not compared to the differently encoded userspace number.

After fork, while the child waits on a pipe, the trusted parent opens its
pidfd and seeds `BPF_MAP_TYPE_TASK_STORAGE` through that pidfd with
`{cgroup, generation, dev, inode}`. It reads the value back before releasing
the child. The LSM obtains a verifier-typed current `task_struct` using
`bpf_get_current_task_btf`, requires active matching generation and exact
opened `bprm->file` identity in the frozen native map, checks ELF magic, and
deletes that exact task's permit before allowing the exec. Successful exec
must leave storage absent, witnessed through the same still-live pidfd.
Neither argv, a nonce, nor raw TGID identifies the task. Userspace maps and
argv are trusted **fixture inputs**, not a real host authorization decision.

The actual child has **UID 902/GID 907**, zero effective/permitted/inheritable/
bounding/ambient capabilities, no-new-privileges, and nondumpable state.
Root map/link/listener/cgroup FDs are closed before exec. Only standard streams,
two synchronization pipes, and the O_PATH executable FD remain; that FD is
CLOEXEC and its absence is checked after exec. No read-permission relaxation
or capabilities are granted to the tool. The pipes add no socket exemption.

Every operation is individually stopped on an acknowledgment while PID 1
checks the **exact** exec counters `{hooks, allows, denies}`:

* Before its own initial exec, the seeded task forks a descendant. The
  descendant's `/tool` exec must return EPERM without consuming the parent's
  permit. The parent subsequently executes that exact O_PATH file using
  `execveat(AT_EMPTY_PATH)` successfully once.
* After success, the same process must get EPERM from `/proc/self/exe`
  `execve`, O_PATH `execveat`, and `fexecve`. Each is exactly one denied hook.
* Identical-byte `/tool-copy` has a distinct inode; an identical-byte copy on
  sealed read-only tmpfs has a distinct superblock device. Both are denied,
  as are hardlink and read-only bind aliases of the protected inode. The
  foreign-mount fixture does not establish a same-inode/different-device collision.
* A script chain (`/exec-script` → `/exec-script-next` → `/tool`) is denied
  at its first hook as an unknown whitelist identity. This does not isolate
  the ELF-magic predicate or prove generic rejection of all scripts.
* Separate gated children test absent permit, inactive scope, changed
  generation, distinct inode, foreign superblock and script chain while a
  native `/tool` permit is still seeded for that exact task where applicable.
  Unknown-file attempts cannot be explained solely by missing task permits.
* Three further gated children execute positively whitelisted native `/tool`
  with an active actual cgroup and matching generation. Each seeded permit
  changes exactly one field: cgroup, inode (same actual device), or device
  (same actual inode). Each must return EPERM with exactly one denied hook.
  PID 1 reads back the unchanged unused permit and explicitly deletes it
  before releasing the child, using the same cleanup checks as other denials.
  These isolate the three permit comparisons rather than relying on unknown
  whitelist identities.
* Unused permits are explicitly removed and read back absent while the child
  is alive and waiting for the final acknowledgment. An inactive newer
  generation is installed **before** release and reap. Task lifetime, not
  numeric PID reuse, controls storage. Same-TGID threads and PID reuse remain
  explicitly unsupported; no artificial PID reuse test is claimed.

The expected totals are **19 hooks, 1 allow, 18 denies**. The dynamically linked
native fixture must contain exactly one `PT_INTERP`, whose path is checked as
root-owned and not group/other-writable. Its successful native exec must
produce **exactly one** `bprm_check_security` invocation, including the actual
dynamic loader path. Linux `fs/exec.c` calls the hook per
`search_binary_handler`, not per candidate native handler; script rewrites
re-enter that search. `fs/binfmt_elf.c` opens/loads `PT_INTERP` itself rather than
rewriting `bprm->interpreter`. The runtime counter gate, not these source
observations alone, proves the expected behavior. Static ELF, compat ELF,
arbitrary interpreters and executable loading after initial exec are not tested.

### Exact Linux 6.12 legality and runtime gates

Upstream v6.12.111
[`bpf_task_storage.c`](https://github.com/gregkh/linux/blob/v6.12.111/kernel/bpf/bpf_task_storage.c)
implements userspace map lookup/update/delete with `pidfd_get_pid` followed by
`pid_task(..., PIDTYPE_PID)`. Its helper argument is
`ARG_PTR_TO_BTF_ID_OR_NULL` for the tracing task type, and its task map does not
inherit a permit on fork. `kernel/trace/bpf_trace.c` exposes task-storage helpers
to tracing programs; `kernel/bpf/bpf_lsm.c` delegates to that helper table and
lists `bprm_check_security` as sleepable. These are source legality evidence,
**not verifier acceptance**. Both-architecture BTF relocation, the real
sleepable program load/attach, pidfd map update/readback/delete, task-storage
non-inheritance and hook multiplicity must all pass in the real pinned kernel.
Any failure aborts rather than skipping, widening permissions, changing kernel
config, or falling back to a raw-TGID permit.

This is **not full production byte attestation**: device/inode identity is
boot-local and trusted files are sealed fixtures, not IMA/fs-verity proofs.
The gate does not authenticate the dynamic interpreter/shared-library bytes,
prevent every way to execute copied bytes, inspect all mappings, solve inode/
device reuse across restored snapshots, or synchronize concurrent revocation.
The root fixture is trusted bootstrap/test infrastructure, not a production
loader. Original socket/bridge unsupported claims remain in force.

## Explicitly unsupported, even after partial PASS

* Concurrent teardown/in-flight syscall races, cgroup-ID wraparound, generation
  wraparound, socket cloning and accepted-socket ownership policy. The bounded
  TCP inheritance fixture is not a complete production FD inheritance policy.
* ptrace attacks beyond the tested nondumpable sibling access checks, enabled
  io_uring, non-socket SCM transfers, UDP, and transports other than this TCP
  loopback probe. Socket receive remains blanket-denied even within invocation.
* **Production EGRESS BRIDGE remains open.** This bounded private-listener
  cgroup-connect redirect is a development-only accepted-binding fixture.
  Bidirectional proxy/connector behavior and production runtime integration
  remain absent; the test control socketpair is not a product protocol.
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
