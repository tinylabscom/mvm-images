#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/bpf.h>
#include <linux/capability.h>
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
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <grp.h>
#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#define CG "/sys/fs/cgroup"
#define INV CG "/invocation"
#define PORT 19042

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
    (void)write(2, marker, sizeof(marker) - 1);
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

int main(int argc, char **argv)
{
    signal(SIGALRM, deadline);
    if (argc == 4 && !strcmp(argv[1], "--tool"))
        return tool(!strcmp(argv[2], "yes"), atoi(argv[3]));
    CHECK(getpid() == 1);
    alarm(60);
    CHECK(mount("proc", "/proc", "proc", MS_NOSUID | MS_NOEXEC | MS_NODEV, NULL) == 0);
    CHECK(mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NOEXEC | MS_NODEV, NULL) == 0);
    CHECK(mount("cgroup2", CG, "cgroup2", MS_NOSUID | MS_NOEXEC | MS_NODEV, NULL) == 0);
    CHECK(mkdir(INV, 0700) == 0);
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
    struct bpf_object *obj = bpf_object__open_file("/probe.bpf.o", NULL);
    CHECK(obj != NULL && !libbpf_get_error(obj));
    CHECK(bpf_object__load(obj) == 0); /* Preserve libbpf/verifier stderr. */
    int cg = open(CG, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    CHECK(cg >= 0);
    struct bpf_link *v4 = attach(obj, "connect4", cg);
    struct bpf_link *v6 = attach(obj, "connect6", cg);
    struct bpf_link *receive = attach(obj, "receive", -1);
    int map = bpf_object__find_map_fd_by_name(obj, "invocation");
    CHECK(map >= 0);
    int listen4 = tcp(AF_INET, true, true);
    int listen6 = tcp(AF_INET6, true, true);
    /* A zero/absent admission is fail-closed before any authorized workload. */
    wait_ok(launch(false, false, -1));
    struct stat st;
    CHECK(stat(INV, &st) == 0 && st.st_uid == 0 && (st.st_mode & 0777) == 0700);
    uint32_t key = 0;
    uint64_t id = st.st_ino;
    CHECK(id != 0 && bpf_map_update_elem(map, &key, &id, BPF_ANY) == 0);
    int pair[2];
    CHECK(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair) == 0);
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
    id = 0;
    CHECK(bpf_map_update_elem(map, &key, &id, BPF_ANY) == 0);
    CHECK(rmdir(INV) == 0);
    CHECK(mkdir(INV, 0700) == 0);
    /* The reused path remains unadmitted. Full concurrent lifetime/reuse and
     * already-connected socket use are intentionally NOT covered. */
    wait_ok(launch(true, false, -1));
    int witnesses = bpf_object__find_map_fd_by_name(obj, "witnessed");
    CHECK(witnesses >= 0);
    const uint64_t minimum[] = { 2, 2, 2, 3, 3 };
    for (key = 0; key < 5; key++) {
        uint64_t value = 0;
        CHECK(bpf_map_lookup_elem(witnesses, &key, &value) == 0);
        CHECK(value >= minimum[key]);
        dprintf(1, "ATTRIBUTION-PROBE:WITNESS:%u=%llu\n",
                key, (unsigned long long)value);
    }
    dprintf(1, "ATTRIBUTION-PROBE:PASS:connect4-connect6-file_receive-partial\n");
    dprintf(1, "ATTRIBUTION-PROBE:UNSUPPORTED:socket-use-lifecycle,claim-protocol,verifier-faults,exec-identity\n");
    (void)v4; (void)v6; (void)receive; /* Links kept live until guest shutdown. */
    sync();
    reboot(RB_POWER_OFF);
    die("poweroff");
}
