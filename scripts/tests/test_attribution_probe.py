"""Host-plan/acceptance/source isolation checks, NOT guest enforcement evidence."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "attribution_boot", ROOT / "scripts/experimental_attribution_boot.py"
)
boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot)


class ProbeTests(unittest.TestCase):
    def test_permit_absence_uses_libbpf_return_convention(self):
        source = (ROOT / "kernel/attribution-probe/exec.c").read_text()
        expression = "bpf_map_lookup_elem(permits, &pidfd, &readback)"
        self.assertEqual(source.count(expression + " == -ENOENT"), 2)
        self.assertNotIn(expression + " == -1", source)

    def test_explicit_devices_and_bounded_success_contract(self):
        for arch in ("x86_64", "aarch64"):
            cmd = boot.command(arch, Path("/kernel"), Path("/rootfs"), "tcg")
            devices = [cmd[i + 1] for i, item in enumerate(cmd) if item == "-device"]
            self.assertEqual(devices, [
                "virtio-blk-pci,drive=probe", "vhost-vsock-pci,guest-cid=9042"
            ])
            self.assertIn("-nodefaults", cmd)
            self.assertIn("readonly=on", " ".join(cmd))
            self.assertEqual(cmd[cmd.index("-net") + 1], "none")
            for forbidden in ("virtio-net", "-netdev", "mvmctl", "bin/dev", "runtime-overlay"):
                self.assertNotIn(forbidden, " ".join(cmd))

    def test_acceptance_requires_all_exact_markers_clean_exit_and_no_failure(self):
        markers = (boot.PASS, boot.LIFECYCLE_PASS, boot.BRIDGE_PASS,
                   boot.BRIDGE_CAPACITY, boot.BOOTSTRAP_CAPS,
                   boot.BRIDGE_UNSUPPORTED, boot.UNSUPPORTED,
                   boot.EXEC_PASS, boot.EXEC_HOOKS, boot.EXEC_UNSUPPORTED,
                   boot.SERVER_PASS, boot.SERVER_HOOKS, boot.SERVER_UNSUPPORTED)
        good = "\r\n".join(markers) + "\r\n"
        boot.validate_output(good, 0)
        for output, code in [
            ("", 0), (boot.PASS, 0), (good, 1), (good + boot.PASS + "\n", 0),
            (good + "ATTRIBUTION-PROBE:FAIL:child\n", 0),
            ("prefix" + good, 0), (good.replace("partial", "production"), 0),
            (good.replace(boot.EXEC_HOOKS,
                          "ATTRIBUTION-PROBE:EXEC-HOOKS:16:allow=1:deny=15:PT_INTERP-native=1"), 0),
            # The previously passing connect/SCM probe is not lifecycle evidence.
            (boot.PASS + "\n" + boot.UNSUPPORTED + "\n", 0),
        ] + [(good.replace(marker + "\r\n", ""), 0) for marker in markers] + [
            (good + marker + "\n", 0) for marker in markers
        ]:
            with self.subTest(output=output, code=code):
                with self.assertRaises(ValueError):
                    boot.validate_output(output, code)

    def test_invalid_plan_rejected(self):
        for arch, accel in (("riscv64", "kvm"), ("aarch64", "hvf")):
            with self.assertRaises(ValueError):
                boot.command(arch, Path("k"), Path("r"), accel)

    def test_native_image_owned_probe_only(self):
        recipe = (ROOT / "kernel/attribution-probe/default.nix").read_text()
        self.assertIn("chmod 0551 root/tool", recipe)
        self.assertIn("pkgs.libbpf", recipe)
        self.assertNotIn("mvm", recipe)
        init = (ROOT / "kernel/attribution-probe/init.c").read_text()
        for required in ("bpf_object__load", "bpf_program__attach_cgroup",
                         "bpf_program__attach_lsm", "MSG_CTRUNC", "PR_SET_DUMPABLE",
                         "SYS_capget", "CAPBSET_DROP", "SYS_lsm_list_modules"):
            self.assertIn(required, init)
        self.assertIn('put("/proc/sys/kernel/unprivileged_bpf_disabled", "1")', init)
        self.assertLess(init.index('attach(obj, "receive"'), init.index("launch(false, false, -1)"))
        self.assertNotIn("/proc/self", init)

    def test_guest_and_host_coverage_markers_cannot_drift(self):
        init = (ROOT / "kernel/attribution-probe/init.c").read_text()
        bridge = (ROOT / "kernel/attribution-probe/bridge.c").read_text()
        execute = (ROOT / "kernel/attribution-probe/exec.c").read_text()
        server = (ROOT / "kernel/attribution-probe/server.c").read_text()
        for marker in (boot.PASS, boot.LIFECYCLE_PASS, boot.BRIDGE_PASS,
                       boot.BRIDGE_CAPACITY, boot.BOOTSTRAP_CAPS,
                       boot.BRIDGE_UNSUPPORTED, boot.UNSUPPORTED,
                       boot.EXEC_PASS, boot.EXEC_HOOKS, boot.EXEC_UNSUPPORTED,
                       boot.SERVER_PASS, boot.SERVER_HOOKS, boot.SERVER_UNSUPPORTED):
            self.assertIn(f'"{marker}\\n"', init + bridge + execute + server)
        for unsupported in ("production-egress-bridge", "claim-protocol",
                            "verifier-faults", "exec-identity",
                            "concurrent-teardown", "non-TCP"):
            self.assertIn(unsupported, boot.UNSUPPORTED)
        self.assertNotIn("socket-use-lifecycle", boot.UNSUPPORTED)

    def test_generation_policy_is_socket_owned_and_checked_at_use(self):
        bpf = (ROOT / "kernel/attribution-probe/probe.bpf.c").read_text()
        for contract in (
            "BPF_MAP_TYPE_HASH", "BPF_MAP_TYPE_SK_STORAGE", "BPF_F_NO_PREALLOC",
            'SEC("lsm/socket_connect")', 'SEC("lsm.s/socket_sendmsg")',
            'SEC("lsm.s/file_receive")', "struct sock *sk = socket->sk",
            "BPF_SK_STORAGE_GET_F_CREATE", "a->active", "a->label", "a->generation",
            "s->cgroup == bpf_get_current_cgroup_id()",
            "s->label == a->label", "s->generation == a->generation",
            "old->generation == a->generation",
        ):
            self.assertIn(contract, bpf)
        self.assertNotIn('SEC("lsm.s/socket_connect")', bpf)
        self.assertNotIn("BPF_CORE_READ(socket", bpf)
        # UID is never an exemption for the old client ownership/generation rule.
        connect = bpf.split('SEC("lsm/socket_connect")', 1)[1].split(
            'SEC("lsm.s/socket_sendmsg")', 1)[0]
        self.assertNotIn("bpf_get_current_uid_gid", connect)
        self.assertEqual(bpf.count("if (ret)\n        return ret;"), 5)

    def test_real_api_matrix_and_exact_counters_are_required(self):
        init = (ROOT / "kernel/attribution-probe/init.c").read_text()
        for call in ("sendmsg(fd,", "write(fd, &byte", "writev(fd,",
                     "splice(pipefd[0],", "sendfile(fd,"):
            self.assertIn(call, init)
        for phase in ("active", "cross-cgroup-inherited", "retired",
                      "replaced-generation", "new-generation"):
            self.assertIn(f'"{phase}"', init)
        self.assertIn("CHECK(rc == -1 && errno == EPERM)", init)
        self.assertIn("if (value != expected[key])", init)
        self.assertNotIn("value >=", init)
        self.assertIn("expected[SEND_ALLOW] += allows", init)
        self.assertIn("expected[SEND_DENY] += denies", init)
        self.assertIn('exact_counters("command")', init)
        self.assertIn("MSG_DONTWAIT) == -1 && errno == EAGAIN", init)
        for family in ("AF_INET, listen4", "AF_INET6, listen6"):
            self.assertIn(f"lifecycle({family}", init)
        self.assertIn("CHECK(setsid() >= 0)", init)
        self.assertIn('execl("/tool", "/tool", "--descendant"', init)
        for program in ("receive", "label_connect", "use_socket"):
            self.assertLess(init.index(f'attach(obj, "{program}"'),
                            init.index("launch(false, false, -1)"))

    def test_infrastructure_and_alternate_paths_are_explicit(self):
        init = (ROOT / "kernel/attribution-probe/init.c").read_text()
        for contract in (
            "CHECK(domain == AF_UNIX)", "CHECK(type == SOCK_SEQPACKET)",
            ".infrastructure = 1", "&& !label.infrastructure",
            "SYS_close_range, 5, ~0U, 0",
            'put(inside ? INV "/cgroup.procs" : OTHER "/cgroup.procs", "0")',
            ".label = 43, .generation = admission.generation, .active = 1",
            "CHECK(other_id && other_id != id)",
            "SYS_pidfd_getfd, pidfd, 4, 0) == -1 && errno == EPERM",
            "O_RDWR | O_CLOEXEC) == -1 && errno == EACCES",
            "O_RDWR | O_CLOEXEC) == -1 && errno == ENXIO",
            "SYS_io_uring_setup, 1, &params) == -1 && errno == ENOSYS",
            "admission.active = 0", "admission.generation++",
            "alarm(20)", "alarm(60)",
        ):
            self.assertIn(contract, init)
        self.assertNotIn("readdir", init)
        self.assertNotIn("SYS_ptrace", init)

    def test_bridge_uses_local_storage_preorder_and_actual_accepted_socket(self):
        bpf = (ROOT / "kernel/attribution-probe/probe.bpf.c").read_text()
        bridge = (ROOT / "kernel/attribution-probe/bridge.c").read_text()
        for contract in ("BPF_MAP_TYPE_CGROUP_STORAGE", "bpf_get_local_storage",
                         "struct bpf_cgroup_storage_key", "count(9)", "count(10)",
                         "count(11)", "count(12)", "bpf_htons(1080)"):
            self.assertIn(contract, bpf)
        for contract in ("BPF_F_PREORDER", "bpf_link_create(", "getsockname(fd,",
                         "accept4(listeners", "SCM_CREDENTIALS", "SO_PASSCRED",
                         "cred.pid == egress && cred.uid == 989 && cred.gid == 989",
                         "live_binding[i] && !tombstone[i]", "reserve_slot() == -1",
                         "errno == ENOSPC", "query.port = ss.ss_family",
                         "tool-and-sibling-egress-query-unbound",
                         "unbound-real1080-data-no-binding",
                         "trusted-root-direct-private-denied"):
            self.assertIn(contract, bridge)
        self.assertLess(bridge.index("live_binding[i] = true"),
                        bridge.index("slot_state(slots, ids[i], i, true)"))
        release = bridge.index("live_binding[slot] = false")
        query = bridge.index("received_query(pair[0], egress", release)
        self.assertLess(release, query)
        self.assertNotIn("allocated--", bridge)
        self.assertNotIn("BPF_F_ALLOW_OVERRIDE", bridge)

    def test_unbound_send_remains_owned_and_exact_normal_destination(self):
        bpf = (ROOT / "kernel/attribution-probe/probe.bpf.c").read_text()
        for contract in ("struct socket_label unbound = { .cgroup = id }",
                         "!s->label && !s->generation && !s->infrastructure",
                         "s->cgroup == id && !bpf_map_lookup_elem(&invocation, &id)",
                         "normal_destination(sk)", "skc_dport != bpf_htons(1080)",
                         "skc_daddr == bpf_htonl(0x7f000001)", "skc_v6_daddr"):
            self.assertIn(contract, bpf)

    def test_real_unbound_negatives_and_fresh_retired_connect_are_required(self):
        bridge = (ROOT / "kernel/attribution-probe/bridge.c").read_text()
        for contract in (
            "control_pair(storage, sync)", "transmit(fd, 0, true)",
            "transmit(fd, 0, false)", 'put(OTHER "/cgroup.procs", pid)',
            'put(SIBLING "/cgroup.procs", pid)',
            "bpf_map_update_elem(admission_map, &owner_id, &admitted, BPF_NOEXIST)",
            "command(sync[0], 'A', 0, 1)",
            "MSG_DONTWAIT) == -1 && errno == EAGAIN",
            "unbound-owner-cgroup-change-denied",
            "unbound-original-owner-admitted-denied",
            "bridge_tcp(family, 1080, false, EPERM)",
            "bpf_map_update_elem(admission_map, &ids[slot], &retired, BPF_EXIST)",
            "CHECK(peer == -1 && errno == EAGAIN)",
            "expected[fresh ? CONNECT6_DENY : CONNECT4_DENY]++",
            "retired-slot-fresh1080-connect-denied-no-binding",
        ):
            self.assertIn(contract, bridge)
        self.assertLess(bridge.index("slot_state(slots, ids[slot], slot, false)"),
                        bridge.index('wait_ok(bridge_launch(INV "/cgroup.procs", -1, false, af, 5))'))

    def test_bootstrap_caps_are_two_words_and_not_leaked_after_egress_exec(self):
        bridge = (ROOT / "kernel/attribution-probe/bridge.c").read_text()
        for contract in ("caps[2]", "mask >> (word * 32)",
                         "UINT64_C(1) << CAP_PERFMON", "UINT64_C(1) << CAP_BPF",
                         "capability_trial(CAP_NET_ADMIN)",
                         "capability_trial(CAP_PERFMON)", "capability_trial(CAP_BPF)",
                         "bpf_object__load(obj) == -EPERM",
                         "setresuid(989, 989, 989)",
                         "i != CAP_NET_BIND_SERVICE",
                         "exact_cap_mask(UINT64_C(1) << CAP_NET_BIND_SERVICE, true)"):
            self.assertIn(contract, bridge)
        self.assertNotIn("<< CAP_SYS_ADMIN", bridge)

    def test_exec_scope_is_exact_task_generation_and_opened_inode(self):
        bpf = (ROOT / "kernel/attribution-probe/probe.bpf.c").read_text()
        execute = (ROOT / "kernel/attribution-probe/exec.c").read_text()
        init = (ROOT / "kernel/attribution-probe/init.c").read_text()
        for contract in (
            'SEC("lsm.s/bprm_check_security")', "BPF_MAP_TYPE_TASK_STORAGE",
            "struct task_struct *task = bpf_get_current_task_btf()",
            "bpf_task_storage_get(&exec_permits, task, 0, 0)",
            "bpf_task_storage_delete(&exec_permits, task) == 0",
            "permit->cgroup == cg", "permit->generation == scope->generation",
            "permit->file.dev == file.dev", "permit->file.ino == file.ino",
            "file, f_inode, i_sb, s_dev", "file, f_inode, i_ino",
            "if (!scope)\n        return 0",
        ):
            self.assertIn(contract, bpf)
        self.assertNotIn("bpf_get_current_pid_tgid", bpf)
        self.assertLess(init.index("bridge_probe(obj, map, storage)"),
                        init.index("exec_probe(obj)"))
        for contract in (
            "SYS_pidfd_open, child, 0",
            "bpf_map_update_elem(permits, &pidfd, &permit, BPF_NOEXIST)",
            "bpf_map_freeze(inodes)", "getuid() == 902", "getgid() == 907",
            "setresuid(902, 902, 902)", "setresgid(907, 907, 907)",
            "SYS_close_range, 6, ~0U, 0", "dup3(executable, 5, O_CLOEXEC)",
            "totals[0] == 19 && totals[1] == 1 && totals[2] == 18",
            'exact_counters("exec-keeps-original-socket-counters")',
            "CHECK(interpreters == 1)", "PT_INTERP", "EM_X86_64", "EM_AARCH64",
        ):
            self.assertIn(contract, execute)

    def test_exec_replays_unknown_files_and_preexec_revocation_are_explicit(self):
        execute = (ROOT / "kernel/attribution-probe/exec.c").read_text()
        for contract in (
            'exec_denied("/proc/self/exe", 0)',
            'exec_denied("/proc/self/exe", 1)',
            'exec_denied("/proc/self/exe", 2)', "fexecve(fd, exec_args, exec_env)",
            'exec_denied("/tool-copy", 0)', 'exec_denied("/tool-hard", 0)',
            'exec_denied(EXEC_DIR "/foreign", 0)',
            'exec_denied(EXEC_DIR "/bind", 0)', 'exec_denied("/exec-script", 0)',
            'exec_denied("/tool", 0)', "pid_t descendant = fork()",
            "copy.st_ino != tool.st_ino", "foreign.st_dev != tool.st_dev",
            "hard.st_ino == tool.st_ino", "bind.st_ino == tool.st_ino",
            "if (trial == 2)\n            scope.active = 0",
            "if (trial == 3)\n            scope.generation++",
            "CHECK(rc == -1 && errno == EPERM)",
        ):
            self.assertIn(contract, execute)
        delete = execute.index("bpf_map_delete_elem(permits, &pidfd)")
        fence = execute.index("scope.generation++;", delete)
        release = execute.index("exec_byte(gate[1], 'X')", fence)
        reap = execute.index("wait_ok(child)", release)
        self.assertLess(delete, fence)
        self.assertLess(fence, release)
        self.assertLess(release, reap)

    def test_exec_permit_identity_mismatches_use_whitelisted_native_tool(self):
        execute = (ROOT / "kernel/attribution-probe/exec.c").read_text()
        self.assertIn("enum { WRONG_CGROUP = 7, WRONG_INODE, WRONG_DEVICE };", execute)
        self.assertIn(
            '"/tool-copy", EXEC_DIR "/foreign", "/exec-script",\n'
            '                           "/tool", "/tool", "/tool" };', execute)
        seed = execute.index("bpf_map_update_elem(permits, &pidfd, &permit, BPF_NOEXIST)")
        for trial, field in (
            ("WRONG_CGROUP", "cgroup"),
            ("WRONG_INODE", "file.ino"),
            ("WRONG_DEVICE", "file.dev"),
        ):
            mutation = f"if (trial == {trial})\n            permit.{field} ^= UINT64_C(1);"
            self.assertIn(mutation, execute)
            self.assertLess(execute.index(mutation), seed)
        for contract in (
            "scope.active = 1;",
            ".cgroup = cg, .generation = scope.generation,\n"
            "                                      .file = identity",
            "exec_denied(paths[trial], 0)",
            "CHECK(denies == (trial == 0 ? 9U : 1U))",
            "if (trial > 1)",
            "bpf_map_delete_elem(permits, &pidfd)",
        ):
            self.assertIn(contract, execute)

    def test_server_fexit_has_pinned_two_argument_abi_and_no_mutable_lease_read(self):
        bpf = (ROOT / "kernel/attribution-probe/probe.bpf.c").read_text()
        accept = bpf.split('SEC("fexit/inet_csk_accept")', 1)[1].split(
            'SEC("cgroup/connect4")', 1)[0]
        self.assertIn(
            "struct sock *listener, struct proto_accept_arg *arg,\n"
            "             struct sock *accepted", accept)
        self.assertNotIn("port_leases", accept)
        for contract in (
            "bpf_sk_storage_get(&server_tags, listener, 0, 0)",
            "listener->__sk_common.skc_state != 10", "!accepted",
            "!server_tuple(listener, s)", "!server_tuple(accepted, s)",
            "accepted->__sk_common.skc_state != 1", "initial.role = 1",
            "!server_live(s)", "BPF_SK_STORAGE_GET_F_CREATE",
        ):
            self.assertIn(contract, accept)
        self.assertNotIn("bpf_probe_read_kernel", accept)
        self.assertNotIn("socket_accept", accept.split("int BPF_PROG", 1)[1])
        bind = bpf.split('SEC("lsm.s/socket_bind")', 1)[1].split(
            "struct proto_accept_arg;", 1)[0]
        for contract in (
            "(__u32)bpf_get_current_uid_gid() != 989", "lease->role != 2",
            "lease->port != port", "!server_live(lease)", "!loopback",
            "old->generation == lease->generation", "struct server_snapshot initial = *lease",
        ):
            self.assertIn(contract, bind)
        send = bpf.split('SEC("lsm.s/socket_sendmsg")', 1)[1].split(
            'SEC("lsm.s/file_receive")', 1)[0]
        for contract in (
            "(__u32)bpf_get_current_uid_gid() == 989 && server->role == 1",
            "server_live(server) && server_tuple(sk, server)",
            "server_count(allow ? 4 : 5)", "return allow ? 0 : -1",
        ):
            self.assertIn(contract, send)
        self.assertNotIn("port_leases", send)
        self.assertIn("__u64 cgroup, generation, port, role;", bpf)
        self.assertIn("sk->__sk_common.skc_num != s->port", bpf)
        self.assertIn("sk->sk_type != 1 || sk->sk_protocol != 6", bpf)
        self.assertIn("skc_v6_rcv_saddr", bpf)
        # Adding the server map must not resize either preceding counter map.
        self.assertIn("__uint(max_entries, 13);", bpf)
        self.assertIn("__uint(max_entries, 3);", bpf)
        self.assertIn("__uint(max_entries, 6);", bpf)

    def test_server_real_echo_revocation_failed_bind_and_normal_gap_are_mandatory(self):
        server = (ROOT / "kernel/attribution-probe/server.c").read_text()
        init = (ROOT / "kernel/attribution-probe/init.c").read_text()
        self.assertLess(init.index("exec_probe(obj)"), init.index("server_probe(obj, map)"))
        self.assertLess(init.index("ATTRIBUTION-PROBE:WITNESS:"), init.index("server_probe(obj, map)"))
        for contract in (
            "server_family(AF_INET, 904", "server_family(AF_INET6, 905",
            "bpf_program__attach_trace(p)", "accept4(listener", "getsockname(fd,",
            "server_lease(leases, cg, 2, port)", "live.active = 0",
            "live.active = 1; live.generation = 2",
            "server_connect(family, port, 902, false)",
            "server_connect(family, port, 1000, false)",
            "server_connect(family, 1080, 1000, true)",
            "server_command(&server, 'S', EPERM, SERVER_SEND_DENY)",
            "server_command(&server, 'S', EPERM, -1)",
            "server_command(&client, 'R', 0, -1)", "byte == 'Q'",
            "mode == 2 || mode == 3", "mode == 4", "mode == 5 || mode == 7",
            "bind(listener, (void *)&ss, len) == -1 && errno == EACCES",
            "bind(listener, (void *)&ss, len) == -1 && errno == EPERM",
            "listen(listener, 8)", "CHECK(server.port != (int)port)",
            "recv(accepted, &byte, 1, MSG_DONTWAIT) == -1 && errno == EAGAIN",
            "SYS_close_range, 5, ~0U, 0", "server_zero_identity(uid)",
            "egress_privileges()", "exact_cap_mask(bind_cap",
            "if (value != server_expected[key])",
            "{ 12, 4, 4, 8, 6, 6 }",
        ):
            self.assertIn(contract, server)
        self.assertNotIn("SCM_RIGHTS", server)
        self.assertNotIn("value >=", server)
        # Deletion of lease does not alter an already captured/live SEND tag.
        deletion = server.index("CHECK(bpf_map_delete_elem(leases, &port) == 0);",
                                server.index('"fresh-lease-listener-ready"'))
        self.assertLess(deletion, server.index(
            "server_command(&server, 'S', 0, SERVER_SEND);", deletion))
        self.assertIn("normal1080-server-SEND", boot.SERVER_UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
