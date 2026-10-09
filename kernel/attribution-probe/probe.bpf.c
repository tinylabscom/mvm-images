/* Development TCP lifecycle experiment only, not a production policy. */
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_tracing.h>

struct inode {
    unsigned short i_mode;
} __attribute__((preserve_access_index));
struct file {
    struct inode *f_inode;
} __attribute__((preserve_access_index));
struct sock {
    int unused;
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

/* 0/1 allowed connect; 2 refused receive; 3/4 denied connect;
 * 5 snapshot creation; 6 allowed send; 7 denied send; 8 infrastructure send. */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 9);
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

SEC("cgroup/connect4")
int connect4(struct bpf_sock_addr *ctx)
{
    int allow = current_admission() != 0;
    count(allow ? 0 : 3);
    return allow;
}

SEC("cgroup/connect6")
int connect6(struct bpf_sock_addr *ctx)
{
    int allow = current_admission() != 0;
    count(allow ? 1 : 4);
    return allow;
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
    if (!a)
        return 0;
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
    struct socket_label *s = sk ?
        bpf_sk_storage_get(&socket_labels, sk, 0, 0) : 0;
    /* Only exact FDs registered by PID 1 receive this flag. Neither root UID,
     * AF_UNIX nor any class of INET socket gets an implicit exemption. */
    if (s && s->infrastructure) {
        count(8);
        return 0;
    }
    struct admission *a = current_admission();
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

char LICENSE[] SEC("license") = "GPL";
