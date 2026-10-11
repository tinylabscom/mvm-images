{
  description = "mvm builder VM image — kernel + rootfs.ext4 with Nix + build tools; mvm binaries arrive in the boot payload";

  # ── Why this flake exists ────────────────────────────────────────────
  #
  # This flake is the artifact the builder VM boots into: a small Linux
  # kernel + ext4 rootfs containing Nix + a curated build-tools subset.
  # It carries no mvm binary: `mvm-host-vm-init`, `mvm-builderd` and
  # `mvm-setpriv` arrive at boot in mvmctl's builder boot payload.
  #
  # The root flake exposes these packages as `legacyPackages.<system>.builder-vm`;
  # its `default` produces `$out/{vmlinux,rootfs.ext4,cmdline.txt,manifest.json}`.
  #
  # The local Linux-layer composer owns the root filesystem. No mvm source
  # or workspace filter participates in evaluation.
  #
  # ── Builder VM package set ────────────────────────────────────────
  #
  # Narrower than the interactive image:
  #
  # - Static busybox (provides `/bin/sh`, `sync`, basic
  #   POSIX utilities — small footprint).
  # - Nix (the whole point of the VM).
  # - Bash + coreutils + gnugrep / gnused / gawk / findutils / which
  #   (user's `cmd.sh` is shell, not necessarily POSIX-only).
  # - Git + gnumake + curl + jq (Nix flakes pull from git, builds
  #   often run make / curl, `cmd.sh` may format JSON).
  # - e2fsprogs (`mkfs.ext4` for the first-boot format of the
  #   persistent `/nix` store) + util-linux (`mount`, `umount`,
  #   `losetup`).
  # - iproute2 (loopback-only setup; never a guest NIC).
  # - **No** `procps`-interactive / `less` — kept slim.
  # - No mvm binary. At builder boot ABI 2 the boot payload supplies
  #   `mvm-host-vm-init`, `mvm-builderd` and `mvm-setpriv`, and the
  #   guest runs them from `/run/mvm/host-bins`.

  # Inputs come from this repository's root flake. This is not a flake on its own.

  outputs =
    { self, nixpkgs, microvm, ... }:
    let
      systems = [ "aarch64-linux" "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      # The builder boot ABI this image is built to (./boot-abi.nix, a bare
      # integer, so the manifest emitters read the same value as text). ABI 2:
      # every mvm binary the builder runs (mvm-host-vm-init, mvm-builderd,
      # mvm-setpriv) arrives at boot in mvmctl's own initramfs payload, so the
      # image bakes none of them and the derivation evaluates pure — no
      # rustPlatform.buildRustPackage calls are permitted in this flake, and
      # the image's closure contains no mvm package.
      bootAbi = import ./boot-abi.nix;
      cacheContractVersion = import ./cache-contract.nix;

      # Shared, locally owned Linux kernel configuration.
      kernelBaseFor = pkgs:
        import ../../kernel/base.nix { inherit pkgs; };

      # veritysetup sidecar bytes must not drift when nixpkgs revs. The
      # OCI-pull path runs `veritysetup format`
      # inside this builder VM, while the Nix-built baseline runs it in
      # the tenant image definitions here. These definitions intentionally
      # pin the same cryptsetup release + tarball hash, and both must be
      # reviewed together on bump.
      pinnedCryptsetupVersion = "2.8.6";
      pinnedCryptsetupSrcHash = "sha256-gAQmX9mTiF0I97Yz2+BWhR3hohAwdhOk693HQ/zO/lo=";
      pinnedCryptsetupFor = pkgs:
        pkgs.cryptsetup.overrideAttrs (_old: {
          version = pinnedCryptsetupVersion;
          src = pkgs.fetchurl {
            url =
              "mirror://kernel/linux/utils/cryptsetup/v${pkgs.lib.versions.majorMinor pinnedCryptsetupVersion}/"
              + "cryptsetup-${pinnedCryptsetupVersion}.tar.xz";
            hash = pinnedCryptsetupSrcHash;
          };
        });

      # Narrower than the interactive image. See module-level docs
      # above for the rationale on each.
      #
      # The application-dependency install pipeline: `uv` / `pnpm`
      # drive the installer, `cyclonedx-py` / `pnpm sbom` emit SBOMs,
      # and `pip-audit` / `pnpm audit` run the CVE scan. The SBOM /
      # CVE side is a soft gate — `mvm-host-vm-init::install` emits a
      # CycloneDX-1.5 empty stub when the tool isn't on PATH and logs
      # a warning.
      #
      # The VM has no NIC. Every job, flake build and dependency install
      # alike, reaches the network only through the vsock egress client,
      # which relays each connection to the host; the host opens the real
      # connection under the builder's egress policy.
      builderPackages = system: pkgs: with pkgs; [
        bashInteractive
        coreutils
        # Static POSIX utilities; the builder has no NIC or DHCP path.
        pkgsStatic.busybox
        gnugrep
        gnused
        gawk
        findutils
        which
        nix
        # gitMinimal drops perl/sendmail/gui/manpages (~20 MB). git is
        # only invoked here by nix's `github:` substituter/fetcher; the
        # core porcelain that needs is intact in the minimal build.
        # `mvm-host-vm-init` does not shell to git (grep -rn '"git"' in
        # crates/mvm-host-vm-init/).
        gitMinimal
        gnumake
        curl
        jq
        iproute2
        e2fsprogs
        util-linux
        # The host VM spawns one Firecracker workload microVM per
        # `WorkloadStart` dispatch inside itself. Sourced from the pinned
        # nixpkgs above — an upstream Nix package, never an
        # mvm-published prebuilt (source-checkout rule).
        # The local composer's symlink loop lands it at /sbin/firecracker
        # and /usr/local/bin/firecracker; the `extraFiles` entry
        # below pins the exact /usr/bin/firecracker path the guest
        # hardcodes (`FirecrackerVmm` in mvm-host-vm-init).
        firecracker
        (pinnedCryptsetupFor pkgs) # provides pinned veritysetup
        # app-deps install pipeline.
        uv
        pnpm
        # NOTE: `python3Packages.cyclonedx-bom` and
        # `python3Packages.pip-audit` are referenced here but not
        # present in nixpkgs-25.11 under those exact attribute names;
        # the Stage 0 nix eval bails with "attribute 'cyclonedx-bom'
        # missing". Commented out until the right attribute name (or
        # a newer nixpkgs pin that has them) lands. The deps-volume
        # audit pipeline still works at runtime (a missing tool yields
        # an empty stub); the SBOM/CVE tools were a nice-to-have inside
        # the builder VM, not something a build depends on.
        # python3Packages.cyclonedx-bom
        # python3Packages.pip-audit
      ];

      # Canonical kernel cmdline for the builder VM. `LibkrunBuilderVm`
      # reads this from the cmdline.txt output and passes it to
      # `mvm_libkrun::KrunContext.kernel_cmdline`.
      #
      # - `console=hvc0` — libkrun's virtio-console (no serial).
      # - `root=/dev/vda` — rootfs.ext4 attached as virtio-blk.
      # - `ro` — root is read-only; writes go to the persistent
      #   /nix-store virtio-blk at /dev/vdb.
      # - `rootfstype=ext4` — skip filesystem auto-detection.
      # - `rootwait` — wait for virtio-blk root enumeration before mounting.
      # - `panic=-1 loglevel=8` — reboot on panic and keep early boot verbose
      #   enough that the host-side console capture has useful crash context.
      # PID 1 is supplied by the CLI's authenticated initramfs. This base is
      # deliberately not independently bootable through a fallback /init.
      builderCmdline = "console=hvc0 root=/dev/vda ro rootfstype=ext4 rootwait panic=-1 loglevel=8";

      # Extra packages for the interactive (dev) builder VM image.
      # Added on top of `builderPackages` when `interactive = true`.
      # Provides a shell environment inside the builder VM. No `mvmctl`
      # verb boots this image; the builder VM `mvmctl` runs is the
      # headless `default` output.
      #
      # The Rust toolchain (`cargo` + `rustc`) is deliberately NOT baked in:
      # it is ~1 GB of closure and `mvm` itself builds on the host, while Rust
      # guest workloads build through nix (`buildRustPackage`) — so interactive
      # `cargo`/`rustc` here only served ad-hoc poking. The builder VM has `nix`
      # and (in this image) open egress, so a contributor who wants it runs
      # `nix shell nixpkgs#rustc nixpkgs#cargo` on demand; it then persists in
      # the `/nix-store` image. Halving the dev rootfs is worth the one-time pull.
      devPackages = pkgs: with pkgs; [
        nano
      ];

      # Rootfs builder. The custom kernel under `./kernel` is built
      # `CONFIG_MODULES=n` (everything in-tree is `=y`), so the
      # rootfs ships no `/lib/modules/<kver>/` tree — same rootfs whether producing
      # the full builder-VM image or the Stage 0 seed.
      #
      # mvm binaries (mvm-host-vm-init, mvm-builderd, mvm-setpriv)
      # are not baked into the rootfs at all: at ABI 2 the boot
      # contract supplies them from mvmctl's initramfs payload at
      # boot, and the image writes only the ABI marker below.
      mkBuilderVmRootfs =
        { system, interactive ? false }:
        let
          pkgs = import nixpkgs { inherit system; };
          extraPkgs = if interactive then devPackages pkgs else [ ];
        in
        (import ../mk-rootfs.nix { inherit pkgs; }) {
          name = "mvm-builder-vm";
          # Persistent build jobs run as this unprivileged numeric uid. Keep
          # a passwd/group entry so Nix can resolve its home directory.
          builderUid = 902;
          packages = (builderPackages system pkgs) ++ extraPkgs;
          # /etc/mvm/builder-boot-abi tells the boot contract this
          # image meets ABI 2: PID 1 (mvm-host-vm-init), mvm-builderd and
          # mvm-setpriv arrive in mvmctl's initramfs payload, baked nowhere
          # in this rootfs.
          # /usr/bin/firecracker is pinned for the guest's
          # FirecrackerVmm spawn. firecracker is
          # also in `packages` above (for the full closure +
          # the /sbin + /usr/local/bin symlinks the composer adds);
          # this entry guarantees the canonical /usr/bin path
          # regardless of the other symlink targets.
          extraFiles = {
            "/etc/mvm/builder-boot-abi" = {
              content = "${toString bootAbi}\n";
              mode = "0444";
            };
            "/usr/bin/firecracker" =
              "${pkgs.firecracker}/bin/firecracker";
          };
        };

      # Two attrs.
      #   default — headless builder VM; the one `mvmctl` boots for builds.
      #   dev     — interactive builder VM (`builderPackages` + `devPackages`).
      #             No `mvmctl` verb boots it.
      #
      # Both evaluate pure: the boot contract supplies the host binaries
      # from mvmctl's payload at boot, so the image bakes none and reads no
      # environment. No rustPlatform.buildRustPackage calls remain in this
      # flake.
      mkBuilderVmImage =
        { system, interactive ? false }:
        let
          pkgs = import nixpkgs { inherit system; };
          # Slim custom kernel — see `nix/images/kernel/builder.nix` (shared
          # base `nix/images/kernel/base.nix` + builder-only delta).
          # `linuxManualConfig` over `make defconfig` carved down by the
          # base disables. `CONFIG_MODULES=n` so the kernel has only what
          # `mvm-host-vm-init` uses built-in — no driver modules tree.
          kernelPkg = import ../../kernel/builder.nix { inherit pkgs; base = kernelBaseFor pkgs; };
          kernelConfig = kernelPkg.passthru.configfile;
          rootfs = mkBuilderVmRootfs { inherit system interactive; };
          kernelFile =
            if pkgs.stdenv.hostPlatform.isAarch64 then "Image" else "vmlinux";
          imageName = if interactive
                      then "mvm-builder-vm-dev-${system}"
                      else "mvm-builder-vm-image-${system}";
          manifestName = if interactive
                         then "mvm-builder-vm-dev"
                         else "mvm-builder-vm";
        in
        pkgs.runCommand imageName
          {
            nativeBuildInputs = [ pkgs.python3 ];
            passthru = {
              inherit rootfs;
              kernel = kernelPkg;
              cmdline = builderCmdline;
            };
          }
          ''
            mkdir -p $out

            # Kernel.
            if [ -f ${kernelPkg}/${kernelFile} ]; then
              cp ${kernelPkg}/${kernelFile} $out/vmlinux
            else
              echo "kernel package ${kernelPkg} did not produce ${kernelFile}" >&2
              ls -la ${kernelPkg} >&2
              exit 1
            fi

            # Rootfs.
            cp ${rootfs} $out/rootfs.ext4

            chmod 0644 $out/vmlinux $out/rootfs.ext4

            cp ${kernelConfig} $out/kernel.config
            python3 ${../../scripts/check_no_network_devices.py} config \
              --kernel-config $out/kernel.config
            chmod 0644 $out/kernel.config

            # Canonical kernel cmdline — `LibkrunBuilderVm` reads this
            # and threads it into `mvm_libkrun::KrunContext.kernel_cmdline`.
            # Living next to the kernel makes the binding atomic with
            # the image.
            echo "${builderCmdline}" > $out/cmdline.txt

            # SHA-256 + size manifest, sister to the dev-image's
            # release-artifact pattern. `download_builder_vm_image`
            # verifies these against the release manifest before
            # extracting.
            kernel_sha=$(sha256sum $out/vmlinux | cut -d' ' -f1)
            config_sha=$(sha256sum $out/kernel.config | cut -d' ' -f1)
            rootfs_sha=$(sha256sum $out/rootfs.ext4 | cut -d' ' -f1)
            kernel_size=$(stat -c%s $out/vmlinux)
            config_size=$(stat -c%s $out/kernel.config)
            rootfs_size=$(stat -c%s $out/rootfs.ext4)
            cat > $out/manifest.json <<MANIFEST
            {
              "name": "${manifestName}",
              "system": "${system}",
              "vmlinux":      { "sha256": "$kernel_sha", "size": $kernel_size },
              "kernel_config": { "sha256": "$config_sha", "size": $config_size },
              "rootfs_ext4":  { "sha256": "$rootfs_sha", "size": $rootfs_size },
              "cmdline": "${builderCmdline}",
              "cache_contract_version": ${toString cacheContractVersion},
              "runtime_overlay_ready": true,
              "vsock_egress_ready": true,
              "no_network_devices_ready": true
            }
            MANIFEST
          '';

      mkBuilderVmStage0Rootfs = system:
        let
          pkgs = import nixpkgs { inherit system; };
          # Stage 0 boots under a different kernel than what nixpkgs
          # ships, so omit the kernel + module tree to avoid
          # misleading modprobe with a foreign kver. Always headless.
          rootfs = mkBuilderVmRootfs { inherit system; interactive = false; };
        in
        pkgs.runCommand "mvm-builder-vm-stage0-rootfs-${system}" { } ''
          mkdir -p $out

          cp ${rootfs} $out/rootfs.ext4

          chmod 0644 $out/rootfs.ext4
          echo "${builderCmdline}" > $out/cmdline.txt

          rootfs_sha=$(sha256sum $out/rootfs.ext4 | cut -d' ' -f1)
          rootfs_size=$(stat -c%s $out/rootfs.ext4)
          # This is not a bootable builder cache: it has no kernel or checked
          # kernel config, so it must not assert no_network_devices_ready.
          cat > $out/manifest.json <<MANIFEST
          {
            "name": "mvm-builder-vm-stage0-rootfs",
            "system": "${system}",
            "rootfs_ext4": { "sha256": "$rootfs_sha", "size": $rootfs_size },
            "cmdline": "${builderCmdline}",
            "cache_contract_version": ${toString cacheContractVersion},
            "runtime_overlay_ready": true,
            "vsock_egress_ready": true,
            "stage0_rootfs_only": true
          }
          MANIFEST
        '';
      # Expose the generated kernel `.config` as a standalone flake
      # output so contributors can audit what
      # `make defconfig + enables/disables + olddefconfig` actually
      # produced without temporarily editing this flake. Build with:
      #
      #   nix build .#kernel-configfile -o /tmp/kconfig
      #   grep '=y$' /tmp/kconfig | sort > /tmp/kconfig.y.txt
      #
      # The file is a regular `.config` text file — diffable across
      # `disables` edits to confirm SoC platform clusters are gone.
      mkKernelConfigfile = system:
        let pkgs = import nixpkgs { inherit system; };
        in (import ../../kernel/builder.nix { inherit pkgs; base = kernelBaseFor pkgs; }).passthru.configfile;

      # Standalone kernel artifacts — targets for `mvmctl kernel build`
      # and the kernel-build GHA. The builder image's `default` output
      # already embeds `builder-kernel`; exposing each kernel on its own
      # lets the expensive compile run (and be cached / published)
      # without realizing a full rootfs.
      #
      # `workload-kernel` is the shared base + the dm-verity delta
      # (`nix/images/kernel/workload.nix`). No runtime consumes it yet —
      # workload microVMs currently boot a host-provided kernel — so it's
      # published as an artifact ahead of the runtime wiring, which is
      # when its home moves out of this builder-vm flake.
      mkBuilderKernel = system:
        let pkgs = import nixpkgs { inherit system; };
        in import ../../kernel/builder.nix { inherit pkgs; base = kernelBaseFor pkgs; };
      mkWorkloadKernel = system:
        let pkgs = import nixpkgs { inherit system; };
        in import ../../kernel/workload.nix { inherit pkgs; base = kernelBaseFor pkgs; };
      mkWorkloadKernelSizeopt = system:
        let pkgs = import nixpkgs { inherit system; };
        in import ../../kernel/workload.nix {
          inherit pkgs;
          base = kernelBaseFor pkgs;
          optimizeForSize = true;
        };
      mkWorkloadKernelConfigfile = system:
        let pkgs = import nixpkgs { inherit system; };
        in (import ../../kernel/workload.nix { inherit pkgs; base = kernelBaseFor pkgs; }).passthru.configfile;
      mkWorkloadKernelSizeoptConfigfile = system:
        let pkgs = import nixpkgs { inherit system; };
        in (import ../../kernel/workload.nix {
          inherit pkgs;
          base = kernelBaseFor pkgs;
          optimizeForSize = true;
        }).passthru.configfile;
    in
    {
      packages = forAllSystems (system: {
        # Headless builder VM — the image `mvmctl` boots for builds.
        # Contains only the build tooling; no interactive shell extras.
        default = mkBuilderVmImage { inherit system; interactive = false; };
        # Interactive builder VM. No `mvmctl` verb boots it.
        # Adds `devPackages` on top of the headless package set.
        dev = mkBuilderVmImage { inherit system; interactive = true; };
        stage0-rootfs = mkBuilderVmStage0Rootfs system;
        # Builder kernel config (base + builder delta). `kernel-configfile`
        # name kept — README + the kernel-build GHA reference it.
        kernel-configfile = mkKernelConfigfile system;
        # Standalone kernels + the workload config, for `mvmctl kernel
        # build` and the publish GHA.
        builder-kernel = mkBuilderKernel system;
        workload-kernel = mkWorkloadKernel system;
        workload-kernel-configfile = mkWorkloadKernelConfigfile system;
        workload-sizeopt-kernel = mkWorkloadKernelSizeopt system;
        workload-sizeopt-kernel-configfile = mkWorkloadKernelSizeoptConfigfile system;
      });
    };
}
