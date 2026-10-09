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
                   boot.BRIDGE_UNSUPPORTED, boot.UNSUPPORTED)
        good = "\r\n".join(markers) + "\r\n"
        boot.validate_output(good, 0)
        for output, code in [
            ("", 0), (boot.PASS, 0), (good, 1), (good + boot.PASS + "\n", 0),
            (good + "ATTRIBUTION-PROBE:FAIL:child\n", 0),
            ("prefix" + good, 0), (good.replace("partial", "production"), 0),
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
        for marker in (boot.PASS, boot.LIFECYCLE_PASS, boot.BRIDGE_PASS,
                       boot.BRIDGE_CAPACITY, boot.BOOTSTRAP_CAPS,
                       boot.BRIDGE_UNSUPPORTED, boot.UNSUPPORTED):
            self.assertIn(f'"{marker}\\n"', init + bridge)
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
        self.assertNotIn("bpf_get_current_uid_gid", bpf)
        self.assertEqual(bpf.count("if (ret)\n        return ret;"), 3)

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


if __name__ == "__main__":
    unittest.main()
