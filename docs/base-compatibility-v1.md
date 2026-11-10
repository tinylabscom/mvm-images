# `base-compatibility/v1` — local candidate only

This **separate** descriptor is not an image-set member, schema-3 change,
published asset, release signature, or production trust anchor. It relates one
production workload kernel and its matching composition-source tar from the
**original exact bytes** of one schema-3 `image-set.json`. Historical sets,
locks, the 17 release members and the release workflow are unchanged.

The producer takes a local manifest and an artifact directory:

```sh
python3 scripts/emit-base-compatibility.py \
  --manifest /path/to/image-set.json --artifacts /path/to/artifacts \
  --arch x86_64 --profile rootless_tenant \
  --kernel-version 6.12.4 \
  --libc-loader glibc=/lib64/ld-linux-x86-64.so.2 \
  --kernel-capability virtio_vsock --kernel-capability namespaces \
  --output /path/to/local-candidate.json
```

The JSON has exactly this v1 shape (illustrative pins only):

```json
{
  "schema": "base-compatibility/v1",
  "image_set_manifest_sha256": "64 lowercase hexadecimal characters",
  "arch": "x86_64",
  "profile": "rootless_tenant",
  "kernel": {"name": "rootless-microvm-vmlinux-x86_64", "sha256": "64 lowercase hexadecimal characters", "size": 123},
  "composition_tar": {"name": "rootless-microvm-rootfs-x86_64.tar.gz", "sha256": "64 lowercase hexadecimal characters", "size": 456},
  "base_image_contract": 1,
  "boot_protocol": "linux_direct",
  "filesystem_abi": 1,
  "kernel_version": "6.12.4",
  "libc_loaders": [{"libc": "glibc", "path": "/lib64/ld-linux-x86-64.so.2"}],
  "kernel_capabilities": ["virtio_vsock", "namespaces"]
}
```

`arch` is `x86_64` or `aarch64`; `profile` is `default_tenant` or
`rootless_tenant`. Kernel and tar names/digests/sizes come **only** from unique
matching non-dev manifest members, and local files must match their pins.
The producer inspects the selected tar for the exact `1\n` root-owned,
mode-0444 base-contract marker and UID/GID 901 worker directory using the
release assembler's composition-source rule. It does not verify a release
signature, build the images, boot a guest, inspect kernel config, or prove
the user-supplied semantic claims.

`kernel_version` is strict decimal major.minor.patch (no suffix, leading
zeros, or component > 99999). `libc_loaders` has 1–2 unique `musl`/`glibc`
entries with bounded absolute, normalized paths. `kernel_capabilities` has
at most eight unique names, drawn only from `virtio_vsock`, `virtio_blk`,
`dm_verity`, `pty`, `namespaces`, `cgroup_v2`, `fuse`, `overlayfs`. They are
*local development declarations*, not evidence or host permissions. The
CLI prints an explicit **UNVERIFIED LOCAL DEVELOPMENT CANDIDATE** warning.
There is deliberately no self-declared `trust` field: trust must be determined
by an independently verified signature/provenance, not bytes supplied by
the candidate author.

Before a descriptor is usable as production compatibility evidence, a
separate protected pipeline must independently validate claims against the
built kernel and rootfs (including loader contents, config/capabilities, and
boot behavior on both architectures), verify the bound image-set identity,
and sign the **exact original descriptor bytes** under its own policy.
Neither this script nor these host fixtures perform that gate; they must
not be used to publish or advance a lock.
