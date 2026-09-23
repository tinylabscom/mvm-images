{
  # Standalone, publishable view of the slim microVM kernel.
  #
  # The builder-VM flake consumes the same `base.nix` / `builder.nix` /
  # `workload.nix` through its `workspaceRoot` import (so the kernel
  # resolves under the `path:` URL the libkrun builder VM fetches). This
  # flake is an ADDITIVE publish surface: it builds the same kernels as
  # first-class outputs plus the size metrics and the content-addressed
  # artifact manifest a release workflow uploads. It does not change how
  # the builder VM gets its kernel — both import the identical files, so
  # the derivations match.
  description = "mvm slim microVM kernel — publishable vmlinux / configfile / metrics / manifest";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

  outputs =
    { self, nixpkgs }:
    let
      systems = [ "aarch64-linux" "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      # Reusable accessor — callers pass their own pkgs so the nixpkgs
      # pin stays with the consumer, not duplicated here.
      lib.kernelBase = pkgs: import ./base.nix { inherit pkgs; };

      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          base = import ./base.nix { inherit pkgs; };
          workload = import ./workload.nix { inherit pkgs base; };
          rootless = import ./rootless.nix { inherit pkgs base; };
          datapath = import ./datapath.nix { inherit pkgs base; };

          # THROWAWAY bisect control #3: the rootless kernel built by gcc13
          # instead of the default gcc 14. Tests whether the fork-path
          # miscompile is specific to the nixpkgs gcc14 codegen. Delete with
          # the bisect.
          rootlessGcc13 = (pkgs.linuxManualConfig.override {
            stdenv = pkgs.gcc13Stdenv;
          }) {
            src = kernelSourceTreeForGcc13;
            version = base.kernelVersion;
            modDirVersion = base.kernelVersion;
            configfile = rootless.passthru.configfile;
            allowImportFromDerivation = false;
          };
          kernelSourceTreeForGcc13 = pkgs.buildPackages.runCommand "linux-gcc13-source"
            {
              nativeBuildInputs = [ pkgs.buildPackages.libarchive ];
            }
            ''
              mkdir -p "$out"
              ${pkgs.buildPackages.libarchive}/bin/bsdtar -xf ${kernelTarballGcc13} -C "$out" --strip-components=1
            '';
          kernelTarballGcc13 = pkgs.fetchurl {
            url = "mirror://kernel/linux/kernel/v6.x/linux-${base.kernelVersion}.tar.xz";
            hash = "sha256-jO4Z4YObtv9NUlTXYZM65qtnBJLV7QMOCagFODINXEw=";
          };

          # THROWAWAY bisect control: near-stock defconfig with none of the
          # base carve-down (only the guest-boot + repro floor requested).
          # Built outside mkKernel on purpose: the all-built-in guards treat
          # a modules-enabled stock config's `=m` symbols as dropped. Delete
          # with the bisect.
          stocktestSrc = pkgs.buildPackages.runCommand "linux-stocktest-source"
            {
              nativeBuildInputs = [ pkgs.buildPackages.libarchive ];
            }
            ''
              mkdir -p "$out"
              ${pkgs.buildPackages.libarchive}/bin/bsdtar -xf ${stocktestTarball} -C "$out" --strip-components=1
            '';
          stocktestConfig = pkgs.buildPackages.runCommandCC "mvm-kernel-config-stocktest"
            {
              nativeBuildInputs = with pkgs.buildPackages; [ gnumake bison flex bc perl pkg-config openssl ];
              enableList = pkgs.lib.concatStringsSep " " [
                "MODULES_N"
                "VIRTIO" "VIRTIO_MMIO" "VIRTIO_PCI" "PCI" "VIRTIO_BLK" "VIRTIO_CONSOLE"
                "HVC_DRIVER" "VSOCKETS" "VIRTIO_VSOCKETS"
                "SERIAL_8250" "SERIAL_8250_CONSOLE" "SERIAL_OF_PLATFORM"
                "BLOCK" "EXT4_FS" "TMPFS" "DEVTMPFS" "DEVTMPFS_MOUNT" "PROC_FS" "SYSFS"
                "MD" "BLK_DEV_DM" "DM_VERITY"
                "NAMESPACES" "UTS_NS" "IPC_NS" "USER_NS" "PID_NS" "NET_NS"
                "CGROUPS" "MEMCG" "CGROUP_PIDS" "CGROUP_SCHED" "FAIR_GROUP_SCHED"
                "CGROUP_FREEZER" "CGROUP_DEVICE" "CGROUP_CPUACCT" "BLK_CGROUP" "CPUSETS"
              ];
            }
            ''
              set -euo pipefail
              cp -a --reflink=auto ${stocktestSrc}/. linux/
              cd linux
              chmod -R u+w .
              export ARCH=arm64
              export SHELL=${pkgs.buildPackages.bash}/bin/bash
              export CONFIG_SHELL=${pkgs.buildPackages.bash}/bin/bash
              if [ ! -x /bin/sh ]; then mkdir -p /bin; ln -s ${pkgs.buildPackages.bash}/bin/sh /bin/sh; fi
              patchShebangs scripts/
              make SHELL="$SHELL" defconfig
              for s in $enableList; do
                if [ "$s" = "MODULES_N" ]; then
                  ./scripts/config --disable MODULES
                else
                  ./scripts/config --enable "$s"
                fi
              done
              make SHELL="$SHELL" olddefconfig
              cp .config $out
            '';
          stocktest = pkgs.linuxManualConfig {
            src = stocktestSrc;
            version = base.kernelVersion;
            modDirVersion = base.kernelVersion;
            configfile = stocktestConfig;
            allowImportFromDerivation = false;
          };

          # THROWAWAY bisect control #2: Debian's own arm64 config (6.18.15),
          # MODULES=n with the virtio/ext4/serial console forced built-in,
          # built by the nixpkgs toolchain. Debian's gcc build of the same
          # config family passes the clone repro; this isolates the compiler
          # from the config. Delete with the bisect.
          stockdebianSrc = pkgs.buildPackages.runCommand "linux-stockdebian-source"
            {
              nativeBuildInputs = [ pkgs.buildPackages.libarchive ];
            }
            ''
              mkdir -p "$out"
              ${pkgs.buildPackages.libarchive}/bin/bsdtar -xf ${stockdebianTarball} -C "$out" --strip-components=1
            '';
          stockdebianTarball = pkgs.fetchurl {
            url = "mirror://kernel/linux/kernel/v6.x/linux-6.18.15.tar.xz";
            hash = "sha256-fHFiFsPEE07Q3mkZVwHmd1d7vN05efMxwYKs0Gvy8XA=";
          };
          stockdebian = pkgs.linuxManualConfig {
            src = stockdebianSrc;
            version = "6.18.15";
            modDirVersion = "6.18.15";
            configfile = ./debian-config-6.18-builtins;
            allowImportFromDerivation = false;
          };
          stockdebian-configfile = pkgs.runCommand "mvm-kernel-config-stockdebian" { } ''
            cp ${./debian-config-6.18-builtins} $out
          '';
          stocktestTarball = pkgs.fetchurl {
            url = "mirror://kernel/linux/kernel/v6.x/linux-${base.kernelVersion}.tar.xz";
            hash = "sha256-jO4Z4YObtv9NUlTXYZM65qtnBJLV7QMOCagFODINXEw=";
          };

          builder = import ./builder.nix { inherit pkgs base; };

          # "aarch64" / "x86_64" for the published filenames (matches the
          # per-arch checksum-manifest naming the downloader verifies).
          arch = nixpkgs.lib.head (nixpkgs.lib.splitString "-" system);
          kver = base.kernelVersion;

          # vmlinux size + built-in symbol count. Pure measurement; the
          # number is what the "tiny kernel" claim is anchored to.
          metricsFor =
            name: kpkg: cfg:
            pkgs.runCommand "mvm-kernel-metrics-${name}-${arch}" { nativeBuildInputs = [ pkgs.gzip ]; } ''
              mkdir -p $out
              img=$(ls ${kpkg}/Image ${kpkg}/bzImage ${kpkg}/vmlinux 2>/dev/null | head -1)
              y=$(grep -c '=y$' ${cfg})
              raw=$(stat -c%s "$img")
              comp=$(gzip -c "$img" | wc -c)
              printf '{"vmlinux_bytes":%d,"vmlinux_compressed_bytes":%d,"y_symbol_count":%d}\n' \
                "$raw" "$comp" "$y" > $out/metrics.json
              ln -s ${cfg} $out/config
            '';

          resolvedConfigs =
            pkgs.runCommand "mvm-kernel-resolved-configs-${arch}" { } ''
              mkdir -p $out
              ln -s ${builder.passthru.configfile} $out/builder.config
              ln -s ${workload.passthru.configfile} $out/workload.config
              ln -s ${rootless.passthru.configfile} $out/rootless.config
              ln -s ${datapath.passthru.configfile} $out/datapath.config
            '';

          workloadSizeopt = import ./workload.nix {
            inherit pkgs base;
            optimizeForSize = true;
          };

          # Content-addressed identity: (kernel_version, config_hash,
          # artifact_hash). Field names mirror the KernelArtifactId type
          # the host resolves a kernel pin against. The checksums file
          # follows the existing hash-verified download format.
          # The file the consumer's loader actually takes. `metricsFor` above
          # deliberately keeps the `ls` probe — it measures the compressed
          # image the tiny-kernel claim is anchored to, and the ELF is ~20 MB
          # against the bzImage's ~8 MB, so repointing it would silently
          # restate that claim.
          #
          # This manifest is different: `update.rs::download_kernel` verifies a
          # fetched `vmlinux-<arch>-<variant>` against it and hands the result
          # to a backend as a `vmlinux`. On x86_64 `ls` sorts `bzImage` ahead of
          # `vmlinux`, so the manifest recorded a bzImage's digest under the
          # name `vmlinux` — and since the kernel package now carries both, it
          # picked the wrong one of two present files. The download then
          # verifies green and boots nothing. A hash proves provenance and says
          # nothing about format — the same way an unloadable image once
          # shipped under a name the loader is documented against.
          manifestKernelFile = if pkgs.stdenv.hostPlatform.isAarch64 then "Image" else "vmlinux";
          manifestFor =
            kpkg: cfg:
            pkgs.runCommand "mvm-kernel-manifest-${arch}" { } ''
              mkdir -p $out
              img=${kpkg}/${manifestKernelFile}
              if [ ! -f "$img" ]; then
                echo "ERROR: kernel ${kpkg} produced no ${manifestKernelFile}." >&2
                exit 1
              fi

              # Assert the format the name promises, so a manifest cannot
              # publish a digest for bytes the loader will refuse.
              magic0=$(${pkgs.coreutils}/bin/od -An -tx1 -N4 "$img" | tr -d ' \n')
              magic56=$(${pkgs.coreutils}/bin/od -An -c -j56 -N4 "$img" | tr -d ' \n')
              ${
                if pkgs.stdenv.hostPlatform.isAarch64 then ''
                if [ "$magic56" != "ARMd" ]; then
                  echo "ERROR: $img is not an arm64 Image (magic@56='$magic56', want 'ARMd')." >&2
                  exit 1
                fi
                '' else ''
                if [ "$magic0" != "7f454c46" ]; then
                  echo "ERROR: $img is not an ELF kernel (magic@0=0x$magic0, want 0x7f454c46)." >&2
                  echo "The manifest names this artifact 'vmlinux', and Firecracker's x86_64" >&2
                  echo "loader takes an uncompressed ELF only. Publishing a bzImage digest" >&2
                  echo "under that name verifies green and boots nothing." >&2
                  exit 1
                fi
                ''
              }

              ch=$(sha256sum ${cfg} | cut -d' ' -f1)
              ah=$(sha256sum "$img" | cut -d' ' -f1)
              printf '{"kernel_version":"%s","config_hash":"%s","artifact_hash":"%s"}\n' \
                "${kver}" "$ch" "$ah" > $out/kernel-${arch}.json
              printf '%s  vmlinux\n' "$ah" > $out/kernel-${arch}-checksums-sha256.txt
            '';
        in
        {
          workload-vmlinux = workload;
          rootless-vmlinux = rootless;
          datapath-vmlinux = datapath;
          builder-vmlinux = builder;
          workload-configfile = workload.passthru.configfile;
          rootless-configfile = rootless.passthru.configfile;
          rootless-gcc13-vmlinux = rootlessGcc13;
          rootless-gcc13-configfile = rootless.passthru.configfile;
          datapath-configfile = datapath.passthru.configfile;
          builder-configfile = builder.passthru.configfile;
          resolved-configs = resolvedConfigs;
          builder-metrics = metricsFor "builder" builder builder.passthru.configfile;
          workload-metrics = metricsFor "workload" workload workload.passthru.configfile;
          rootless-metrics = metricsFor "rootless" rootless rootless.passthru.configfile;
          datapath-metrics = metricsFor "datapath" datapath datapath.passthru.configfile;
          stocktest-vmlinux = stocktest;
          stocktest-configfile = stocktest.passthru.configfile;
          stockdebian-vmlinux = stockdebian;
          stockdebian-configfile = stockdebian-configfile;

          metrics = metricsFor "workload" workload workload.passthru.configfile;
          workload-sizeopt-vmlinux = workloadSizeopt;
          workload-sizeopt-configfile = workloadSizeopt.passthru.configfile;
          workload-sizeopt-metrics =
            metricsFor "workload-sizeopt" workloadSizeopt workloadSizeopt.passthru.configfile;
          artifact-manifest = manifestFor workload workload.passthru.configfile;
        }
      );
    };
}
