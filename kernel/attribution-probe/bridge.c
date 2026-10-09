/* Included by init.c. Fixed trusted test fixtures, NOT a claim protocol. */
#define EGRESS CG "/egress"
#define SIBLING CG "/sibling-egress"
struct bridge_slot { uint32_t port, active; };
struct bridge_request { uint32_t port, family; };
struct bridge_command { uint32_t slot, family, retired; };
static const struct { uint32_t port; uint64_t binding; } fixtures[2] = {
    {900, UINT64_C(0xc723a0dd34f89162)}, {901, UINT64_C(0x61b359bfca712804)}
};
static bool tombstone[2], live_binding[2];
static unsigned int allocated;

static int reserve_slot(void)
{
    if (allocated == 2) { errno = ENOSPC; return -1; }
    return allocated++; /* Never decrement: boot-local retired slots stay used. */
}

static void cap_mask(uint64_t mask, bool inheritable)
{
    struct __user_cap_header_struct hdr = { .version = _LINUX_CAPABILITY_VERSION_3 };
    struct __user_cap_data_struct caps[2] = {0};
    for (int word = 0; word < 2; word++) {
        caps[word].effective = caps[word].permitted = (uint32_t)(mask >> (word * 32));
        caps[word].inheritable = inheritable ? caps[word].permitted : 0;
    }
    CHECK(syscall(SYS_capset, &hdr, caps) == 0);
}

static void exact_cap_mask(uint64_t mask, bool inherited)
{
    struct __user_cap_header_struct hdr = { .version = _LINUX_CAPABILITY_VERSION_3 };
    struct __user_cap_data_struct caps[2] = {0};
    CHECK(syscall(SYS_capget, &hdr, caps) == 0);
    for (int word = 0; word < 2; word++) {
        uint32_t bits = (uint32_t)(mask >> (word * 32));
        CHECK(caps[word].effective == bits && caps[word].permitted == bits);
        CHECK(caps[word].inheritable == (inherited ? bits : 0));
    }
    for (int i = 0; i <= CAP_LAST_CAP; i++) {
        int bit = !!(mask & (UINT64_C(1) << i));
        CHECK(prctl(PR_CAPBSET_READ, i, 0, 0, 0) == bit);
        CHECK(prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_IS_SET, i, 0, 0) == (inherited ? bit : 0));
    }
}

static int root_guard(struct bpf_object *obj, const char *name, int cg,
                      enum bpf_attach_type type)
{
    struct bpf_program *p = bpf_object__find_program_by_name(obj, name);
    CHECK(p);
    LIBBPF_OPTS(bpf_link_create_opts, opts, .flags = BPF_F_PREORDER);
    int link = bpf_link_create(bpf_program__fd(p), cg, type, &opts);
    CHECK(link >= 0); /* Never fall back to default leaf-before-root order. */
    return link;
}

static void capability_trial(int omitted)
{
    pid_t pid = fork();
    CHECK(pid >= 0);
    if (!pid) {
        /* Bootstrap-only exact sufficient set, not a production loader claim. */
        uint64_t mask = (UINT64_C(1) << CAP_NET_ADMIN) |
                        (UINT64_C(1) << CAP_PERFMON) | (UINT64_C(1) << CAP_BPF);
        if (omitted >= 0)
            mask &= ~(UINT64_C(1) << omitted);
        CHECK(prctl(PR_SET_SECUREBITS, SECBIT_NOROOT | SECBIT_NOROOT_LOCKED) == 0);
        for (int i = 0; i <= CAP_LAST_CAP; i++)
            if (!(mask & (UINT64_C(1) << i)))
                CHECK(prctl(PR_CAPBSET_DROP, i, 0, 0, 0) == 0);
        cap_mask(mask, false);
        exact_cap_mask(mask, false);
        struct bpf_object *obj = bpf_object__open_file("/probe.bpf.o", NULL);
        CHECK(obj && !libbpf_get_error(obj));
        if (omitted >= 0) {
            CHECK(bpf_object__load(obj) == -EPERM);
            bpf_object__close(obj);
            cap_mask(0, false);
            _exit(0);
        }
        CHECK(bpf_object__load(obj) == 0);
        int cg = open(CG, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        CHECK(cg >= 0);
        int guard = root_guard(obj, "connect4", cg, BPF_CGROUP_INET4_CONNECT);
        struct bpf_link *lsm = attach(obj, "label_connect", -1);
        CHECK(bpf_link__destroy(lsm) == 0);
        close(guard); close(cg);
        bpf_object__close(obj);
        cap_mask(0, false);
        _exit(0); /* No tool is ever launched from this bootstrap child. */
    }
    wait_ok(pid);
}

static void capability_viability(void)
{
    capability_trial(CAP_NET_ADMIN);
    capability_trial(CAP_PERFMON);
    capability_trial(CAP_BPF);
    capability_trial(-1);
    dprintf(1, "ATTRIBUTION-PROBE:BOOTSTRAP-CAPS:NET_ADMIN12,PERFMON38,BPF39:no-SYS_ADMIN\n");
}

static void egress_privileges(void)
{
    CHECK(prctl(PR_SET_SECUREBITS, SECBIT_NOROOT | SECBIT_NOROOT_LOCKED) == 0);
    for (int i = 0; i <= CAP_LAST_CAP; i++)
        if (i != CAP_NET_BIND_SERVICE)
            CHECK(prctl(PR_CAPBSET_DROP, i, 0, 0, 0) == 0);
    CHECK(prctl(PR_SET_KEEPCAPS, 1) == 0);
    CHECK(setgroups(0, NULL) == 0);
    CHECK(setresgid(989, 989, 989) == 0 && setresuid(989, 989, 989) == 0);
    cap_mask(UINT64_C(1) << CAP_NET_BIND_SERVICE, true);
    CHECK(prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_RAISE, CAP_NET_BIND_SERVICE, 0, 0) == 0);
    CHECK(prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0);
}

static int bridge_tcp(int family, int port, bool server, int error)
{
    int fd = socket(family, SOCK_STREAM | SOCK_CLOEXEC, 0);
    CHECK(fd >= 0);
    struct sockaddr_storage ss = {0};
    socklen_t len;
    if (family == AF_INET) {
        struct sockaddr_in *a = (void *)&ss;
        a->sin_family = family; a->sin_port = htons(port);
        a->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        len = sizeof(*a);
    } else {
        struct sockaddr_in6 *a = (void *)&ss;
        a->sin6_family = family; a->sin6_port = htons(port);
        a->sin6_addr = in6addr_loopback;
        len = sizeof(*a);
        if (server) {
            int one = 1;
            CHECK(setsockopt(fd, IPPROTO_IPV6, IPV6_V6ONLY, &one, sizeof(one)) == 0);
        }
    }
    int rc = server ? bind(fd, (void *)&ss, len) : connect(fd, (void *)&ss, len);
    if (error)
        CHECK(rc == -1 && errno == error);
    else {
        CHECK(rc == 0);
        if (server) CHECK(listen(fd, 16) == 0);
    }
    return fd;
}

static void private_guesses(void)
{
    for (int i = 0; i < 2; i++) {
        close(bridge_tcp(AF_INET, fixtures[i].port, false, EPERM));
        close(bridge_tcp(AF_INET6, fixtures[i].port, false, EPERM));
    }
}

static int bridge_tool(int family, int mode)
{
    alarm(20);
    if (getuid() == 989)
        exact_cap_mask(UINT64_C(1) << CAP_NET_BIND_SERVICE, true);
    else check_unprivileged();
    if (mode == 0) {
        private_guesses();
        if (getuid() == 1000)
            for (int i = 0; i < 2; i++) {
                close(bridge_tcp(AF_INET, fixtures[i].port, true, EACCES));
                close(bridge_tcp(AF_INET6, fixtures[i].port, true, EACCES));
            }
    } else if (mode == 1) {
        int fd = bridge_tcp(family, 1080, false, 0);
        transmit(fd, 0, true);
        close(fd);
    } else if (mode == 2) {
        close(bridge_tcp(family, 1080, false, ECONNREFUSED));
    } else if (mode == 3) {
        pid_t child = fork();
        CHECK(child >= 0);
        if (!child) _exit(bridge_tool(family, 1));
        wait_ok(child);
    } else if (mode == 4) {
        /* Deliberately grant ONLY an infrastructure test channel to the
         * adversary. Even a guessed live port cannot yield a binding. */
        struct bridge_request query = { .port = 900, .family = AF_INET };
        CHECK(write(3, &query, sizeof(query)) == sizeof(query));
        uint64_t binding = UINT64_MAX;
        CHECK(read(3, &binding, sizeof(binding)) == sizeof(binding) && !binding);
        CHECK(write(3, "K", 1) == 1);
    } else die("bridge tool mode");
    return 0;
}

static int bridge_egress(void)
{
    alarm(20);
    CHECK(getuid() == 989 && geteuid() == 989);
    exact_cap_mask(UINT64_C(1) << CAP_NET_BIND_SERVICE, true); /* AFTER exec */
    CHECK(prctl(PR_SET_DUMPABLE, 0) == 0);
    private_guesses();
    int listeners[2][2];
    for (int slot = 0; slot < 2; slot++) {
        listeners[slot][0] = bridge_tcp(AF_INET, fixtures[slot].port, true, 0);
        listeners[slot][1] = bridge_tcp(AF_INET6, fixtures[slot].port, true, 0);
    }
    CHECK(write(3, "R", 1) == 1);
    struct bridge_command cmd;
    while (read(3, &cmd, sizeof(cmd)) == sizeof(cmd)) {
        if (cmd.slot == 2) return 0;
        CHECK(cmd.slot < 2 && (cmd.family == AF_INET || cmd.family == AF_INET6));
        int fd = accept4(listeners[cmd.slot][cmd.family == AF_INET6], NULL, NULL, SOCK_CLOEXEC);
        CHECK(fd >= 0);
        char byte;
        CHECK(read(fd, &byte, 1) == 1 && byte == 'M'); /* Real labelled SEND. */
        struct sockaddr_storage ss;
        socklen_t size = sizeof(ss);
        CHECK(getsockname(fd, (void *)&ss, &size) == 0);
        struct bridge_request query = { .family = ss.ss_family };
        query.port = ss.ss_family == AF_INET ? ntohs(((struct sockaddr_in *)&ss)->sin_port)
                                            : ntohs(((struct sockaddr_in6 *)&ss)->sin6_port);
        CHECK(query.family == cmd.family && query.port == fixtures[cmd.slot].port);
        /* Query uses the ACTUAL ACCEPTED SOCKET, never a simulated address. */
        CHECK(write(3, &query, sizeof(query)) == sizeof(query));
        uint64_t binding;
        CHECK(read(3, &binding, sizeof(binding)) == sizeof(binding));
        CHECK(binding == (cmd.retired ? 0 : fixtures[cmd.slot].binding));
        CHECK(write(3, "K", 1) == 1);
        close(fd);
    }
    die("egress control EOF");
    return 1;
}

static pid_t bridge_launch(const char *cgroup, int channel, bool egress, int family, int mode)
{
    pid_t pid = fork();
    CHECK(pid >= 0);
    if (!pid) {
        put(cgroup, "0");
        if (channel >= 0) {
            int temp = fcntl(channel, F_DUPFD_CLOEXEC, 10);
            CHECK(temp >= 0 && dup2(temp, 3) == 3);
        } else close(3);
        CHECK(syscall(SYS_close_range, 4, ~0U, 0) == 0);
        if (egress) egress_privileges(); else unprivileged();
        char fam[16], operation[16];
        snprintf(fam, sizeof(fam), "%d", family);
        snprintf(operation, sizeof(operation), "%d", mode);
        if (channel >= 0 && mode != 4) execl("/tool", "/tool", "--bridge-egress", NULL);
        else execl("/tool", "/tool", "--bridge-tool", fam, operation, NULL);
        die("exec bridge fixture");
    }
    return pid;
}

static void slot_state(int map, uint64_t id, int slot, bool active)
{
    struct bridge_slot value = { .port = fixtures[slot].port, .active = active };
    enum bpf_attach_type types[] = { BPF_CGROUP_INET4_CONNECT, BPF_CGROUP_INET6_CONNECT };
    for (int i = 0; i < 2; i++) {
        struct bpf_cgroup_storage_key key = { .cgroup_inode_id = id, .attach_type = types[i] };
        struct bridge_slot actual = {0};
        CHECK(bpf_map_update_elem(map, &key, &value, BPF_ANY) == 0);
        CHECK(bpf_map_lookup_elem(map, &key, &actual) == 0);
        CHECK(!memcmp(&value, &actual, sizeof(value)));
    }
}

static struct bridge_request received_query(int channel, pid_t egress, bool *authenticated)
{
    struct bridge_request request;
    struct iovec io = { .iov_base = &request, .iov_len = sizeof(request) };
    union { struct cmsghdr align; char bytes[CMSG_SPACE(sizeof(struct ucred))]; } control = {0};
    struct msghdr msg = { .msg_iov = &io, .msg_iovlen = 1,
                         .msg_control = control.bytes, .msg_controllen = sizeof(control) };
    CHECK(recvmsg(channel, &msg, 0) == sizeof(request));
    CHECK(!(msg.msg_flags & (MSG_CTRUNC | MSG_TRUNC)));
    struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
    CHECK(c && c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_CREDENTIALS &&
          c->cmsg_len == CMSG_LEN(sizeof(struct ucred)));
    struct ucred cred;
    memcpy(&cred, CMSG_DATA(c), sizeof(cred));
    *authenticated = cred.pid == egress && cred.uid == 989 && cred.gid == 989;
    return request;
}

static uint64_t fixture_lookup(struct bridge_request query, bool authenticated)
{
    if (!authenticated || (query.family != AF_INET && query.family != AF_INET6))
        return 0;
    for (int i = 0; i < 2; i++)
        if (query.port == fixtures[i].port)
            return live_binding[i] && !tombstone[i] ? fixtures[i].binding : 0;
    return 0;
}

static void bridge_probe(struct bpf_object *obj, int admission_map, int storage)
{
    CHECK(mkdir(EGRESS, 0700) == 0 && mkdir(SIBLING, 0700) == 0);
    put("/proc/sys/net/ipv4/ip_unprivileged_port_start", "1024");
    int slots = bpf_object__find_map_fd_by_name(obj, "bridge_slots");
    CHECK(slots >= 0);
    const char *paths[] = { INV, OTHER };
    uint64_t ids[2];
    struct bpf_link *links[2][2];
    for (int i = 0; i < 2; i++) {
        struct stat st;
        CHECK(stat(paths[i], &st) == 0);
        ids[i] = st.st_ino;
        int cg = open(paths[i], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        CHECK(cg >= 0);
        links[i][0] = attach(obj, "bridge4", cg);
        links[i][1] = attach(obj, "bridge6", cg);
        close(cg);
        CHECK(reserve_slot() == i);
        slot_state(slots, ids[i], i, false); /* No fixture yet, no redirect. */
    }
    CHECK(reserve_slot() == -1 && errno == ENOSPC);
    wait_ok(bridge_launch(INV "/cgroup.procs", -1, false, AF_INET, 2));
    wait_ok(bridge_launch(INV "/cgroup.procs", -1, false, AF_INET6, 2));
    expected[CONNECT4]++; expected[CONNECT6]++;
    expected[SNAPSHOT] += 2;
    exact_counters("inactive-slot-unbound1080");
    int pair[2];
    control_pair(storage, pair);
    int one = 1;
    CHECK(setsockopt(pair[0], SOL_SOCKET, SO_PASSCRED, &one, sizeof(one)) == 0);
    pid_t egress = bridge_launch(EGRESS "/cgroup.procs", pair[1], true, 0, 0);
    close(pair[1]);
    char byte;
    CHECK(read(pair[0], &byte, 1) == 1 && byte == 'R');
    expected[PRIVATE4_DENY] += 2; expected[PRIVATE6_DENY] += 2;
    expected[INFRA_SEND]++;
    exact_counters("egress-exec-caps-listeners-ready");
    private_guesses(); /* Trusted root UID also has NO private-connect bypass. */
    expected[PRIVATE4_DENY] += 2; expected[PRIVATE6_DENY] += 2;
    exact_counters("trusted-root-direct-private-denied");
    for (int i = 0; i < 2; i++) {
        CHECK(!tombstone[i] && fixtures[i].binding);
        live_binding[i] = true; /* Immutable fixture installed BEFORE activation. */
        struct admission a = { .label = 90 + i, .generation = 1, .active = 1 };
        CHECK(bpf_map_update_elem(admission_map, &ids[i], &a, BPF_ANY) == 0);
        slot_state(slots, ids[i], i, true);
    }
    for (int attacker = 0; attacker < 2; attacker++) {
        int attack[2];
        control_pair(storage, attack);
        CHECK(setsockopt(attack[0], SOL_SOCKET, SO_PASSCRED, &one, sizeof(one)) == 0);
        pid_t pid = bridge_launch(SIBLING "/cgroup.procs", attack[1], attacker, AF_INET, 4);
        close(attack[1]);
        bool authenticated;
        struct bridge_request query = received_query(attack[0], egress, &authenticated);
        CHECK(!authenticated && query.port == 900);
        uint64_t binding = fixture_lookup(query, authenticated);
        CHECK(!binding && write(attack[0], &binding, sizeof(binding)) == sizeof(binding));
        CHECK(read(attack[0], &byte, 1) == 1 && byte == 'K');
        wait_ok(pid);
        close(attack[0]);
        expected[INFRA_SEND] += 3;
        exact_counters("tool-and-sibling-egress-query-unbound");
    }
    const char *identities[] = { CG "/cgroup.procs", INV "/cgroup.procs",
                                OTHER "/cgroup.procs", SIBLING "/cgroup.procs" };
    for (int i = 0; i < 4; i++) {
        wait_ok(bridge_launch(identities[i], -1, i == 3, 0, 0));
        expected[PRIVATE4_DENY] += 2; expected[PRIVATE6_DENY] += 2;
        if (i == 1 || i == 2) expected[SNAPSHOT] += 4;
        exact_counters("all-identities-direct-private-denied");
    }
    /* Real ordinary 1080 listener: prove unbound DATA, not just refusal. Root
     * only reads accepted TCP sockets, never grants them a send exemption. */
    for (int family = 0; family < 2; family++) {
        int af = family ? AF_INET6 : AF_INET;
        int listener = bridge_tcp(af, 1080, true, 0);
        wait_ok(bridge_launch(SIBLING "/cgroup.procs", -1, false, af, 1));
        int peer = accept4(listener, NULL, NULL, SOCK_CLOEXEC);
        CHECK(peer >= 0 && read(peer, &byte, 1) == 1 && byte == 'M');
        struct sockaddr_storage ss;
        socklen_t length = sizeof(ss);
        CHECK(getsockname(peer, (void *)&ss, &length) == 0);
        CHECK((af == AF_INET ? ntohs(((struct sockaddr_in *)&ss)->sin_port) :
               ntohs(((struct sockaddr_in6 *)&ss)->sin6_port)) == 1080);
        expected[family ? CONNECT6 : CONNECT4]++;
        expected[SNAPSHOT]++; expected[SEND_ALLOW]++;
        exact_counters("unbound-real1080-data-no-binding");
        close(peer); close(listener);
    }
    for (int step = 0; step < 7; step++) {
        int slot = step < 4 ? step / 2 : 0;
        int family = step % 2 ? AF_INET6 : AF_INET;
        struct bridge_command cmd = { .slot = slot, .family = family, .retired = step == 6 };
        wait_ok(bridge_launch(slot ? OTHER "/cgroup.procs" : INV "/cgroup.procs",
                             -1, false, family, step == 4 || step == 5 ? 3 : 1));
        expected[family == AF_INET ? CONNECT4 : CONNECT6]++;
        expected[family == AF_INET ? REDIRECT4 : REDIRECT6]++;
        expected[SNAPSHOT]++; expected[SEND_ALLOW]++;
        exact_counters("redirect-real-client-send");
        if (cmd.retired) {
            slot_state(slots, ids[slot], slot, false);
            live_binding[slot] = false; tombstone[slot] = true;
            CHECK(reserve_slot() == -1 && errno == ENOSPC);
        }
        /* Release is complete BEFORE the egress process makes its query. */
        CHECK(write(pair[0], &cmd, sizeof(cmd)) == sizeof(cmd));
        bool authenticated;
        struct bridge_request query = received_query(pair[0], egress, &authenticated);
        CHECK(authenticated);
        CHECK(query.port == fixtures[slot].port && query.family == (uint32_t)family);
        uint64_t binding = fixture_lookup(query, authenticated);
        CHECK(write(pair[0], &binding, sizeof(binding)) == sizeof(binding));
        CHECK(read(pair[0], &byte, 1) == 1 && byte == 'K');
        expected[INFRA_SEND] += 4;
        exact_counters("authenticated-accepted-localport-query");
        dprintf(1, "ATTRIBUTION-PROBE:BRIDGE:accepted-family=%u:port=%u:binding=%llx\n",
                query.family, query.port, (unsigned long long)binding);
    }
    struct bridge_command stop = { .slot = 2 };
    CHECK(write(pair[0], &stop, sizeof(stop)) == sizeof(stop));
    wait_ok(egress);
    expected[INFRA_SEND]++;
    exact_counters("bridge-egress-exit");
    close(pair[0]);
    for (int i = 0; i < 2; i++) {
        slot_state(slots, ids[i], i, false);
        CHECK(bpf_map_delete_elem(admission_map, &ids[i]) == 0);
        for (int family = 0; family < 2; family++) CHECK(bpf_link__destroy(links[i][family]) == 0);
    }
    dprintf(1, "ATTRIBUTION-PROBE:PASS:egress-label-bridge-development-only\n");
    dprintf(1, "ATTRIBUTION-PROBE:CAPACITY:2:boot-local-tombstones:no-reuse:exhaustion-closed\n");
    dprintf(1, "ATTRIBUTION-PROBE:UNSUPPORTED:production-slot-reuse,host-FlowMux,exec-identity,snapshot-restore,production-loader\n");
}
