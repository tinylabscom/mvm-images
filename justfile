# mvm-images — the system-image train for mvm.
#
# Every recipe is a thin wrapper over the documented commands in README.md;
# keep the two in sync. Images are Linux artifacts, so the `build*` recipes
# need Nix on Linux or a Linux remote builder. On macOS, use `image-set`,
# which builds through a paired mvm checkout's builder VM instead of host
# Nix. `just release-check` is the local gate sequence; `just release <semver>`
# pushes the protected tag whose workflow builds, signs, verifies and publishes
# one complete image set.

set shell := ["bash", "-cu"]
set positional-arguments

# Guest architecture of this host (images build for the host architecture).
arch := `uname -m | sed -e 's/^arm64$/aarch64/' -e 's/^amd64$/x86_64/'`
system := arch + "-linux"

[private]
default:
    @just --list --unsorted

# What one release publishes, and the roles this flake builds. Informational.
list:
    @printf 'system detected: %s (override with e.g. `just build runtime-overlay default aarch64-linux`)\n' '{{system}}'
    @echo 'roles × attrs (.#legacyPackages.<system>.<role>.<attr>):'
    @echo ''
    @echo '  builder-vm       default, dev, stage0-rootfs,'
    @echo '                   builder-kernel, workload-kernel,'
    @echo '                   kernel-configfile, workload-kernel-configfile,'
    @echo '                   sdk-sidecar-image{,-musl}   (needs MVM_HOST_BIN_DIR + --impure)'
    @echo '  default-tenant   default, dev                 (default needs --impure)'
    @echo '  rootless-tenant  default, prod, dev, smoke    (generic; no NIC)'
    @echo '  runtime-overlay  default,'
    @echo '                   sdk-sidecar-image, sdk-sidecar-image-musl'
    @echo '  initramfs        default'
    @echo ''
    @echo '  kernels:  nix build ./kernel#workload-vmlinux | ./kernel#<attr>'
    @echo '            (kernel/ is a flake of its own; see kernel/flake.nix)'
    @echo '  qemu-wasm outputs: under .#packages.<system> in qemu-wasm/'

# Build one image role with Nix, or dispatch `all` to the complete build.
# `just build all` and `just build role=all` are aliases for `just build-all`.
build role attr="default" system=system *nix_args:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ "{{role}}" == all || "{{role}}" == role=all ]]; then
        if [[ "{{attr}}" != default ]]; then
            echo "build all: attr is not accepted; use 'just build-all [arch]'" >&2
            exit 2
        fi
        target="{{system}}"
        target="${target%-linux}"
        exec just build-all "$target" {{nix_args}}
    fi
    exec nix build ".#legacyPackages.{{system}}.{{role}}.{{attr}}" {{nix_args}}

# Build the workload or builder kernel (kernel/ is its own flake).
kernel attr="workload-vmlinux":
    nix build "./kernel#{{attr}}"

# Cross-compile the builder VM's three static host binaries and print
# MVM_HOST_BIN_DIR=<dir>. Without a checkout argument the source is the
# commit flake.lock pins; with one, that checkout's tree, edits included.
host-binaries mvm_checkout="" target=arch:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -n "{{mvm_checkout}}" ]; then
        exec scripts/build-host-binaries.sh --mvm-checkout "$(cd '{{mvm_checkout}}' && pwd -P)" "{{target}}"
    fi
    exec scripts/build-host-binaries.sh "{{target}}"

# Build the builder VM: host binaries from the same source as the guest,
# then the image with MVM_HOST_BIN_DIR. Defaults to the pinned commit;
# pass an mvm checkout to build from it instead.
builder-vm mvm_checkout="" target=arch:
    #!/usr/bin/env bash
    set -euo pipefail
    toolchain_prefix="$HOME/.local/mvm-images"
    install_args=()
    if [ -n "{{mvm_checkout}}" ]; then
        mvm="$(cd '{{mvm_checkout}}' && pwd -P)"
        install_args=(--mvm-checkout "$mvm")
    fi
    scripts/install-host-toolchain.sh "${install_args[@]}" "$toolchain_prefix"
    # The installer runs as a child process, so its local path changes cannot
    # affect this recipe. Prefer the exact Zig it just installed over any
    # system/Homebrew Zig that appears earlier on the caller's PATH.
    export PATH="$toolchain_prefix/bin:$PATH"
    if [ -n "{{mvm_checkout}}" ]; then
        bins="$(scripts/build-host-binaries.sh --mvm-checkout "$mvm" '{{target}}' | tee /dev/stderr | sed -n 's/^MVM_HOST_BIN_DIR=//p' | tail -1)"
        MVM_HOST_BIN_DIR="$bins" nix build ".#legacyPackages.{{system}}.builder-vm.default" --impure --override-input mvm "path:$mvm"
    else
        bins="$(scripts/build-host-binaries.sh '{{target}}' | tee /dev/stderr | sed -n 's/^MVM_HOST_BIN_DIR=//p' | tail -1)"
        MVM_HOST_BIN_DIR="$bins" nix build ".#legacyPackages.{{system}}.builder-vm.default" --impure
    fi

# Build every release-bearing output for this machine's guest architecture.
# CI runs this build surface on both x86_64 and aarch64 before a release.
build-all target=arch:
    #!/usr/bin/env bash
    set -euo pipefail
    host=$(uname -m | sed -e 's/^arm64$/aarch64/' -e 's/^amd64$/x86_64/')
    if [[ "{{target}}" != "$host" ]]; then
        echo "build-all: target {{target}} does not match this host architecture $host" >&2
        echo "run this recipe once on each architecture; CI does that for releases" >&2
        exit 2
    fi
    system="{{target}}-linux"
    just builder-vm '' '{{target}}'
    nix build \
      ".#legacyPackages.${system}.builder-vm.stage0-rootfs" \
      ".#legacyPackages.${system}.runtime-overlay.default" \
      ".#legacyPackages.${system}.runtime-overlay.sdk-sidecar-image" \
      ".#legacyPackages.${system}.runtime-overlay.sdk-sidecar-image-musl" \
      ".#legacyPackages.${system}.initramfs.default"
    nix build \
      ".#legacyPackages.${system}.default-tenant.default" \
      ".#legacyPackages.${system}.rootless-tenant.default" \
      --impure
    nix build \
      "./kernel#packages.${system}.builder-vmlinux" \
      "./kernel#packages.${system}.workload-vmlinux" \
      "./kernel#packages.${system}.rootless-vmlinux"
    if [[ "{{target}}" == x86_64 ]]; then
        nix build ".#legacyPackages.${system}.qemu-wasm.qemu-wasm-smoke-pack"
    fi

# Build one role against a paired local mvm checkout, uncommitted edits
# included (README "Working on images"). `just with-mvm ../mvm runtime-overlay`
with-mvm mvm role attr="default" system=system:
    nix build ".#legacyPackages.{{system}}.{{role}}.{{attr}}" --override-input mvm "path:$(cd '{{mvm}}' && pwd -P)"

# Build one role through a paired mvm checkout's builder VM, on a host with
# no Nix (the W5e path; publishes into mvm's local image cache at the
# local-dev tier). `just image-set ../mvm default-tenant`
image-set mvm="../mvm" role="default-tenant" attr="default":
    #!/usr/bin/env bash
    set -euo pipefail
    mvm="$(cd '{{mvm}}' && pwd -P)"
    if [ ! -x "$mvm/bin/dev" ]; then
        echo "image-set: $mvm has no bin/dev (mvm at or past the W5e build verb is required)" >&2
        exit 2
    fi
    MVM_IMAGES_DIR="$(pwd -P)" exec "$mvm/bin/dev" build image-set "{{role}}" --attr "{{attr}}"

# The local pre-publish gate sequence. The tag workflow repeats the release
# critical checks after building both architectures and before publication.
release-check:
    scripts/check-action-pins.sh
    scripts/check-source-drift.sh
    scripts/check-local-mvm-override.sh '{{system}}'
    scripts/check_no_network_devices.py
    scripts/check_rootless_tenant.py
    python3 -m unittest discover -s scripts/tests -v
    scripts/run-bdd.py

# Cut one immutable image-set release from a clean, fully-synced main checkout.
# The pushed protected tag starts .github/workflows/release.yml; only that
# workflow can create the GitHub Release, after protected-environment review.
release version:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! "{{version}}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
        echo "release: version must be semver without a leading v (for example 0.1.0)" >&2
        exit 2
    fi
    branch=$(git branch --show-current)
    if [[ "$branch" != main ]]; then
        echo "release: refusing from branch $branch; switch to main first" >&2
        exit 2
    fi
    if [[ -n "$(git status --porcelain=v1 --untracked-files=all)" ]]; then
        echo "release: refusing a dirty main checkout" >&2
        git status --short
        exit 2
    fi
    git fetch origin main --tags
    if [[ "$(git rev-parse HEAD)" != "$(git rev-parse refs/remotes/origin/main)" ]]; then
        echo "release: local main is not exactly origin/main; pull and try again" >&2
        exit 2
    fi
    tag="image-set/v{{version}}"
    if git rev-parse --quiet --verify "refs/tags/$tag" >/dev/null; then
        echo "release: tag already exists: $tag" >&2
        exit 2
    fi
    just release-check
    git tag -a "$tag" -m "mvm image set v{{version}}"
    git push origin "refs/tags/$tag"
    echo "release: $tag pushed; follow .github/workflows/release.yml on GitHub"

# Fast repository-local verification. Neither recipe runs mvm or mvmctl.
test:
    python3 -m unittest discover -s scripts/tests -v
    scripts/check_no_network_devices.py
    scripts/check_rootless_tenant.py

bdd:
    scripts/run-bdd.py

# Print both direct-VMM plans without building or booting an image.
e2e-plan artifacts="result":
    scripts/e2e_boot.py qemu '{{artifacts}}' --plan
    scripts/e2e_boot.py firecracker '{{artifacts}}' --plan

# Boot a qemu-wasm-smoke-image output directly. The directory must contain
# kernel.img, vmlinux and rootfs.bin. These require a capable native VMM host
# but never an mvm checkout or process.
e2e-qemu artifacts binary="qemu-system-x86_64":
    scripts/e2e_boot.py qemu '{{artifacts}}' --binary '{{binary}}'

e2e-firecracker artifacts binary="firecracker":
    scripts/e2e_boot.py firecracker '{{artifacts}}' --binary '{{binary}}'

# Boot the actual rootless tenant smoke variant. It checks uid 1000, user/mount/
# PID namespaces, delegated cgroup v2, crun + fuse-overlayfs, and loopback-only
# networking before emitting MVM-ROOTLESS-READY.
e2e-rootless-plan artifacts="result" runtime_overlay="result/runtime-overlay.ext4":
    scripts/e2e_boot.py qemu '{{artifacts}}' --rootfs-name rootfs.ext4 --rootfs-type ext4 --runtime-overlay '{{runtime_overlay}}' --ready-marker MVM-ROOTLESS-READY --plan
    scripts/e2e_boot.py firecracker '{{artifacts}}' --rootfs-name rootfs.ext4 --rootfs-type ext4 --runtime-overlay '{{runtime_overlay}}' --ready-marker MVM-ROOTLESS-READY --plan

e2e-rootless-qemu artifacts="result" runtime_overlay="result/runtime-overlay.ext4" binary="qemu-system-x86_64" accel="kvm":
    scripts/e2e_boot.py qemu '{{artifacts}}' --binary '{{binary}}' --accel '{{accel}}' --rootfs-name rootfs.ext4 --rootfs-type ext4 --runtime-overlay '{{runtime_overlay}}' --ready-marker MVM-ROOTLESS-READY

e2e-rootless-firecracker artifacts="result" runtime_overlay="result/runtime-overlay.ext4" binary="firecracker":
    scripts/e2e_boot.py firecracker '{{artifacts}}' --binary '{{binary}}' --rootfs-name rootfs.ext4 --rootfs-type ext4 --runtime-overlay '{{runtime_overlay}}' --ready-marker MVM-ROOTLESS-READY

# Boot the browser pack directly under headless Chromium.
e2e-qemu-wasm pack chrome:
    scripts/run-qemu-wasm-smoke-chromium.py '{{pack}}' '{{chrome}}'

# Rebuild one canonical role byte-for-byte — the reproduce.yml lane by hand.
# builder-vm additionally requires MVM_HOST_BIN_DIR, just like its normal build.
reproduce target role out_dir:
    scripts/check-reproducible.sh '{{target}}' '{{role}}' '{{out_dir}}'

# Describe a built set in the manifest schema mvm parses, into a new directory
# (local_checkouts producer, local-dev tier; mvm refuses it once either
# checkout moves). Pass-through over scripts/emit-local-manifest.py; see the
# README example for the full artifact set of one role.
manifest *args:
    scripts/emit-local-manifest.py "$@"
