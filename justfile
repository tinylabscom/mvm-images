# mvm-images — the system-image train for mvm.
#
# Every recipe is a thin wrapper over the documented commands in README.md;
# keep the two in sync. Images are Linux artifacts, so the `build*` recipes
# need Nix on Linux or a Linux remote builder. On macOS, use `image-set`,
# which builds through a paired mvm checkout's builder VM instead of host
# Nix. Nothing here publishes anything until the migration's publication
# workstream lands; `just release-check` is the pre-publish gate sequence.

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
    @echo '  runtime-overlay  default,'
    @echo '                   sdk-sidecar-image, sdk-sidecar-image-musl'
    @echo '  initramfs        default'
    @echo ''
    @echo '  kernels:  nix build ./kernel#workload-vmlinux | ./kernel#<attr>'
    @echo '            (kernel/ is a flake of its own; see kernel/flake.nix)'
    @echo '  qemu-wasm outputs: under .#packages.<system> in qemu-wasm/'

# Build one image role with Nix: `just build <role> [attr] [system] [nix args...]`.
build role attr="default" system=system *nix_args:
    nix build ".#legacyPackages.{{system}}.{{role}}.{{attr}}" {{nix_args}}

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
    if [ -n "{{mvm_checkout}}" ]; then
        mvm="$(cd '{{mvm_checkout}}' && pwd -P)"
        scripts/install-host-toolchain.sh --mvm-checkout "$mvm"
        bins="$(scripts/build-host-binaries.sh --mvm-checkout "$mvm" '{{target}}' | tee /dev/stderr | sed -n 's/^MVM_HOST_BIN_DIR=//p' | tail -1)"
        MVM_HOST_BIN_DIR="$bins" nix build ".#legacyPackages.{{system}}.builder-vm.default" --impure --override-input mvm "path:$mvm"
    else
        scripts/install-host-toolchain.sh
        bins="$(scripts/build-host-binaries.sh '{{target}}' | tee /dev/stderr | sed -n 's/^MVM_HOST_BIN_DIR=//p' | tail -1)"
        MVM_HOST_BIN_DIR="$bins" nix build ".#legacyPackages.{{system}}.builder-vm.default" --impure
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

# The pre-publish gate sequence, in the order a release runs them. Publishing
# itself is not wired in this repository yet (README "Release contract"); CI
# (build.yml, reproduce.yml) runs the builders on both architectures.
release-check:
    scripts/check-action-pins.sh
    scripts/check-source-drift.sh
    scripts/check-local-mvm-override.sh '{{system}}'

# Compare this repository's build with mvm's in-tree build of the same role at
# the pinned commit — the reproduce.yml lane by hand. Needs a checkout of the
# pinned commit (build-host-binaries.sh leaves one behind) and, for
# builder-vm, MVM_HOST_BIN_DIR from the same tree.
compare-same-commit target role mvm_checkout out_dir:
    scripts/compare-same-commit.sh '{{target}}' '{{role}}' '{{mvm_checkout}}' '{{out_dir}}'

# Describe a built set in the manifest schema mvm parses, into a new directory
# (local_checkouts producer, local-dev tier; mvm refuses it once either
# checkout moves). Pass-through over scripts/emit-local-manifest.py; see the
# README example for the full artifact set of one role.
manifest *args:
    scripts/emit-local-manifest.py "$@"
