#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/bpf.h>
#include <linux/capability.h>
#include <linux/io_uring.h>
#include <linux/lsm.h>
#include <linux/securebits.h>
#include <net/if.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/reboot.h>
#include <sys/resource.h>
#include <sys/sendfile.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <unistd.h>
#include <grp.h>
#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#define CG "/sys/fs/cgroup"
#define INV CG "/invocation"
#define OTHER CG "/other-invocation"
#define PORT 19042

struct admission {
    uint64_t label, generation, active;
};
struct socket_label {
    uint64_t cgroup, label, generation, infrastructure;
};
enum { CONNECT4, CONNECT6, RECEIVE_DENY, CONNECT4_DENY, CONNECT6_DENY,
       SNAPSHOT, SEND_ALLOW, SEND_DENY, INFRA_SEND,
       PRIVATE4_DENY, PRIVATE6_DENY, REDIRECT4, REDIRECT6, NCOUNTERS };
static int witnesses;
static uint64_t expected[NCOUNTERS];

static void die(const char *what)
{
    dprintf(2, "ATTRIBUTION-PROBE:FAIL:%s errno=%d\n", what, errno);
    _exit(1); /* PID 1 exit deliberately panics; host never accepts it. */
}
#define CHECK(x) do { if (!(x)) die(#x); } while (0)

static void deadline(int sig)
{
    (void)sig;
    static const char marker[] = "ATTRIBUTION-PROBE:FAIL:guest-timeout\n";
    if (write(2, marker, sizeof(marker) - 1) != (ssize_t)(sizeof(marker) - 1))
        _exit(2); /* Diagnostic failure must still fail the bounded probe. */
    _exit(1);
}

static void put(const char *path, const char *value)
{
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    CHECK(fd >= 0);
    CHECK(write(fd, value, strlen(value)) == (ssize_t)strlen(value));
    CHECK(close(fd) == 0);
}

static void wait_ok(pid_t pid)
{
    int status;
    CHECK(waitpid(pid, &status, 0) == pid);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
}

static void unprivileged(void)
{
    CHECK(prctl(PR_SET_SECUREBITS, SECBIT_NOROOT | SECBIT_NOROOT_LOCKED) == 0);
    for (int i = 0; i <= CAP_LAST_CAP; i++)
        CHECK(prctl(PR_CAPBSET_DROP, i, 0, 0, 0) == 0);
    CHECK(setgroups(0, NULL) == 0);
    CHECK(setresgid(1000, 1000, 1000) == 0);
    CHECK(setresuid(1000, 1000, 1000) == 0);
    CHECK(prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0);
}

static void check_unprivileged(void)
{
    struct __user_cap_header_struct hdr = { .version = _LINUX_CAPABILITY_VERSION_3 };
    struct __user_cap_data_struct caps[2] = {0};
    CHECK(getuid() == 1000 && geteuid() == 1000);
    CHECK(syscall(SYS_capget, &hdr, caps) == 0);
    for (int i = 0; i < 2; i++)
        CHECK(!caps[i].effective && !caps[i].permitted && !caps[i].inheritable);
    for (int i = 0; i <= CAP_LAST_CAP; i++) {
        CHECK(prctl(PR_CAPBSET_READ, i, 0, 0, 0) == 0);
        CHECK(prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_IS_SET, i, 0, 0) == 0);
    }
    CHECK(prctl(PR_SET_DUMPABLE, 0) == 0);
    CHECK(prctl(PR_GET_DUMPABLE) == 0);
    struct stat executable;
    CHECK(stat("/tool", &executable) == 0);
    CHECK(executable.st_uid == 0 && (executable.st_mode & 07777) == 0551);
    int fd = open("/tool", O_RDONLY);
    CHECK(fd == -1 && errno == EACCES);
    fd = open(CG "/cgroup.procs", O_WRONLY);
    CHECK(fd == -1 && errno == EACCES);
    fd = open(INV "/cgroup.procs", O_WRONLY);
    CHECK(fd == -1 && errno == EACCES);
    fd = open(OTHER "/cgroup.procs", O_WRONLY);
    CHECK(fd == -1 && errno == EACCES);
    union bpf_attr attr = {
        .map_type = BPF_MAP_TYPE_ARRAY, .key_size = 4,
        .value_size = 8, .max_entries = 1,
    };
    CHECK(syscall(SYS_bpf, BPF_MAP_CREATE, &attr, sizeof(attr)) == -1);
    CHECK(errno == EPERM);
}

static int tcp(int family, bool server, bool allowed)
{
    int fd = socket(family, SOCK_STREAM | SOCK_CLOEXEC, 0);
    CHECK(fd >= 0);
    struct sockaddr_storage storage = {0};
    socklen_t len;
    if (family == AF_INET) {
        struct sockaddr_in *a = (void *)&storage;
        a->sin_family = family;
        a->sin_port = htons(PORT);
        a->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        len = sizeof(*a);
    } else {
        struct sockaddr_in6 *a = (void *)&storage;
        a->sin6_family = family;
        a->sin6_port = htons(PORT);
        a->sin6_addr = in6addr_loopback;
        len = sizeof(*a);
    }
    if (server) {
        int one = 1;
        CHECK(setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one)) == 0);
        CHECK(bind(fd, (void *)&storage, len) == 0);
        CHECK(listen(fd, 16) == 0);
    } else {
        int rc = connect(fd, (void *)&storage, len);
        if (allowed)
            CHECK(rc == 0);
        else
            CHECK(rc == -1 && errno == EPERM);
    }
    return fd;
}

static void send_fd(int channel, int fd)
{
    char byte = 'S';
    struct iovec io = { .iov_base = &byte, .iov_len = 1 };
    union { struct cmsghdr align; char bytes[CMSG_SPACE(sizeof(int))]; } control = {0};
    struct msghdr msg = {
        .msg_iov = &io, .msg_iovlen = 1,
        .msg_control = control.bytes, .msg_controllen = sizeof(control),
    };
    struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
    c->cmsg_level = SOL_SOCKET;
    c->cmsg_type = SCM_RIGHTS;
    c->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(c), &fd, sizeof(fd));
    CHECK(sendmsg(channel, &msg, 0) == 1);
}

static void receive_denied(int channel)
{
    char byte = 0;
    struct iovec io = { .iov_base = &byte, .iov_len = 1 };
    union { struct cmsghdr align; char bytes[CMSG_SPACE(sizeof(int))]; } control = {0};
    struct msghdr msg = {
        .msg_iov = &io, .msg_iovlen = 1,
        .msg_control = control.bytes, .msg_controllen = sizeof(control),
    };
    /* Linux SCM detachment reports a rejected FD as MSG_CTRUNC, not
     * necessarily a failing recvmsg. Do not mistake data delivery for an FD. */
    CHECK(recvmsg(channel, &msg, 0) == 1);
    CHECK(byte == 'S' && (msg.msg_flags & MSG_CTRUNC));
    CHECK(CMSG_FIRSTHDR(&msg) == NULL);
}

static int tool(bool admitted, int channel)
{
    alarm(20);
    check_unprivileged();
    int v4 = tcp(AF_INET, false, admitted);
    int v6 = tcp(AF_INET6, false, admitted);
    if (admitted) {
        pid_t descendant = fork();
        CHECK(descendant >= 0);
        if (!descendant) {
            close(tcp(AF_INET, false, true));
            close(tcp(AF_INET6, false, true));
            _exit(0);
        }
        wait_ok(descendant);
        send_fd(channel, v4);
        send_fd(channel, v6);
    } else if (channel >= 0) {
        receive_denied(channel);
        receive_denied(channel);
    }
    close(v4);
    close(v6);
    return 0;
}

/* Every API emits exactly one byte; setup uses non-socket FDs. A failure must
 * be EPERM, and PID 1 separately demands exactly one socket_sendmsg denial. */
static void transmit(int fd, int api, bool allowed)
{
    static const char bytes[] = { 'M', 'W', 'V', 'P', '\177' };
    char byte = bytes[api];
    struct iovec io = { .iov_base = &byte, .iov_len = 1 };
    struct msghdr msg = { .msg_iov = &io, .msg_iovlen = 1 };
    ssize_t rc = -1;
    int saved;
    switch (api) {
    case 0:
        rc = sendmsg(fd, &msg, MSG_NOSIGNAL);
        break;
    case 1:
        rc = write(fd, &byte, 1);
        break;
    case 2:
        rc = writev(fd, &io, 1);
        break;
    case 3: {
        int pipefd[2];
        CHECK(pipe2(pipefd, O_CLOEXEC) == 0);
        CHECK(write(pipefd[1], &byte, 1) == 1);
        rc = splice(pipefd[0], NULL, fd, NULL, 1, 0);
        saved = errno;
        close(pipefd[0]);
        close(pipefd[1]);
        errno = saved;
        break;
    }
    case 4: {
        /* Read-only ELF is a regular-file source, not a tmpfs/config dependency. */
        int source = open("/probe.bpf.o", O_RDONLY | O_CLOEXEC);
        CHECK(source >= 0);
        off_t offset = 0;
        rc = sendfile(fd, source, &offset, 1);
        saved = errno;
        close(source);
        errno = saved;
        break;
    }
    default:
        die("unknown transmission API");
    }
    if (allowed)
        CHECK(rc == 1);
    else
        CHECK(rc == -1 && errno == EPERM);
}

static void alternate_paths(pid_t target)
{
    /* These are precise access/absence checks, NOT socket-hook denials. The
     * known target is live, nondumpable, same UID, with the inherited FD 4. */
    int pidfd = syscall(SYS_pidfd_open, target, 0);
    CHECK(pidfd >= 0);
    CHECK(syscall(SYS_pidfd_getfd, pidfd, 4, 0) == -1 && errno == EPERM);
    close(pidfd);
    char path[80];
    snprintf(path, sizeof(path), "/proc/%d/fd/4", target);
    CHECK(open(path, O_RDWR | O_CLOEXEC) == -1 && errno == EACCES);
    snprintf(path, sizeof(path), "/proc/%d/fd/4", getpid());
    CHECK(open(path, O_RDWR | O_CLOEXEC) == -1 && errno == ENXIO);
    struct io_uring_params params = {0};
    CHECK(syscall(SYS_io_uring_setup, 1, &params) == -1 && errno == ENOSYS);
}

static int lifecycle_tool(pid_t target)
{
    alarm(20);
    check_unprivileged();
    char command;
    CHECK(write(3, "R", 1) == 1); /* nondumpable before parent starts probes */
    while (read(3, &command, 1) == 1) {
        if (command == 'X')
            return 0;
        if (command >= '0' && command <= '4') {
            transmit(4, command - '0', true);
        } else if (command >= 'A' && command <= 'E') {
            transmit(4, command - 'A', false);
        } else if (command == 'F') {
            pid_t child = fork();
            CHECK(child >= 0);
            if (!child) {
                CHECK(setsid() >= 0);
                execl("/tool", "/tool", "--descendant", NULL);
                die("exec descendant");
            }
            wait_ok(child);
        } else if (command == 'P') {
            alternate_paths(target);
        } else {
            die("unknown lifecycle command");
        }
        CHECK(write(3, "K", 1) == 1);
    }
    die("unexpected control EOF");
    return 1;
}

/* Narrow exemptions: the caller supplies only control socketpair endpoints.
 * Prove the family and type before assigning a supervisor-only storage value. */
static void control_pair(int storage, int pair[2])
{
    CHECK(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair) == 0);
    for (int i = 0; i < 2; i++) {
        int domain = 0, type = 0;
        socklen_t size = sizeof(int);
        CHECK(getsockopt(pair[i], SOL_SOCKET, SO_DOMAIN, &domain, &size) == 0);
        CHECK(domain == AF_UNIX);
        CHECK(getsockopt(pair[i], SOL_SOCKET, SO_TYPE, &type, &size) == 0);
        CHECK(type == SOCK_SEQPACKET);
        struct socket_label label = { .infrastructure = 1 }, actual = {0};
        CHECK(bpf_map_update_elem(storage, &pair[i], &label, BPF_NOEXIST) == 0);
        CHECK(bpf_map_lookup_elem(storage, &pair[i], &actual) == 0);
        CHECK(!memcmp(&actual, &label, sizeof(label)));
    }
}

static void exact_counters(const char *phase)
{
    for (uint32_t key = 0; key < NCOUNTERS; key++) {
        uint64_t value = 0;
        CHECK(bpf_map_lookup_elem(witnesses, &key, &value) == 0);
        if (value != expected[key]) {
            dprintf(2, "ATTRIBUTION-PROBE:COUNTER:%s:%u actual=%llu expected=%llu\n",
                    phase, key, (unsigned long long)value,
                    (unsigned long long)expected[key]);
            die("exact hook counters");
        }
    }
}

static pid_t launch_lifecycle(bool inside, int channel, int socketfd, pid_t target)
{
    pid_t pid = fork();
    CHECK(pid >= 0);
    if (!pid) {
        put(inside ? INV "/cgroup.procs" : OTHER "/cgroup.procs", "0");
        /* Use high temporary descriptors so dup2 cannot clobber either input. */
        int control = fcntl(channel, F_DUPFD_CLOEXEC, 10);
        int data = fcntl(socketfd, F_DUPFD_CLOEXEC, 10);
        CHECK(control >= 0 && data >= 0);
        CHECK(dup2(control, 3) == 3 && dup2(data, 4) == 4);
        CHECK(syscall(SYS_close_range, 5, ~0U, 0) == 0);
        unprivileged();
        char pidarg[24];
        snprintf(pidarg, sizeof(pidarg), "%d", target);
        execl("/tool", "/tool", "--lifecycle", pidarg, NULL);
        die("exec lifecycle tool");
    }
    return pid;
}

static void ready(int channel)
{
    char byte;
    CHECK(read(channel, &byte, 1) == 1 && byte == 'R');
    expected[INFRA_SEND]++;
    exact_counters("tool-ready");
}

static void command(int channel, char byte, unsigned int allows, unsigned int denies)
{
    CHECK(write(channel, &byte, 1) == 1);
    CHECK(read(channel, &byte, 1) == 1 && byte == 'K');
    expected[INFRA_SEND] += 2;
    expected[SEND_ALLOW] += allows;
    expected[SEND_DENY] += denies;
    exact_counters("command");
}

static void stop_tool(int channel, pid_t pid)
{
    CHECK(write(channel, "X", 1) == 1);
    wait_ok(pid);
    expected[INFRA_SEND]++;
    exact_counters("tool-exit");
    close(channel);
}

static void transmissions(int channel, int peer, bool allowed, const char *phase)
{
    static const char bytes[] = { 'M', 'W', 'V', 'P', '\177' };
    static const char *apis[] = { "sendmsg", "write", "writev", "splice", "sendfile" };
    for (int api = 0; api < 5; api++) {
        command(channel, (allowed ? '0' : 'A') + api, allowed, !allowed);
        char byte;
        if (allowed) {
            CHECK(read(peer, &byte, 1) == 1 && byte == bytes[api]);
        } else {
            CHECK(recv(peer, &byte, 1, MSG_DONTWAIT) == -1 && errno == EAGAIN);
        }
        dprintf(1, "ATTRIBUTION-PROBE:USE:%s:%s:%s\n",
                phase, apis[api], allowed ? "allow" : "deny");
    }
}

static int owned_connection(int family, int listener, int storage,
                            uint64_t id, const struct admission *admission, int *peer)
{
    /* Trusted supervisor establishes the connection in the invocation, then
     * returns to root. Inheritance bypasses SCM_RIGHTS entirely. Tools never
     * get cgroup handles or privilege to perform this migration themselves. */
    put(INV "/cgroup.procs", "0");
    int socketfd = tcp(family, false, true);
    put(CG "/cgroup.procs", "0");
    *peer = accept4(listener, NULL, NULL, SOCK_CLOEXEC);
    CHECK(*peer >= 0);
    struct socket_label label = {0};
    CHECK(bpf_map_lookup_elem(storage, &socketfd, &label) == 0);
    CHECK(label.cgroup == id && label.label == admission->label &&
          label.generation == admission->generation && !label.infrastructure);
    expected[family == AF_INET ? CONNECT4 : CONNECT6]++;
    expected[SNAPSHOT]++;
    exact_counters("owned-connection");
    return socketfd;
}

static void lifecycle(int family, int listener, int map, int storage, uint64_t id)
{
    struct admission admission = {
        .label = 42, .generation = family == AF_INET ? 2 : 4, .active = 1,
    };
    CHECK(bpf_map_update_elem(map, &id, &admission, BPF_ANY) == 0);
    struct stat other_st;
    CHECK(stat(OTHER, &other_st) == 0 && other_st.st_uid == 0 &&
          (other_st.st_mode & 0777) == 0700);
    uint64_t other_id = other_st.st_ino;
    CHECK(other_id && other_id != id);
    struct admission other_admission = {
        .label = 43, .generation = admission.generation, .active = 1,
    };
    CHECK(bpf_map_update_elem(map, &other_id, &other_admission, BPF_ANY) == 0);
    int peer, socketfd = owned_connection(family, listener, storage, id, &admission, &peer);
    int pair[2];
    control_pair(storage, pair);
    pid_t inside = launch_lifecycle(true, pair[1], socketfd, 0);
    close(pair[1]);
    ready(pair[0]);
    transmissions(pair[0], peer, true, "active");
    command(pair[0], 'F', 5, 0);
    const char bytes[] = { 'M', 'W', 'V', 'P', '\177' };
    for (int i = 0; i < 5; i++) {
        char byte;
        CHECK(read(peer, &byte, 1) == 1 && byte == bytes[i]);
    }
    dprintf(1, "ATTRIBUTION-PROBE:INHERIT:fork-exec-setsid:allow\n");
    int other[2];
    control_pair(storage, other);
    pid_t outside = launch_lifecycle(false, other[1], socketfd, inside);
    close(other[1]);
    ready(other[0]);
    transmissions(other[0], peer, false, "cross-cgroup-inherited");
    command(other[0], 'P', 0, 0);
    dprintf(1, "ATTRIBUTION-PROBE:ALTERNATE:pidfd-EPERM,procfd-EACCES,self-procfd-ENXIO,io_uring-ENOSYS\n");
    stop_tool(other[0], outside);
    /* The peer and both copies of the established TCP socket remain open. */
    admission.active = 0;
    CHECK(bpf_map_update_elem(map, &id, &admission, BPF_ANY) == 0);
    transmissions(pair[0], peer, false, "retired");
    admission.active = 1;
    admission.generation++;
    CHECK(bpf_map_update_elem(map, &id, &admission, BPF_ANY) == 0);
    transmissions(pair[0], peer, false, "replaced-generation");
    stop_tool(pair[0], inside);
    close(socketfd);
    close(peer);
    /* Generation replacement is not a global outage: a new socket can send. */
    socketfd = owned_connection(family, listener, storage, id, &admission, &peer);
    control_pair(storage, pair);
    inside = launch_lifecycle(true, pair[1], socketfd, 0);
    close(pair[1]);
    ready(pair[0]);
    transmissions(pair[0], peer, true, "new-generation");
    stop_tool(pair[0], inside);
    close(socketfd);
    close(peer);
    CHECK(bpf_map_delete_elem(map, &other_id) == 0);
}

static pid_t launch(bool join_invocation, bool admitted, int channel)
{
    pid_t pid = fork();
    CHECK(pid >= 0);
    if (!pid) {
        if (join_invocation)
            put(INV "/cgroup.procs", "0");
        /* Never inherit supervisor listener, program, link, map or cgroup FDs.
         * Retain only the deliberately supplied UNIX transport as fd 3. */
        if (channel >= 0) {
            CHECK(dup2(channel, 3) == 3);
            CHECK(fcntl(3, F_SETFD, 0) == 0);
        } else {
            close(3);
        }
        CHECK(syscall(SYS_close_range, 4, ~0U, 0) == 0);
        unprivileged();
        execl("/tool", "/tool", "--tool", admitted ? "yes" : "no",
              channel >= 0 ? "3" : "-1", NULL);
        die("exec sealed tool");
    }
    return pid;
}

static struct bpf_link *attach(struct bpf_object *obj, const char *name, int cg)
{
    struct bpf_program *p = bpf_object__find_program_by_name(obj, name);
    CHECK(p != NULL);
    struct bpf_link *link = cg < 0 ? bpf_program__attach_lsm(p)
                                  : bpf_program__attach_cgroup(p, cg);
    CHECK(link != NULL && !libbpf_get_error(link));
    return link;
}

static void negative_loader_checks(void)
{
    struct bpf_object *missing = bpf_object__open_file("/unavailable.bpf.o", NULL);
    CHECK(!missing || libbpf_get_error(missing));
    const char invalid[] = "not-an-ELF-BPF-program";
    struct bpf_object *bad = bpf_object__open_mem(invalid, sizeof(invalid), NULL);
    CHECK(!bad || libbpf_get_error(bad));
    /* These test parser refusal only, not verifier rejection/fault injection. */
}

#include "bridge.c"
#include "exec.c"
#include "server.c"

int main(int argc, char **argv)
{
    signal(SIGALRM, deadline);
    signal(SIGPIPE, SIG_IGN);
    if (argc == 6 && !strcmp(argv[1], "--server-fixture"))
        return server_fixture(atoi(argv[2]), atoi(argv[3]), atoi(argv[4]), atoi(argv[5]));
    if (argc == 6 && !strcmp(argv[1], "--server-client"))
        return server_client(atoi(argv[2]), atoi(argv[3]), atoi(argv[5]));
    if (argc == 2 && !strcmp(argv[1], "--exec-tool"))
        return exec_tool();
    if (argc >= 2 && !strcmp(argv[1], "--bridge-egress"))
        return bridge_egress();
    if (argc == 4 && !strcmp(argv[1], "--bridge-tool"))
        return bridge_tool(atoi(argv[2]), atoi(argv[3]));
    if (argc == 3 && !strcmp(argv[1], "--lifecycle"))
        return lifecycle_tool(atoi(argv[2]));
    if (argc == 2 && !strcmp(argv[1], "--descendant")) {
        alarm(20);
        check_unprivileged();
        for (int api = 0; api < 5; api++)
            transmit(4, api, true);
        return 0;
    }
    if (argc == 4 && !strcmp(argv[1], "--tool"))
        return tool(!strcmp(argv[2], "yes"), atoi(argv[3]));
    CHECK(getpid() == 1);
    alarm(60);
    CHECK(mount("proc", "/proc", "proc", MS_NOSUID | MS_NOEXEC | MS_NODEV, NULL) == 0);
    CHECK(mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NOEXEC | MS_NODEV, NULL) == 0);
    CHECK(mount("cgroup2", CG, "cgroup2", MS_NOSUID | MS_NOEXEC | MS_NODEV, NULL) == 0);
    CHECK(mkdir(INV, 0700) == 0);
    CHECK(mkdir(OTHER, 0700) == 0);
    /* SECURITYFS is deliberately off in the inherited kernel config. The
     * 6.12 LSM syscall lists active modules without that filesystem. */
    uint64_t lsms[16] = {0};
    uint32_t lsm_size = sizeof(lsms);
    long lsm_count = syscall(SYS_lsm_list_modules, lsms, &lsm_size, 0);
    if (lsm_count < 0)
        die("lsm_list_modules unavailable or failed");
    CHECK(lsm_count > 0 && lsm_count <= 16);
    bool bpf_active = false;
    for (long i = 0; i < lsm_count; i++)
        bpf_active |= lsms[i] == LSM_ID_BPF;
    CHECK(bpf_active);
    CHECK(access("/sys/kernel/btf/vmlinux", R_OK) == 0);
    put("/proc/sys/kernel/unprivileged_bpf_disabled", "1");
    int fd = open("/proc/sys/kernel/unprivileged_bpf_disabled", O_RDONLY | O_CLOEXEC);
    char setting = 0;
    CHECK(fd >= 0 && read(fd, &setting, 1) == 1 && setting == '1');
    close(fd);
    struct rlimit unlimited = { RLIM_INFINITY, RLIM_INFINITY };
    CHECK(setrlimit(RLIMIT_MEMLOCK, &unlimited) == 0);
    /* Loopback only. No guest network device, route, firewall, or namespace. */
    fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    CHECK(fd >= 0);
    struct ifreq lo = { .ifr_name = "lo" };
    CHECK(ioctl(fd, SIOCGIFFLAGS, &lo) == 0);
    lo.ifr_flags |= IFF_UP;
    CHECK(ioctl(fd, SIOCSIFFLAGS, &lo) == 0);
    close(fd);
    negative_loader_checks();
    capability_viability();
    struct bpf_object *obj = bpf_object__open_file("/probe.bpf.o", NULL);
    CHECK(obj != NULL && !libbpf_get_error(obj));
    CHECK(bpf_object__load(obj) == 0); /* Preserve libbpf/verifier stderr. */
    int cg = open(CG, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    CHECK(cg >= 0);
    int v4 = root_guard(obj, "connect4", cg, BPF_CGROUP_INET4_CONNECT);
    int v6 = root_guard(obj, "connect6", cg, BPF_CGROUP_INET6_CONNECT);
    struct bpf_link *receive = attach(obj, "receive", -1);
    struct bpf_link *label = attach(obj, "label_connect", -1);
    struct bpf_link *use = attach(obj, "use_socket", -1);
    int map = bpf_object__find_map_fd_by_name(obj, "invocation");
    int storage = bpf_object__find_map_fd_by_name(obj, "socket_labels");
    witnesses = bpf_object__find_map_fd_by_name(obj, "witnessed");
    CHECK(map >= 0 && storage >= 0 && witnesses >= 0);
    int listen4 = tcp(AF_INET, true, true);
    int listen6 = tcp(AF_INET6, true, true);
    /* A zero/absent admission is fail-closed before any authorized workload. */
    wait_ok(launch(false, false, -1));
    struct stat st;
    CHECK(stat(INV, &st) == 0 && st.st_uid == 0 && (st.st_mode & 0777) == 0700);
    uint64_t id = st.st_ino;
    struct admission admission = { .label = 42, .generation = 1, .active = 1 };
    CHECK(id != 0 && bpf_map_update_elem(map, &id, &admission, BPF_ANY) == 0);
    int pair[2];
    control_pair(storage, pair);
    pid_t outside = launch(false, false, pair[0]);
    pid_t inside = launch(true, true, pair[1]);
    close(pair[0]);
    close(pair[1]);
    wait_ok(inside);
    wait_ok(outside);
    /* Receive real data-plane connections; four = tool + descendant/family. */
    for (int i = 0; i < 2; i++) {
        fd = accept4(listen4, NULL, NULL, SOCK_CLOEXEC);
        CHECK(fd >= 0);
        close(fd);
        fd = accept4(listen6, NULL, NULL, SOCK_CLOEXEC);
        CHECK(fd >= 0);
        close(fd);
    }
    const uint64_t initial[NCOUNTERS] = { 2, 2, 2, 2, 2, 4, 0, 0, 2 };
    memcpy(expected, initial, sizeof(expected));
    exact_counters("connect-and-SCM");
    lifecycle(AF_INET, listen4, map, storage, id);
    lifecycle(AF_INET6, listen6, map, storage, id);
    CHECK(bpf_map_delete_elem(map, &id) == 0);
    CHECK(rmdir(INV) == 0);
    CHECK(mkdir(INV, 0700) == 0);
    /* Path reuse is not kernel cgroup-ID wraparound or concurrent teardown. */
    wait_ok(launch(true, false, -1));
    expected[CONNECT4_DENY]++;
    expected[CONNECT6_DENY]++;
    exact_counters("recreated-cgroup");
    bridge_probe(obj, map, storage);
    exec_probe(obj);
    for (uint32_t key = 0; key < NCOUNTERS; key++) {
        dprintf(1, "ATTRIBUTION-PROBE:WITNESS:%u=%llu\n",
                key, (unsigned long long)expected[key]);
    }
    dprintf(1, "ATTRIBUTION-PROBE:PASS:connect4-connect6-file_receive-partial\n");
    dprintf(1, "ATTRIBUTION-PROBE:PASS:socket-generation-actual-use-revocation-partial\n");
    dprintf(1, "ATTRIBUTION-PROBE:UNSUPPORTED:production-egress-bridge,claim-protocol,verifier-faults,exec-identity,concurrent-teardown,non-TCP\n");
    server_probe(obj, map);
    (void)v4; (void)v6; (void)receive; (void)label; (void)use;
    /* Links kept live until guest shutdown. */
    sync();
    reboot(RB_POWER_OFF);
    die("poweroff");
}
