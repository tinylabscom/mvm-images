# mvm-images

Canonical Nix definitions for the Linux layer: builder, default-tenant and
rootless-tenant kernels, sealed workload bases, builder VM image, Stage 0
seeds and the QEMU/WebAssembly smoke pack. Package and toolchain dependencies
are pinned locally. There is no mvm flake/source input or source mirror.

The complete signed guest runtime ships version-locked with `mvmctl`:
overlay, initramfs, SDK sidecar, guest agent/helpers, `mvm-setpriv` and GPU
shims. `mvmctl` assembles it at boot. It is not part of image publication.
Base images contain no mvm binaries or mvm-authored `/init`.
See [mvm#4100](https://github.com/tinylabscom/mvm/issues/4100) and
[mvm-images#49](https://github.com/tinylabscom/mvm-images/issues/49).

## Staged migration — publication blocked

These definitions are staged code, not release or installed-boot evidence.
No compatible CLI v0.23.1 has been published yet. Readiness is ordered:

1. Land the compatible consumer before the CLI tag.
2. Actually publish the CLI and its signed full runtime, and witness
   installed-CLI boots using that published runtime.
3. Only then publish the new signed Linux-only set.
4. Only afterward advance mvm's digest-pinned `images.lock`.

Historical signed sets and the current consumer lock remain untouched.
Consumers retain historical schema 2 (`guest_agent_protocol`) support; new
sets use schema 3 with `base_image_contract: 1`. There is no artificial
set-version cutoff. Local checks do not unlock publication. No manual bypass
or invented release/run IDs may replace evidence.

## Base contract and networking

Both `x86_64` and `aarch64` have separate generic profiles:

- `default-tenant`: the smallest sealed base for one admitted workload.
- `rootless-tenant`: namespaces, delegated cgroup v2/controllers, PTYs,
  container filesystems, `crun`, `fuse-overlayfs` and writable-volume-backed
  storage, preserving verified boot, storage, egress and admission.

Select kernel and rootfs from the same profile. Do not widen the default
kernel for rootless consumers or add application-specific packages or names.
Every base declares `/etc/mvm/base-image-contract` with exact bytes `1\n`,
emitted from `images/base-image-contract.nix`. Fixed identities are root 0,
`mvm-worker` 901, `mvm-dns` 987, `mvm-crng` 988 and `mvm-egress` 989.
Builder additionally provides UID 902 and boot ABI 2; its host tools arrive
in the CLI boot payload.

No guest NIC, TAP, TUN, bridge, veth, macvlan, SLIRP, passt, vpnkit, CNI
dataplane, raw-packet tunnel, guest NAT or firewall fallback is permitted.
Loopback is only for local adapters. Proxy-aware TCP/UDP, controlled DNS,
mediated ping, typed connectors and declared ingress use a single
authenticated FlowMux session over vsock; host endpoints own sockets and
signed policy. Rootless stacks use this same boundary, not CNI compatibility
devices. See the canonical
[networking guide](https://github.com/tinylabscom/mvm/blob/main/public/src/content/docs/guides/networking.md).

## Developer interface

Use Nix on Linux or a configured Linux execution boundary matching the guest
architecture. Host Python/static checks do not prove Linux builds or boots.
There is no paired-source override, pin-advance procedure or host-runtime
build/test harness.

```sh
just list
just build builder-vm
just builder-vm aarch64
just build default-tenant dev aarch64-linux
just build rootless-tenant
just build-all x86_64
just kernel workload-vmlinux
just test
just bdd
just release-check
```

`build-all` builds the complete Linux release surface for one native guest
architecture; CI runs both architectures. Keep default dev variants:
17 producer Linux-layer members, versus a 13-member consumer minimum.
The complete assembler member table drives publication, provenance and
reproduction, not the minimum. The smoke pack is the additional x86_64 member.

Use `just manifest --help` for artifact arguments. Local manifests take
`--images-checkout` only; their `local_checkouts` producer contains only
`images` identity at `local-dev` tier. A moved checkout invalidates that
identity. Local manifests are not signed release evidence.

Built Linux artifacts are checked by the locally owned checker and
`kernel/budgets.json`, not checks loaded from mvm. For a built x86_64 builder
output containing the named files:

```sh
python3 scripts/check_linux_layer.py --arch x86_64 \
  --kernel result/vmlinux --format elf --rootfs result/rootfs.ext4 --builder
```

Artifact checks require actual built files; fast `release-check` retains
Python coverage without pretending unbuilt artifacts have been verified.

```sh
just e2e-plan result
just e2e-rootless-plan result
just e2e-qemu result
just e2e-firecracker result
just e2e-rootless-qemu result
just e2e-rootless-firecracker result
just reproduce x86_64 builder-vm ./reproduction
```

Boot probes invoke VMMs directly with explicit device lists, vsock and no NIC.
They boot a probe-owned init with no runtime overlay or mvm-built bytes and
never start `mvm`, `mvmctl` or `bin/dev`. Real boots require capable Linux
runners. Rootless smoke directly witnesses UID 901, namespace creation,
delegated cgroup v2, generic OCI tools and loopback-only networking.

## Release, reproduction and signatures

After the migration prerequisites are evidenced, `just release <semver>`
remains the established interface from clean, synchronized main. The
protected immutable `image-set/v<semver>` tag starts the workflow which
builds both architectures, reproduces these canonical definitions, signs
and verifies the full atomic set and publishes under environment review.
No mvm merge may require a build or publication here.

The publication gate selects a real CLI release using the repository
variables `IMAGE_CONSUMER_TAG`, `IMAGE_CONSUMER_COMMIT`, and
`IMAGE_CONSUMER_RUN_ID`. It authenticates that release's installed-boot
report and installs the same CLI/runtime pair to verify the signed candidate.
Missing pins, unavailable evidence, or verification failures stop publication.
Cross-repository artifact access uses the workflow token, or the optional
`IMAGE_CONSUMER_READ_TOKEN` secret when a separately scoped token is needed.
That token needs read access to Actions artifacts, contents, and attestations
in `tinylabscom/mvm`; it needs no write permission. Supply it through GitHub
Actions secrets, never a repository file. The token's cross-repository access
must be witnessed in the real release gate, not assumed from local tests.

Preserve immutable action pins, source provenance, cosign workflow identity,
revocation checks and digest-pinned consumer selection. See
[SECURITY.md](SECURITY.md) and [REVOCATIONS.md](REVOCATIONS.md).
SLSA provenance subjects cover member artifacts, pack manifests, SBOMs and
the image-set root, derived from the assembler member table, never a second
workflow list. Attestation does not replace byte-for-byte reproduction.

Verify a downloaded provenance subject against its actual release tag:

```sh
: "${tag:?Set tag to the actual release_tag from images.lock}"
gh release download "$tag" -R tinylabscom/mvm-images -p image-set.json
gh attestation verify image-set.json \
  -R tinylabscom/mvm-images \
  --signer-workflow tinylabscom/mvm-images/.github/workflows/release.yml \
  --source-ref "refs/tags/$tag" \
  --deny-self-hosted-runners
```

The same verification applies to other subjects. The certificate identity is
`https://github.com/tinylabscom/mvm-images/.github/workflows/release.yml@refs/tags/$tag`;
`--source-digest` can additionally pin `producer.source_commit`.
This is SLSA Build Level 2, not proof of an uncompromised build: a compromised
release job can attest altered bytes. Reproduction independently rebuilds
the canonical definitions here and checks equality. Never compare against
retired mvm recipes. Historical sets without attestations stay unchanged.

Instruction files have detached Sigstore bundles. Editing `AGENTS.md` or
`CLAUDE.md` makes existing bundles stale. An authorized signer must really
re-sign them; never fabricate bundles or claim stale signatures authenticate
changed text.
