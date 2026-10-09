/* Development viability only: globally refuse socket SCM_RIGHTS, not an
 * ownership/lifetime policy. CO-RE relocations resolve against guest BTF. */
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

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u64);
} invocation SEC(".maps");

/* 0/1: admitted v4/v6; 2: refused socket receive; 3/4: refused v4/v6. */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 5);
    __type(key, __u32);
    __type(value, __u64);
} witnessed SEC(".maps");

static __always_inline void count(__u32 key)
{
    __u64 *value = bpf_map_lookup_elem(&witnessed, &key);
    if (value)
        __sync_fetch_and_add(value, 1);
}

static __always_inline int admitted(void)
{
    __u32 key = 0;
    __u64 *id = bpf_map_lookup_elem(&invocation, &key);
    return id && *id && *id == bpf_get_current_cgroup_id();
}

SEC("cgroup/connect4")
int connect4(struct bpf_sock_addr *ctx)
{
    int allow = admitted();
    count(allow ? 0 : 3);
    return allow;
}

SEC("cgroup/connect6")
int connect6(struct bpf_sock_addr *ctx)
{
    int allow = admitted();
    count(allow ? 1 : 4);
    return allow;
}

SEC("lsm/file_receive")
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
