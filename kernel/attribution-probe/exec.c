/* Native ELF, task-local one-shot admission development fixture only.
 * Included after bridge.c so all earlier witnesses run unchanged. */
#include <elf.h>
#include <sys/sysmacros.h>

#define EXEC_CG CG "/exec-probe"
#define EXEC_DIR "/exec-fixtures"
struct exec_inode { uint64_t dev, ino; };
struct exec_permit {
    uint64_t cgroup, generation;
    struct exec_inode file;
};
static char *const exec_args[] = { "/tool", "--exec-tool", NULL };
static char *const exec_env[] = { NULL };

static void exec_byte(int fd, char byte)
{
    CHECK(write(fd, &byte, 1) == 1);
}

static char exec_read(int fd)
{
    char byte;
    CHECK(read(fd, &byte, 1) == 1);
    return byte;
}

/* Pipes are deliberately not socket infrastructure exemptions. Each ack
 * stops the task while PID 1 reads exact counters and task-local storage. */
static void exec_event(char event)
{
    exec_byte(4, event);
    CHECK(exec_read(3) == event);
}

static void exec_denied(const char *path, int method)
{
    int fd = -1;
    if (method) {
        fd = open(path, O_PATH | O_CLOEXEC);
        CHECK(fd >= 0);
    }
    errno = 0;
    int rc = method == 2 ? fexecve(fd, exec_args, exec_env) :
             method == 1 ? syscall(SYS_execveat, fd, "", exec_args, exec_env,
                                   AT_EMPTY_PATH) :
             execve(path, exec_args, exec_env);
    CHECK(rc == -1 && errno == EPERM);
    if (fd >= 0)
        CHECK(close(fd) == 0);
    exec_event('D');
}

static void exec_identity(void)
{
    struct __user_cap_header_struct hdr = { .version = _LINUX_CAPABILITY_VERSION_3 };
    struct __user_cap_data_struct caps[2] = {0};
    CHECK(getuid() == 902 && geteuid() == 902 && getgid() == 907 && getegid() == 907);
    CHECK(syscall(SYS_capget, &hdr, caps) == 0);
    for (int i = 0; i < 2; i++)
        CHECK(!caps[i].effective && !caps[i].permitted && !caps[i].inheritable);
    for (int i = 0; i <= CAP_LAST_CAP; i++) {
        CHECK(prctl(PR_CAPBSET_READ, i, 0, 0, 0) == 0);
        CHECK(prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_IS_SET, i, 0, 0) == 0);
    }
    CHECK(prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) == 1);
    CHECK(prctl(PR_SET_DUMPABLE, 0) == 0 && prctl(PR_GET_DUMPABLE) == 0);
    struct stat st;
    CHECK(stat("/tool", &st) == 0 && st.st_uid == 0 && (st.st_mode & 07777) == 0551);
    CHECK(open("/tool", O_RDONLY | O_CLOEXEC) == -1 && errno == EACCES);
}

static int exec_tool(void)
{
    alarm(20);
    exec_identity();
    /* On successful exec the O_PATH launch descriptor was CLOEXEC. The two
     * remaining descriptors are pipes, not root map/link/cgroup descriptors. */
    CHECK(fcntl(5, F_GETFD) == -1 && errno == EBADF);
    exec_event('A');
    exec_denied("/proc/self/exe", 0);
    exec_denied("/proc/self/exe", 1);
    exec_denied("/proc/self/exe", 2);
    exec_denied("/tool-copy", 0);
    exec_denied(EXEC_DIR "/foreign", 0);
    exec_denied("/tool-hard", 0);
    exec_denied(EXEC_DIR "/bind", 0);
    exec_denied("/exec-script", 0);
    exec_event('X');
    return 0;
}

static void exec_drop(void)
{
    CHECK(prctl(PR_SET_SECUREBITS, SECBIT_NOROOT | SECBIT_NOROOT_LOCKED) == 0);
    for (int i = 0; i <= CAP_LAST_CAP; i++)
        CHECK(prctl(PR_CAPBSET_DROP, i, 0, 0, 0) == 0);
    CHECK(setgroups(0, NULL) == 0);
    CHECK(setresgid(907, 907, 907) == 0);
    CHECK(setresuid(902, 902, 902) == 0);
    CHECK(prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0);
    exec_identity();
}

static struct exec_inode exec_file(int fd)
{
    struct stat st;
    CHECK(fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == 0 &&
          (st.st_mode & 07777) == 0551);
    /* Linux s_dev is internal dev_t (major << 20 | minor), not the encoded
     * userspace st_dev. Both architectures use this explicit conversion. */
    return (struct exec_inode) {
        .dev = ((uint64_t)major(st.st_dev) << 20) | minor(st.st_dev),
        .ino = st.st_ino,
    };
}

static void exec_native_contract(void)
{
    int fd = open("/tool", O_RDONLY | O_CLOEXEC);
    CHECK(fd >= 0);
    Elf64_Ehdr elf;
    CHECK(pread(fd, &elf, sizeof(elf), 0) == sizeof(elf));
    CHECK(!memcmp(elf.e_ident, ELFMAG, SELFMAG) &&
          elf.e_ident[EI_CLASS] == ELFCLASS64 &&
          elf.e_ident[EI_DATA] == ELFDATA2LSB &&
          elf.e_ident[EI_VERSION] == EV_CURRENT &&
          (elf.e_type == ET_EXEC || elf.e_type == ET_DYN));
#if defined(__x86_64__)
    CHECK(elf.e_machine == EM_X86_64);
#elif defined(__aarch64__)
    CHECK(elf.e_machine == EM_AARCH64);
#else
#error Unsupported native probe architecture
#endif
    CHECK(elf.e_phentsize == sizeof(Elf64_Phdr) && elf.e_phnum > 0 && elf.e_phnum < 128);
    unsigned interpreters = 0;
    for (unsigned i = 0; i < elf.e_phnum; i++) {
        Elf64_Phdr ph;
        CHECK(pread(fd, &ph, sizeof(ph), elf.e_phoff + i * sizeof(ph)) == sizeof(ph));
        if (ph.p_type == PT_INTERP) {
            char path[512] = {0};
            CHECK(ph.p_filesz > 1 && ph.p_filesz <= sizeof(path));
            CHECK(pread(fd, path, ph.p_filesz, ph.p_offset) == (ssize_t)ph.p_filesz);
            CHECK(path[0] == '/' && path[ph.p_filesz - 1] == 0);
            struct stat st;
            CHECK(stat(path, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == 0 &&
                  !(st.st_mode & 0022));
            interpreters++;
        }
    }
    /* The dynamically linked native fixture MUST exercise PT_INTERP.
     * The runtime counter gate below must prove one bprm hook, not assume it
     * from the presence of ELF magic or confuse PT_INTERP with script rewrite. */
    CHECK(interpreters == 1);
    CHECK(close(fd) == 0);
}

static void exec_fixtures(void)
{
    CHECK(mount("tmpfs", EXEC_DIR, "tmpfs", MS_NOSUID | MS_NODEV, "mode=0755,size=32m") == 0);
    int source = open("/tool", O_RDONLY | O_CLOEXEC);
    int dest = open(EXEC_DIR "/foreign", O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0551);
    CHECK(source >= 0 && dest >= 0);
    char buf[4096];
    ssize_t n;
    while ((n = read(source, buf, sizeof(buf))) > 0)
        CHECK(write(dest, buf, n) == n);
    CHECK(n == 0 && fchmod(dest, 0551) == 0);
    CHECK(close(source) == 0 && close(dest) == 0);
    dest = open(EXEC_DIR "/bind", O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0551);
    CHECK(dest >= 0 && close(dest) == 0);
    CHECK(mount("/tool", EXEC_DIR "/bind", NULL, MS_BIND, NULL) == 0);
    CHECK(mount(NULL, EXEC_DIR "/bind", NULL, MS_BIND | MS_REMOUNT | MS_RDONLY |
                MS_NOSUID | MS_NODEV, NULL) == 0);
    CHECK(mount(NULL, EXEC_DIR, NULL, MS_REMOUNT | MS_RDONLY | MS_NOSUID |
                MS_NODEV, NULL) == 0);
    struct stat tool, copy, foreign, hard, bind;
    CHECK(stat("/tool", &tool) == 0 && stat("/tool-copy", &copy) == 0 &&
          stat(EXEC_DIR "/foreign", &foreign) == 0 &&
          stat("/tool-hard", &hard) == 0 && stat(EXEC_DIR "/bind", &bind) == 0);
    CHECK(copy.st_dev == tool.st_dev && copy.st_ino != tool.st_ino);
    CHECK(foreign.st_dev != tool.st_dev);
    CHECK(hard.st_dev == tool.st_dev && hard.st_ino == tool.st_ino);
    CHECK(bind.st_dev == tool.st_dev && bind.st_ino == tool.st_ino);
}

static void exec_exact(int map, const uint64_t totals[3], const char *phase)
{
    for (uint32_t key = 0; key < 3; key++) {
        uint64_t value;
        CHECK(bpf_map_lookup_elem(map, &key, &value) == 0);
        if (value != totals[key])
            die(phase);
    }
    exact_counters("exec-keeps-original-socket-counters");
}

static void exec_probe(struct bpf_object *obj)
{
    exec_native_contract();
    exec_fixtures();
    int scope_map = bpf_object__find_map_fd_by_name(obj, "exec_scope");
    int inodes = bpf_object__find_map_fd_by_name(obj, "exec_inodes");
    int permits = bpf_object__find_map_fd_by_name(obj, "exec_permits");
    int counters = bpf_object__find_map_fd_by_name(obj, "exec_witness");
    CHECK(scope_map >= 0 && inodes >= 0 && permits >= 0 && counters >= 0);
    int tool = open("/tool", O_PATH | O_CLOEXEC);
    CHECK(tool >= 0);
    struct exec_inode identity = exec_file(tool);
    uint64_t native = 1;
    CHECK(bpf_map_update_elem(inodes, &identity, &native, BPF_NOEXIST) == 0);
    CHECK(bpf_map_freeze(inodes) == 0); /* Bounded immutable boot-local inode set. */
    CHECK(bpf_map_update_elem(inodes, &identity, &native, BPF_ANY) == -1 &&
          errno == EPERM);
    CHECK(mkdir(EXEC_CG, 0700) == 0);
    struct stat st;
    CHECK(stat(EXEC_CG, &st) == 0 && st.st_uid == 0 && (st.st_mode & 0777) == 0700);
    uint64_t cg = st.st_ino;
    struct admission scope = { .label = 1, .generation = 1, .active = 1 };
    struct bpf_link *link = attach(obj, "exec_admit", -1);
    CHECK(bpf_map_update_elem(scope_map, &cg, &scope, BPF_NOEXIST) == 0);
    uint64_t totals[3] = {0};
    /* Initial success with fork-before-exec; absent, revoked, changed
     * generation, unknown files; then isolate each permit identity predicate
     * against the positively whitelisted native /tool in the active scope. */
    enum { WRONG_CGROUP = 7, WRONG_INODE, WRONG_DEVICE };
    const char *paths[] = { "/tool", "/tool", "/tool", "/tool",
                           "/tool-copy", EXEC_DIR "/foreign", "/exec-script",
                           "/tool", "/tool", "/tool" };
    for (unsigned trial = 0; trial < sizeof(paths) / sizeof(paths[0]); trial++) {
        scope.active = 1;
        CHECK(bpf_map_update_elem(scope_map, &cg, &scope, BPF_EXIST) == 0);
        int gate[2], events[2];
        CHECK(pipe2(gate, O_CLOEXEC) == 0 && pipe2(events, O_CLOEXEC) == 0);
        pid_t child = fork();
        CHECK(child >= 0);
        if (!child) {
            alarm(20);
            put(EXEC_CG "/cgroup.procs", "0");
            /* Duplicate above the targets first: never overwrite a source FD. */
            int g = fcntl(gate[0], F_DUPFD_CLOEXEC, 10);
            int e = fcntl(events[1], F_DUPFD_CLOEXEC, 10);
            int executable = fcntl(tool, F_DUPFD_CLOEXEC, 10);
            CHECK(g >= 0 && e >= 0 && executable >= 0);
            CHECK(dup2(g, 3) == 3 && dup2(e, 4) == 4 && dup3(executable, 5, O_CLOEXEC) == 5);
            CHECK(syscall(SYS_close_range, 6, ~0U, 0) == 0);
            exec_drop();
            CHECK(exec_read(3) == 'G');
            if (!trial) {
                pid_t descendant = fork();
                CHECK(descendant >= 0);
                if (!descendant) {
                    exec_denied("/tool", 0);
                    _exit(0);
                }
                wait_ok(descendant);
                syscall(SYS_execveat, 5, "", exec_args, exec_env, AT_EMPTY_PATH);
                die("initial one-shot native execveat");
            }
            exec_denied(paths[trial], 0);
            exec_event('X');
            _exit(0);
        }
        CHECK(close(gate[0]) == 0 && close(events[1]) == 0);
        int pidfd = syscall(SYS_pidfd_open, child, 0);
        CHECK(pidfd >= 0);
        struct exec_permit permit = { .cgroup = cg, .generation = scope.generation,
                                      .file = identity }, readback;
        /* Change only one permit field. Actual cgroup, generation and opened
         * file remain valid; whitelist failure cannot mask these comparisons. */
        if (trial == WRONG_CGROUP)
            permit.cgroup ^= UINT64_C(1);
        if (trial == WRONG_INODE)
            permit.file.ino ^= UINT64_C(1);
        if (trial == WRONG_DEVICE)
            permit.file.dev ^= UINT64_C(1);
        if (trial != 1) {
            CHECK(bpf_map_update_elem(permits, &pidfd, &permit, BPF_NOEXIST) == 0);
            CHECK(bpf_map_lookup_elem(permits, &pidfd, &readback) == 0 &&
                  !memcmp(&permit, &readback, sizeof(permit)));
        }
        if (trial == 2)
            scope.active = 0;
        if (trial == 3)
            scope.generation++;
        CHECK(bpf_map_update_elem(scope_map, &cg, &scope, BPF_EXIST) == 0);
        exec_byte(gate[1], 'G');
        unsigned allows = 0, denies = 0;
        for (;;) {
            char event = exec_read(events[0]);
            if (event == 'X')
                break;
            CHECK(event == 'A' || event == 'D');
            totals[0]++;
            totals[event == 'A' ? 1 : 2]++;
            allows += event == 'A';
            denies += event == 'D';
            exec_exact(counters, totals, paths[trial]);
            if (event == 'A')
               /* libbpf returns -errno, unlike the raw syscall's -1. */
               CHECK(bpf_map_lookup_elem(permits, &pidfd, &readback) == -ENOENT);
            if (trial == 0 && event == 'D' && allows == 0)
                CHECK(bpf_map_lookup_elem(permits, &pidfd, &readback) == 0 &&
                      !memcmp(&permit, &readback, sizeof(permit)));
            exec_byte(gate[1], event);
        }
        CHECK(allows == (trial == 0 ? 1U : 0U));
        CHECK(denies == (trial == 0 ? 9U : 1U));
        /* Child remains alive, gated on X. Expire unused permits and advance
         * generation BEFORE releasing/reaping it. No PID reuse is simulated. */
        if (trial > 1) {
            CHECK(bpf_map_lookup_elem(permits, &pidfd, &readback) == 0 &&
                  !memcmp(&permit, &readback, sizeof(permit)));
            CHECK(bpf_map_delete_elem(permits, &pidfd) == 0);
        }
        CHECK(bpf_map_lookup_elem(permits, &pidfd, &readback) == -1 && errno == ENOENT);
        scope.active = 0;
        scope.generation++;
        CHECK(bpf_map_update_elem(scope_map, &cg, &scope, BPF_EXIST) == 0);
        exec_exact(counters, totals, "cleanup-before-child-reap");
        exec_byte(gate[1], 'X');
        wait_ok(child);
        CHECK(close(pidfd) == 0 && close(gate[1]) == 0 && close(events[0]) == 0);
    }
    CHECK(totals[0] == 19 && totals[1] == 1 && totals[2] == 18);
    CHECK(close(tool) == 0);
    CHECK(rmdir(EXEC_CG) == 0);
    /* Keep inactive scope tombstone and LSM link live until poweroff. */
    (void)link;
    dprintf(1, "ATTRIBUTION-PROBE:EXEC-HOOKS:19:allow=1:deny=18:PT_INTERP-native=1\n");
    dprintf(1, "ATTRIBUTION-PROBE:PASS:native-ELF-task-storage-one-shot-exec-admission-partial\n");
    dprintf(1, "ATTRIBUTION-PROBE:UNSUPPORTED:exec-byte-attestation,script-chains,production-exec-decision,concurrent-exec-revocation\n");
}
