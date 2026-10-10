/* Included by init.c: bounded accepted-server witness, never a runtime service. */
#include <stddef.h>
#define SERVER_INV CG "/server-invocation"
#define SERVER_EGRESS CG "/server-egress"
#define SERVER_UNBOUND CG "/server-unbound"
struct server_snapshot { uint64_t cgroup, generation, port, role; };
_Static_assert(sizeof(struct server_snapshot) == 32 &&
               offsetof(struct server_snapshot, cgroup) == 0 &&
               offsetof(struct server_snapshot, generation) == 8 &&
               offsetof(struct server_snapshot, port) == 16 &&
               offsetof(struct server_snapshot, role) == 24, "server snapshot ABI");
enum { SERVER_BIND, SERVER_BIND_DENY, SERVER_ACCEPT, SERVER_ACCEPT_DENY,
       SERVER_SEND, SERVER_SEND_DENY, SERVER_COUNTERS };
static int server_witnesses, server_exec_witnesses;
static uint64_t server_expected[SERVER_COUNTERS];
static uint64_t server_exec_baseline[3];
struct server_cmd { int op, error; };
struct server_child { pid_t pid; int to, from, port; };

static void server_write(int fd, const void *value, size_t size)
{
    CHECK(write(fd, value, size) == (ssize_t)size);
}

static void server_read(int fd, void *value, size_t size)
{
    CHECK(read(fd, value, size) == (ssize_t)size);
}

static void server_exact(const char *phase)
{
    exact_counters(phase); /* Earlier counters remain exact, not lower bounds. */
    for (uint32_t key = 0; key < 3; key++) {
        uint64_t value;
        CHECK(bpf_map_lookup_elem(server_exec_witnesses, &key, &value) == 0);
        CHECK(value == server_exec_baseline[key]);
    }
    for (uint32_t key = 0; key < SERVER_COUNTERS; key++) {
        uint64_t value = UINT64_MAX;
        CHECK(bpf_map_lookup_elem(server_witnesses, &key, &value) == 0);
        if (value != server_expected[key]) {
            dprintf(2, "ATTRIBUTION-PROBE:SERVER-COUNTER:%s:%u:%llu!=%llu\n",
                    phase, key, (unsigned long long)value,
                    (unsigned long long)server_expected[key]);
            die("exact server counters");
        }
    }
}

static socklen_t server_address(int family, int port, bool wildcard,
                                struct sockaddr_storage *ss)
{
    memset(ss, 0, sizeof(*ss));
    if (family == AF_INET) {
        struct sockaddr_in *a = (void *)ss;
        a->sin_family = family;
        a->sin_port = htons(port);
        a->sin_addr.s_addr = htonl(wildcard ? INADDR_ANY : INADDR_LOOPBACK);
        return sizeof(*a);
    }
    struct sockaddr_in6 *a = (void *)ss;
    a->sin6_family = family;
    a->sin6_port = htons(port);
    a->sin6_addr = wildcard ? in6addr_any : in6addr_loopback;
    return sizeof(*a);
}

static int server_port(int fd, int family)
{
    struct sockaddr_storage ss;
    socklen_t len = sizeof(ss);
    CHECK(getsockname(fd, (void *)&ss, &len) == 0 && ss.ss_family == family);
    int port = family == AF_INET ? ntohs(((struct sockaddr_in *)&ss)->sin_port)
                                : ntohs(((struct sockaddr_in6 *)&ss)->sin6_port);
    CHECK(port > 0);
    return port;
}

static int server_socket(int family)
{
    int fd = socket(family, SOCK_STREAM | SOCK_CLOEXEC, 0);
    CHECK(fd >= 0);
    int one = 1;
    CHECK(setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one)) == 0);
    if (family == AF_INET6)
        CHECK(setsockopt(fd, IPPROTO_IPV6, IPV6_V6ONLY, &one, sizeof(one)) == 0);
    return fd;
}

static void server_zero_identity(int uid)
{
    CHECK(prctl(PR_SET_SECUREBITS, SECBIT_NOROOT | SECBIT_NOROOT_LOCKED) == 0);
    for (int i = 0; i <= CAP_LAST_CAP; i++)
        CHECK(prctl(PR_CAPBSET_DROP, i, 0, 0, 0) == 0);
    CHECK(setgroups(0, NULL) == 0);
    CHECK(setresgid(uid == 902 ? 907 : uid, uid == 902 ? 907 : uid,
                    uid == 902 ? 907 : uid) == 0);
    CHECK(setresuid(uid, uid, uid) == 0);
    CHECK(prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0);
}

static void server_identity_check(int uid, bool bind_cap)
{
    CHECK(getuid() == (uid_t)uid && geteuid() == (uid_t)uid);
    CHECK(getgid() == (gid_t)(uid == 902 ? 907 : uid) &&
          getegid() == (gid_t)(uid == 902 ? 907 : uid));
    exact_cap_mask(bind_cap ? UINT64_C(1) << CAP_NET_BIND_SERVICE : 0, bind_cap);
    CHECK(prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) == 1);
    CHECK(prctl(PR_SET_DUMPABLE, 0) == 0 && prctl(PR_GET_DUMPABLE) == 0);
    CHECK(fcntl(5, F_GETFD) == -1 && errno == EBADF);
    struct stat st;
    CHECK(stat("/tool", &st) == 0 && st.st_uid == 0 && (st.st_mode & 07777) == 0551);
    CHECK(open("/tool", O_RDONLY | O_CLOEXEC) == -1 && errno == EACCES);
    CHECK(open(SERVER_INV "/cgroup.procs", O_WRONLY | O_CLOEXEC) == -1 && errno == EACCES);
    union bpf_attr attr = { .map_type = BPF_MAP_TYPE_ARRAY, .key_size = 4,
                           .value_size = 8, .max_entries = 1 };
    CHECK(syscall(SYS_bpf, BPF_MAP_CREATE, &attr, sizeof(attr)) == -1 && errno == EPERM);
}

/* mode 0 leased listener; 1 failed bind then listen autobind; 2 failed bind
 * then loopback rebind; 3 failed bind then wildcard rebind; 4 failed bind
 * then ordinary client connect; 5 wrong UID bind; 6 ordinary 1080 listener;
 * 7 no-lease bind. All TCP sockets are opened AFTER privilege drop/exec. */
static int server_fixture(int family, int port, int mode, int uid)
{
    alarm(20);
    server_identity_check(uid, mode == 0 || mode == 6 || mode == 7);
    int listener = server_socket(family);
    struct sockaddr_storage ss;
    socklen_t len = server_address(family, port, false, &ss);
    if (mode == 5 || mode == 7) {
        CHECK(bind(listener, (void *)&ss, len) == -1 && errno == EPERM);
        int ready = 0;
        server_write(4, &ready, sizeof(ready));
        close(listener);
        return 0;
    }
    if (mode >= 1 && mode <= 4) {
        /* LSM has already created role=listener storage before this EACCES. */
        CHECK(bind(listener, (void *)&ss, len) == -1 && errno == EACCES);
        if (mode == 2 || mode == 3) {
            len = server_address(family, 0, mode == 3, &ss);
            CHECK(bind(listener, (void *)&ss, len) == 0);
        } else if (mode == 4) {
            len = server_address(family, 1080, false, &ss);
            CHECK(connect(listener, (void *)&ss, len) == 0);
        }
    } else {
        CHECK(bind(listener, (void *)&ss, len) == 0);
    }
    if (mode != 4)
        CHECK(listen(listener, 8) == 0);
    int actual_port = server_port(listener, family);
    if (mode == 0 || mode == 6)
        CHECK(actual_port == port);
    else
        CHECK(actual_port != port);
    server_write(4, &actual_port, sizeof(actual_port)); /* Readiness source. */
    int accepted = mode == 4 ? listener : -1;
    for (;;) {
        struct server_cmd cmd;
        server_read(3, &cmd, sizeof(cmd));
        if (cmd.op == 'X')
            break;
        if (cmd.op == 'A' || cmd.op == 'Z') {
            if (accepted >= 0) close(accepted);
            accepted = accept4(listener, NULL, NULL, SOCK_CLOEXEC);
            CHECK(accepted >= 0 && server_port(accepted, family) == actual_port);
            char byte;
            if (cmd.op == 'A')
                CHECK(read(accepted, &byte, 1) == 1 && byte == 'Q');
            else
                CHECK(recv(accepted, &byte, 1, MSG_DONTWAIT) == -1 && errno == EAGAIN);
        } else {
            CHECK(cmd.op == 'S' && accepted >= 0);
            char byte = 'Q'; /* Exact echo, not simulated reply success. */
            ssize_t rc = send(accepted, &byte, 1, MSG_NOSIGNAL);
            if (cmd.error)
                CHECK(rc == -1 && errno == cmd.error);
            else
                CHECK(rc == 1);
        }
        server_write(4, &cmd.op, sizeof(cmd.op));
    }
    if (accepted >= 0 && accepted != listener) close(accepted);
    close(listener);
    return 0;
}

static int server_client(int family, int port, int uid)
{
    alarm(20);
    server_identity_check(uid, false);
    int fd = server_socket(family);
    struct sockaddr_storage ss;
    socklen_t len = server_address(family, port, false, &ss);
    CHECK(connect(fd, (void *)&ss, len) == 0);
    int ready = port;
    server_write(4, &ready, sizeof(ready));
    for (;;) {
        struct server_cmd cmd;
        server_read(3, &cmd, sizeof(cmd));
        if (cmd.op == 'X')
            break;
        char byte = 'Q';
        if (cmd.op == 'S') {
            CHECK(send(fd, &byte, 1, MSG_NOSIGNAL) == 1);
        } else if (cmd.op == 'R') {
            CHECK(read(fd, &byte, 1) == 1 && byte == 'Q');
        } else {
            CHECK(cmd.op == 'N');
            CHECK(recv(fd, &byte, 1, MSG_DONTWAIT) == -1 && errno == EAGAIN);
        }
        server_write(4, &cmd.op, sizeof(cmd.op));
    }
    close(fd);
    return 0;
}

static struct server_child server_launch(int family, int port, int mode, int uid,
                                         bool client, bool unbound)
{
    int in[2], out[2];
    CHECK(pipe2(in, O_CLOEXEC) == 0 && pipe2(out, O_CLOEXEC) == 0);
    pid_t pid = fork();
    CHECK(pid >= 0);
    if (!pid) {
        put(client ? (unbound ? SERVER_UNBOUND "/cgroup.procs" : SERVER_INV "/cgroup.procs")
                   : SERVER_EGRESS "/cgroup.procs", "0");
        /* Duplicate above fixed FD range first; never inherit map/link/socket
         * authority. Only pipes 3/4 cross exec, no TCP FD or SCM transfer. */
        int a = fcntl(in[0], F_DUPFD_CLOEXEC, 10);
        int b = fcntl(out[1], F_DUPFD_CLOEXEC, 10);
        CHECK(a >= 10 && b >= 10);
        CHECK(dup3(a, 3, 0) == 3 && dup3(b, 4, 0) == 4);
        CHECK(syscall(SYS_close_range, 5, ~0U, 0) == 0);
        if (!client && (mode == 0 || mode == 6 || mode == 7))
            egress_privileges();
        else
            server_zero_identity(uid);
        char af[16], p[16], m[16], u[16];
        snprintf(af, sizeof(af), "%d", family);
        snprintf(p, sizeof(p), "%d", port);
        snprintf(m, sizeof(m), "%d", mode);
        snprintf(u, sizeof(u), "%d", uid);
        execl("/tool", "/tool", client ? "--server-client" : "--server-fixture",
              af, p, m, u, NULL);
        die("exec server fixture");
    }
    close(in[0]); close(out[1]);
    struct server_child child = { .pid = pid, .to = in[1], .from = out[0] };
    server_read(child.from, &child.port, sizeof(child.port));
    return child;
}

static void server_command(struct server_child *child, int op, int error,
                            int counter)
{
    struct server_cmd cmd = { .op = op, .error = error };
    server_write(child->to, &cmd, sizeof(cmd));
    int ack;
    server_read(child->from, &ack, sizeof(ack));
    CHECK(ack == op);
    if (counter >= 0) server_expected[counter]++;
    server_exact("server-command");
}

static void server_stop(struct server_child *child)
{
    struct server_cmd cmd = { .op = 'X' };
    server_write(child->to, &cmd, sizeof(cmd));
    wait_ok(child->pid);
    close(child->to); close(child->from);
    server_exact("server-exit");
}

static struct server_child server_connect(int family, int port, int uid, bool unbound)
{
    struct server_child child = server_launch(family, port, 0, uid, true, unbound);
    expected[family == AF_INET ? CONNECT4 : CONNECT6]++;
    expected[SNAPSHOT]++;
    server_exact("server-client-connect");
    return child;
}

static void server_data(struct server_child *client, struct server_child *server,
                         bool accepted_tag)
{
    expected[SEND_ALLOW]++;
    server_command(client, 'S', 0, -1);
    server_command(server, 'A', 0, accepted_tag ? SERVER_ACCEPT : SERVER_ACCEPT_DENY);
}

static void server_lease(int leases, uint64_t cg, uint64_t generation, uint64_t port)
{
    struct server_snapshot s = { .cgroup = cg, .generation = generation,
                                 .port = port, .role = 2 }, readback;
    CHECK(bpf_map_update_elem(leases, &port, &s, BPF_ANY) == 0);
    CHECK(bpf_map_lookup_elem(leases, &port, &readback) == 0);
    CHECK(!memcmp(&s, &readback, sizeof(s)));
}

static void server_family(int family, uint64_t port, uint64_t cg, int map, int leases)
{
    struct admission live = { .label = 51, .generation = 1, .active = 1 };
    CHECK(bpf_map_update_elem(map, &cg, &live, BPF_ANY) == 0);
    server_lease(leases, cg, live.generation, port);
    struct server_child server = server_launch(family, port, 0, 989, false, false);
    server_expected[SERVER_BIND]++;
    server_exact("listener-ready-before-activation");
    /* Port-map replacement alone must NOT change the already captured label. */
    server_lease(leases, cg, 2, port);
    struct server_child client = server_connect(family, port, 902, false);
    server_data(&client, &server, true);
    server_command(&server, 'S', 0, SERVER_SEND);
    server_command(&client, 'R', 0, -1);
    live.active = 0;
    CHECK(bpf_map_update_elem(map, &cg, &live, BPF_EXIST) == 0);
    server_command(&server, 'S', EPERM, SERVER_SEND_DENY);
    server_command(&client, 'N', 0, -1);
    live.active = 1; live.generation = 2;
    CHECK(bpf_map_update_elem(map, &cg, &live, BPF_EXIST) == 0);
    server_command(&server, 'S', EPERM, SERVER_SEND_DENY);
    server_command(&client, 'N', 0, -1);
    server_stop(&client);
    /* Same old listener, new port lease and admission: no new-generation tag. */
    client = server_connect(family, port, 1000, false);
    server_data(&client, &server, false);
    expected[SEND_DENY]++; /* Actual accepted socket is deliberately unlabelled. */
    server_command(&server, 'S', EPERM, -1);
    server_command(&client, 'N', 0, -1);
    server_stop(&client); server_stop(&server);
    CHECK(bpf_map_delete_elem(leases, &port) == 0);
    server = server_launch(family, port, 7, 989, false, false);
    server_expected[SERVER_BIND_DENY]++;
    wait_ok(server.pid); close(server.to); close(server.from);
    server_exact("fresh-listener-without-fresh-lease-denied");
    server_lease(leases, cg, live.generation, port);
    server = server_launch(family, port, 0, 989, false, false);
    server_expected[SERVER_BIND]++;
    server_exact("fresh-lease-listener-ready");
    client = server_connect(family, port, 1000, false);
    server_data(&client, &server, true);
    server_command(&server, 'S', 0, SERVER_SEND);
    server_command(&client, 'R', 0, -1);
    CHECK(bpf_map_delete_elem(leases, &port) == 0);
    server_command(&server, 'S', 0, SERVER_SEND);
    server_command(&client, 'R', 0, -1);
    server_stop(&client); server_stop(&server);
    server_lease(leases, cg, live.generation, port);
    server = server_launch(family, port, 5, 1000, false, false);
    server_expected[SERVER_BIND_DENY]++;
    wait_ok(server.pid); close(server.to); close(server.from);
    server_exact("wrong-uid-protected-server-bind-EPERM");
    for (int mode = 1; mode <= 3; mode++) {
        server = server_launch(family, port, mode, 989, false, false);
        server_expected[SERVER_BIND]++;
        server_exact("failed-EACCES-bind-tag-before-autobind-or-rebind");
        CHECK(server.port != (int)port);
        client = server_connect(family, server.port, 902, false);
        server_data(&client, &server, false);
        expected[SEND_DENY]++;
        server_command(&server, 'S', EPERM, -1);
        server_command(&client, 'N', 0, -1);
        server_stop(&client); server_stop(&server);
    }
    /* Ordinary unbound 1080 is an explicit interface GAP: client DATA works
     * without scoped admission, actual uid989 accepted-server SEND is denied. */
    server = server_launch(family, 1080, 6, 989, false, false);
    server_exact("ordinary-listener-no-scoped-authority");
    client = server_connect(family, 1080, 1000, true);
    expected[SEND_ALLOW]++;
    server_command(&client, 'S', 0, -1);
    server_command(&server, 'A', 0, -1); /* No listener tag, no fexit counter. */
    expected[SEND_DENY]++;
    server_command(&server, 'S', EPERM, -1);
    server_command(&client, 'N', 0, -1);
    server_stop(&client);
    /* A failed private bind followed by normal client connect retains role=2.
     * Even the old normal client label cannot turn it into server authority. */
    struct server_child rebind = server_launch(family, port, 4, 989, false, false);
    server_expected[SERVER_BIND]++;
    expected[family == AF_INET ? CONNECT4 : CONNECT6]++;
    expected[SNAPSHOT]++;
    server_exact("failed-private-bind-then-normal-client-connect");
    server_command(&rebind, 'S', EPERM, SERVER_SEND_DENY);
    server_command(&server, 'Z', 0, -1); /* Actual peer has no illicit DATA. */
    server_stop(&rebind); server_stop(&server);
    CHECK(bpf_map_delete_elem(leases, &port) == 0);
    CHECK(bpf_map_delete_elem(map, &cg) == 0);
}

static void server_probe(struct bpf_object *obj, int map)
{
    CHECK(mkdir(SERVER_INV, 0700) == 0 && mkdir(SERVER_EGRESS, 0700) == 0 &&
          mkdir(SERVER_UNBOUND, 0700) == 0);
    struct stat st;
    CHECK(stat(SERVER_INV, &st) == 0 && st.st_uid == 0 && (st.st_mode & 0777) == 0700);
    uint64_t cg = st.st_ino;
    int leases = bpf_object__find_map_fd_by_name(obj, "port_leases");
    server_witnesses = bpf_object__find_map_fd_by_name(obj, "server_witness");
    server_exec_witnesses = bpf_object__find_map_fd_by_name(obj, "exec_witness");
    CHECK(leases >= 0 && server_witnesses >= 0 && server_exec_witnesses >= 0 && cg);
    for (uint32_t key = 0; key < 3; key++)
        CHECK(bpf_map_lookup_elem(server_exec_witnesses, &key,
                                  &server_exec_baseline[key]) == 0);
    struct bpf_link *bind = attach(obj, "server_bind", -1);
    struct bpf_program *p = bpf_object__find_program_by_name(obj, "server_accept");
    CHECK(p);
    struct bpf_link *accept = bpf_program__attach_trace(p);
    CHECK(accept && !libbpf_get_error(accept)); /* Actual 6.12 helper/ABI gate. */
    server_exact("server-hooks-ready");
    server_family(AF_INET, 904, cg, map, leases);
    server_family(AF_INET6, 905, cg, map, leases);
    const uint64_t totals[SERVER_COUNTERS] = { 12, 4, 4, 8, 6, 6 };
    CHECK(!memcmp(server_expected, totals, sizeof(totals)));
    for (uint32_t key = 0; key < SERVER_COUNTERS; key++)
        dprintf(1, "ATTRIBUTION-PROBE:SERVER-WITNESS:%u=%llu\n",
                key, (unsigned long long)server_expected[key]);
    dprintf(1, "ATTRIBUTION-PROBE:SERVER-HOOKS:bind=12:bind-deny=4:accept=4:accept-deny=8:send=6:send-deny=6\n");
    dprintf(1, "ATTRIBUTION-PROBE:PASS:accepted-server-fexit-generation-bidirectional-private-partial\n");
    dprintf(1, "ATTRIBUTION-PROBE:UNSUPPORTED:normal1080-server-SEND,production-server-handler,host-FlowMux,concurrent-server-revocation,production-port-reuse\n");
    CHECK(bpf_link__destroy(accept) == 0 && bpf_link__destroy(bind) == 0);
    CHECK(rmdir(SERVER_INV) == 0 && rmdir(SERVER_EGRESS) == 0 &&
          rmdir(SERVER_UNBOUND) == 0);
}
