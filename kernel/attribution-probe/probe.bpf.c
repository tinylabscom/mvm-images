/* Development TCP lifecycle experiment only, not a production policy. */
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_endian.h>
#include <linux/in.h>
#include <linux/in6.h>

struct super_block {
    __u32 s_dev;
} __attribute__((preserve_access_index));
struct inode {
    unsigned short i_mode;
    unsigned long i_ino;
    struct super_block *i_sb;
} __attribute__((preserve_access_index));
struct file {
    struct inode *f_inode;
} __attribute__((preserve_access_index));
struct linux_binprm {
    struct file *file;
    char buf[256];
} __attribute__((preserve_access_index));
struct task_struct;
struct sock_common {
    __u16 skc_family, skc_dport;
    __u16 skc_num;
    unsigned char skc_state;
    __u32 skc_daddr, skc_rcv_saddr;
    struct in6_addr skc_v6_daddr, skc_v6_rcv_saddr;
} __attribute__((preserve_access_index));
struct sock {
    struct sock_common __sk_common;
    __u16 sk_type, sk_protocol;
} __attribute__((preserve_access_index));
struct socket {
    struct sock *sk;
} __attribute__((preserve_access_index));
struct sockaddr;
struct msghdr;

struct admission {
    __u64 label, generation, active;
};
struct socket_label {
    __u64 cgroup, label, generation, infrastructure;
};

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 8);
    __type(key, __u64);
    __type(value, struct admission);
} invocation SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_SK_STORAGE);
    __uint(map_flags, BPF_F_NO_PREALLOC);
    __type(key, int);
    __type(value, struct socket_label);
} socket_labels SEC(".maps");

struct bridge_slot {
    __u32 port, active;
};
struct {
    __uint(type, BPF_MAP_TYPE_CGROUP_STORAGE);
    __type(key, struct bpf_cgroup_storage_key);
    __type(value, struct bridge_slot);
} bridge_slots SEC(".maps");

/* 0/1 allowed connect; 2 refused receive; 3/4 denied connect;
 * 5 snapshot creation; 6 allowed send; 7 denied send; 8 infrastructure send. */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 13);
    __type(key, __u32);
    __type(value, __u64);
} witnessed SEC(".maps");

static __always_inline void count(__u32 key)
{
    __u64 *value = bpf_map_lookup_elem(&witnessed, &key);
    if (value)
        __sync_fetch_and_add(value, 1);
}

static __always_inline struct admission *current_admission(void)
{
    __u64 id = bpf_get_current_cgroup_id();
    struct admission *a = bpf_map_lookup_elem(&invocation, &id);
    return a && a->active && a->label && a->generation ? a : 0;
}

static __always_inline int normal_proxy_allowed(void)
{
    __u64 id = bpf_get_current_cgroup_id();
    struct admission *a = bpf_map_lookup_elem(&invocation, &id);
    /* A retired/invalid record is not an ordinary unbound workload. Keep
     * its tombstone authoritative instead of falling through to port 1080. */
    return !a || (a->active && a->label && a->generation);
}

static __always_inline int normal_destination(struct sock *sk)
{
    if (sk->__sk_common.skc_dport != bpf_htons(1080))
        return 0;
    if (sk->__sk_common.skc_family == 2)
        return sk->__sk_common.skc_daddr == bpf_htonl(0x7f000001);
    if (sk->__sk_common.skc_family == 10) {
        struct in6_addr addr = BPF_CORE_READ(sk, __sk_common.skc_v6_daddr);
        return !addr.s6_addr32[0] && !addr.s6_addr32[1] && !addr.s6_addr32[2] &&
               addr.s6_addr32[3] == bpf_htonl(1);
    }
    return 0;
}

/* Isolated development server ABI: every field is a native-endian u64.
 * The port key and snapshot port are HOST order, unlike skc_dport.
 * Neither the old client storage ABI nor its counter array changes. */
struct server_snapshot {
    __u64 cgroup, generation, port, role;
};
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 2);
    __type(key, __u64);
    __type(value, struct server_snapshot);
} port_leases SEC(".maps");
struct {
    __uint(type, BPF_MAP_TYPE_SK_STORAGE);
    __uint(map_flags, BPF_F_NO_PREALLOC);
    __type(key, int);
    __type(value, struct server_snapshot);
} server_tags SEC(".maps");
/* bind-tag, bind-deny, accept-tag, accept-deny, server-send, server-deny. */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 6);
    __type(key, __u32);
    __type(value, __u64);
} server_witness SEC(".maps");

static __always_inline void server_count(__u32 key)
{
    __u64 *value = bpf_map_lookup_elem(&server_witness, &key);
    if (value)
        __sync_fetch_and_add(value, 1);
}

static __always_inline int server_live(const struct server_snapshot *s)
{
    struct admission *a = bpf_map_lookup_elem(&invocation, &s->cgroup);
    return s->cgroup && s->generation && a && a->active && a->label &&
           a->generation == s->generation;
}

static __always_inline int server_tuple(struct sock *sk,
                                        const struct server_snapshot *s)
{
    if (!sk || sk->sk_type != 1 || sk->sk_protocol != 6 ||
        sk->__sk_common.skc_num != s->port ||
        (s->port != 904 && s->port != 905))
        return 0;
    if (sk->__sk_common.skc_family == 2)
        return sk->__sk_common.skc_rcv_saddr == bpf_htonl(0x7f000001);
    if (sk->__sk_common.skc_family == 10) {
        struct in6_addr addr = BPF_CORE_READ(sk, __sk_common.skc_v6_rcv_saddr);
        return !addr.s6_addr32[0] && !addr.s6_addr32[1] && !addr.s6_addr32[2] &&
               addr.s6_addr32[3] == bpf_htonl(1);
    }
    return 0;
}

/* v6.12.111 bpf_lsm.c sleepable_lsm_hooks includes socket_bind.
 * This runs BEFORE the kernel privileged-port permission check. A successful
 * storage creation does NOT prove bind succeeded: accept checks actual tuple. */
SEC("lsm.s/socket_bind")
int BPF_PROG(server_bind, struct socket *socket, struct sockaddr *address,
             int address_len, int ret)
{
    if (ret)
        return ret;
    __u64 port = 0;
    int loopback = 0;
    struct sockaddr_in addr4 = {};
    struct sockaddr_in6 addr6 = {};
    if (address_len == sizeof(addr4) &&
        !bpf_probe_read_kernel(&addr4, sizeof(addr4), address) &&
        addr4.sin_family == 2) {
        port = bpf_ntohs(addr4.sin_port);
        loopback = addr4.sin_addr.s_addr == bpf_htonl(0x7f000001);
    } else if (address_len == sizeof(addr6) &&
               !bpf_probe_read_kernel(&addr6, sizeof(addr6), address) &&
               addr6.sin6_family == 10) {
        port = bpf_ntohs(addr6.sin6_port);
        loopback = !addr6.sin6_addr.s6_addr32[0] && !addr6.sin6_addr.s6_addr32[1] &&
                   !addr6.sin6_addr.s6_addr32[2] &&
                   addr6.sin6_addr.s6_addr32[3] == bpf_htonl(1);
    }
    if (port != 904 && port != 905)
        return 0; /* No change to the earlier 900/901 EACCES contract. */
    struct server_snapshot *lease = bpf_map_lookup_elem(&port_leases, &port);
    struct sock *sk = socket->sk;
    if ((__u32)bpf_get_current_uid_gid() != 989 || !loopback || !sk ||
        sk->sk_type != 1 || sk->sk_protocol != 6 || !lease ||
        lease->role != 2 || lease->port != port || !server_live(lease)) {
        server_count(1);
        return -1;
    }
    struct server_snapshot *old = bpf_sk_storage_get(&server_tags, sk, 0, 0);
    if (old) {
        /* Never overwrite an old generation on a bind retry. */
        int same = old->role == 2 && old->cgroup == lease->cgroup &&
                   old->generation == lease->generation && old->port == port;
        if (!same)
            server_count(1);
        return same ? 0 : -1;
    }
    struct server_snapshot initial = *lease;
    if (!bpf_sk_storage_get(&server_tags, sk, &initial, BPF_SK_STORAGE_GET_F_CREATE)) {
        server_count(1);
        return -1;
    }
    server_count(0);
    return 0;
}

struct proto_accept_arg;
/* PINNED 6.12.111: TWO args + return. BPF_PROG reads accepted from ctx[2].
 * No old four-argument ABI, FD lookup, socket_accept pre-hook, or port-map
 * reread. This hook runs on the actual returned sock before FD publication. */
SEC("fexit/inet_csk_accept")
int BPF_PROG(server_accept, struct sock *listener, struct proto_accept_arg *arg,
             struct sock *accepted)
{
    if (!listener)
        return 0;
    struct server_snapshot *s = bpf_sk_storage_get(&server_tags, listener, 0, 0);
    if (!s)
        return 0; /* Earlier suites and ordinary 1080 remain unchanged. */
    if ((__u32)bpf_get_current_uid_gid() != 989 || s->role != 2 ||
        listener->__sk_common.skc_state != 10 || !server_live(s) ||
        !server_tuple(listener, s) || !accepted ||
        !server_tuple(accepted, s) ||
        accepted->__sk_common.skc_family != listener->__sk_common.skc_family ||
        accepted->__sk_common.skc_state != 1 ||
        bpf_sk_storage_get(&server_tags, accepted, 0, 0)) {
        server_count(3);
        return 0;
    }
    struct server_snapshot initial = *s;
    initial.role = 1;
    if (!bpf_sk_storage_get(&server_tags, accepted, &initial,
                            BPF_SK_STORAGE_GET_F_CREATE)) {
        server_count(3);
        return 0;
    }
    server_count(2);
    return 0;
}

SEC("cgroup/connect4")
int connect4(struct bpf_sock_addr *ctx)
{
    if (ctx->user_port == bpf_htons(900) || ctx->user_port == bpf_htons(901)) {
        count(9);
        return 0;
    }
    if (ctx->user_ip4 == bpf_htonl(0x7f000001) &&
        ctx->user_port == bpf_htons(1080)) {
        int allow = normal_proxy_allowed();
        count(allow ? 0 : 3);
        return allow;
    }
    int allow = current_admission() != 0;
    count(allow ? 0 : 3);
    return allow;
}

SEC("cgroup/connect6")
int connect6(struct bpf_sock_addr *ctx)
{
    if (ctx->user_port == bpf_htons(900) || ctx->user_port == bpf_htons(901)) {
        count(10);
        return 0;
    }
    if (!ctx->user_ip6[0] && !ctx->user_ip6[1] && !ctx->user_ip6[2] &&
        ctx->user_ip6[3] == bpf_htonl(1) && ctx->user_port == bpf_htons(1080)) {
        int allow = normal_proxy_allowed();
        count(allow ? 1 : 4);
        return allow;
    }
    int allow = current_admission() != 0;
    count(allow ? 1 : 4);
    return allow;
}

/* Root guards are explicitly attached PREORDER. A sticky root denial must
 * never be reset here. Local storage belongs to the program's attachment,
 * not the current process: inherited leaf programs select the same slot. */
SEC("cgroup/connect4")
int bridge4(struct bpf_sock_addr *ctx)
{
    struct bridge_slot *slot = bpf_get_local_storage(&bridge_slots, 0);
    if (slot->active && (slot->port == 900 || slot->port == 901) &&
        ctx->user_ip4 == bpf_htonl(0x7f000001) && ctx->user_port == bpf_htons(1080)) {
        ctx->user_port = bpf_htons(slot->port);
        count(11);
    }
    return 1;
}

SEC("cgroup/connect6")
int bridge6(struct bpf_sock_addr *ctx)
{
    struct bridge_slot *slot = bpf_get_local_storage(&bridge_slots, 0);
    if (slot->active && (slot->port == 900 || slot->port == 901) &&
        !ctx->user_ip6[0] && !ctx->user_ip6[1] && !ctx->user_ip6[2] &&
        ctx->user_ip6[3] == bpf_htonl(1) && ctx->user_port == bpf_htons(1080)) {
        ctx->user_port = bpf_htons(slot->port);
        count(12);
    }
    return 1;
}

/* socket_connect is NOT sleepable on Linux 6.12. Direct CO-RE loads preserve
 * the typed sock pointer; no probe_read scalar cast pretends to prove verifier
 * acceptance. The native load/attach gate must establish helper compatibility. */
SEC("lsm/socket_connect")
int BPF_PROG(label_connect, struct socket *socket, struct sockaddr *address,
             int address_len, int ret)
{
    if (ret)
        return ret;
    struct admission *a = current_admission();
    /* The connect4/6 programs witness unadmitted INET connect denials. */
    if (!a) {
        __u64 id = bpf_get_current_cgroup_id();
        /* An inactive/retired scoped admission is not ordinary unbound. */
        if (bpf_map_lookup_elem(&invocation, &id))
            return 0;
        struct sockaddr_in addr4 = {};
        struct sockaddr_in6 addr6 = {};
        int normal = 0;
        if (address_len == sizeof(addr4) &&
            !bpf_probe_read_kernel(&addr4, sizeof(addr4), address))
            normal = addr4.sin_family == 2 && addr4.sin_port == bpf_htons(1080) &&
                     addr4.sin_addr.s_addr == bpf_htonl(0x7f000001);
        else if (address_len == sizeof(addr6) &&
                 !bpf_probe_read_kernel(&addr6, sizeof(addr6), address))
            normal = addr6.sin6_family == 10 && addr6.sin6_port == bpf_htons(1080) &&
                     !addr6.sin6_addr.s6_addr32[0] && !addr6.sin6_addr.s6_addr32[1] &&
                     !addr6.sin6_addr.s6_addr32[2] &&
                     addr6.sin6_addr.s6_addr32[3] == bpf_htonl(1);
        if (!normal)
            return 0;
        struct sock *sk = socket->sk;
        if (!sk)
            return -1;
        struct socket_label *old = bpf_sk_storage_get(&socket_labels, sk, 0, 0);
        if (old)
            return old->cgroup == id && !old->label && !old->generation &&
                   !old->infrastructure ? 0 : -1;
        struct socket_label unbound = { .cgroup = id };
        if (!bpf_sk_storage_get(&socket_labels, sk, &unbound, BPF_SK_STORAGE_GET_F_CREATE))
            return -1;
        count(5);
        return 0;
    }
    struct sock *sk = socket->sk;
    if (!sk)
        return -1;
    struct socket_label *old = bpf_sk_storage_get(&socket_labels, sk, 0, 0);
    /* Never relabel a previously labelled socket on connect retry. */
    if (old)
        return old->cgroup == bpf_get_current_cgroup_id() &&
               old->label == a->label && old->generation == a->generation &&
               !old->infrastructure ? 0 : -1;
    struct socket_label initial = {
        .cgroup = bpf_get_current_cgroup_id(),
        .label = a->label,
        .generation = a->generation,
    };
    if (!bpf_sk_storage_get(&socket_labels, sk, &initial,
                            BPF_SK_STORAGE_GET_F_CREATE))
        return -1;
    count(5);
    return 0;
}

SEC("lsm.s/socket_sendmsg")
int BPF_PROG(use_socket, struct socket *socket, struct msghdr *msg,
             int size, int ret)
{
    if (ret)
        return ret;
    struct sock *sk = socket->sk;
    struct server_snapshot *server = sk ?
        bpf_sk_storage_get(&server_tags, sk, 0, 0) : 0;
    if (server) {
        int allow = (__u32)bpf_get_current_uid_gid() == 989 && server->role == 1 &&
                    server_live(server) && server_tuple(sk, server) &&
                    sk->__sk_common.skc_state == 1;
        server_count(allow ? 4 : 5);
        return allow ? 0 : -1;
    }
    struct socket_label *s = sk ?
        bpf_sk_storage_get(&socket_labels, sk, 0, 0) : 0;
    /* Only exact FDs registered by PID 1 receive this flag. Neither root UID,
     * AF_UNIX nor any class of INET socket gets an implicit exemption. */
    if (s && s->infrastructure) {
        count(8);
        return 0;
    }
    struct admission *a = current_admission();
    __u64 id = bpf_get_current_cgroup_id();
    if (s && !s->label && !s->generation && !s->infrastructure &&
        s->cgroup == id && !bpf_map_lookup_elem(&invocation, &id) &&
        normal_destination(sk)) {
        count(6);
        return 0;
    }
    if (s && a && s->cgroup == bpf_get_current_cgroup_id() &&
        s->label == a->label && s->generation == a->generation) {
        count(6);
        return 0;
    }
    count(7);
    return -1;
}

SEC("lsm.s/file_receive")
int BPF_PROG(receive, struct file *file, int ret)
{
    if (ret)
        return ret;
    /* Unknown/unreadable mode is denied, too. Prior LSM errors survive. */
    unsigned short mode = BPF_CORE_READ(file, f_inode, i_mode);
    if (!mode)
        return -1;
    if ((mode & 0170000) == 0140000) {
        count(2);
        return -1; /* EPERM */
    }
    return 0;
}

/* This scope is activated only after the existing bridge/socket witnesses.
 * Absent scope is not a universal execution policy. A present tombstone denies. */
struct exec_inode {
    __u64 dev, ino;
};
struct exec_permit {
    __u64 cgroup, generation;
    struct exec_inode file;
};
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1);
    __type(key, __u64);
    __type(value, struct admission);
} exec_scope SEC(".maps");
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1);
    __type(key, struct exec_inode);
    __type(value, __u64);
} exec_inodes SEC(".maps");
struct {
    __uint(type, BPF_MAP_TYPE_TASK_STORAGE);
    __uint(map_flags, BPF_F_NO_PREALLOC);
    __type(key, int);
    __type(value, struct exec_permit);
} exec_permits SEC(".maps");
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 3);
    __type(key, __u32);
    __type(value, __u64);
} exec_witness SEC(".maps");

static __always_inline void exec_count(__u32 key)
{
    __u64 *value = bpf_map_lookup_elem(&exec_witness, &key);
    if (value)
        __sync_fetch_and_add(value, 1);
}

SEC("lsm.s/bprm_check_security")
int BPF_PROG(exec_admit, struct linux_binprm *bprm, int ret)
{
    if (ret)
        return ret;
    __u64 cg = bpf_get_current_cgroup_id();
    struct admission *scope = bpf_map_lookup_elem(&exec_scope, &cg);
    if (!scope)
        return 0;
    exec_count(0);
    /* get_current_task_btf returns the kernel-verified task type. No scalar
     * TGID, probe-read pointer cast, or workload-supplied identity is used. */
    struct task_struct *task = bpf_get_current_task_btf();
    struct exec_permit *permit =
        bpf_task_storage_get(&exec_permits, task, 0, 0);
    struct exec_inode file = {
        .dev = BPF_CORE_READ(bprm, file, f_inode, i_sb, s_dev),
        .ino = BPF_CORE_READ(bprm, file, f_inode, i_ino),
    };
    __u64 *native = bpf_map_lookup_elem(&exec_inodes, &file);
    unsigned char magic[4] = {};
    BPF_CORE_READ_INTO(&magic, bprm, buf);
    if (scope->active && scope->generation && permit && native && *native == 1 &&
        permit->cgroup == cg && permit->generation == scope->generation &&
        permit->file.dev == file.dev && permit->file.ino == file.ino &&
        magic[0] == 0x7f && magic[1] == 'E' && magic[2] == 'L' && magic[3] == 'F' &&
        bpf_task_storage_delete(&exec_permits, task) == 0) {
        exec_count(1);
        return 0;
    }
    exec_count(2);
    return -1; /* EPERM, including every unknown executable in this scope. */
}

char LICENSE[] SEC("license") = "GPL";
