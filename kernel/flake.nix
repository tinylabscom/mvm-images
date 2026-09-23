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
          # THROWAWAY bisect control #5: Debian's own gcc-14 driver + cc1
          # (binaries from the Debian arm64 debs, patchelf'd onto the nix
          # glibc + nix runtime libs) building the rootless config with the
          # plain make recipe. Hydra's as/ld remain (PATH). If this passes,
          # the hydra gcc/cc1 binaries miscompile the kernel. Delete with
          # the bisect.
          debianGcc = pkgs.runCommand "debian-gcc-14" {
            nativeBuildInputs = [ pkgs.dpkg pkgs.patchelf ];
          } ''
            set -euo pipefail
            mkdir -p $out
            dpkg-deb -x ${debGccReal} $out
            dpkg-deb -x ${debCppReal} $out
            dpkg-deb -x ${debGccBase} $out
            dpkg-deb -x ${debCc1} $out
            dpkg-deb -x ${debIsl} $out
            dpkg-deb -x ${debMpfr} $out
            dpkg-deb -x ${debMpc} $out
            dpkg-deb -x ${debZstd} $out
            dpkg-deb -x ${debStdCpp} $out
            dpkg-deb -x ${debGccS} $out
            dpkg-deb -x ${debLibgccDev} $out
            dpkg-deb -x ${debBinutils} $out
            LIBS=$out/usr/lib/aarch64-linux-gnu:$out/lib/aarch64-linux-gnu
            NIXLIBS=${pkgs.gmp}/lib:${pkgs.mpfr}/lib:${pkgs.libmpc}/lib:${pkgs.isl}/lib:${pkgs.zstd}/lib:${pkgs.zlib}/lib:${pkgs.stdenv.cc.cc.lib}/lib
            # Every dynamically-linked ELF in the unpacked debs gets the nix
            # glibc interpreter and a rath spanning the merged deb tree plus
            # the nix runtime libs gcc's driver/cc1 link against (gmp, mpfr,
            # mpc, isl, zstd, zlib, libstdc++/libgcc). Failures tolerated:
            # scripts and static binaries have no .interp to patch.
            find $out/usr/bin $out/usr/lib/aarch64-linux-gnu $out/usr/lib/gcc/aarch64-linux-gnu/14 $out/usr/libexec/gcc/aarch64-linux-gnu/14 \
              -maxdepth 1 -type f ! -type l 2>/dev/null | while read f; do
              patchelf --set-interpreter ${pkgs.glibc}/lib/ld-linux-aarch64.so.1 \
                       --set-rpath "$LIBS:$NIXLIBS" "$f" 2>/dev/null || true
            done
            echo "== cc1 rpath =="
            patchelf --print-rpath $out/usr/libexec/gcc/aarch64-linux-gnu/14/cc1 || true
            ls -la ${pkgs.isl}/lib/libisl.so.* 2>/dev/null || echo "NIX ISL LIB MISSING"
            echo "== cc1 needed =="
            patchelf --print-needed $out/usr/libexec/gcc/aarch64-linux-gnu/14/cc1 || true
          '';
          debGccReal = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/g/gcc-14/gcc-14-aarch64-linux-gnu_14.2.0-19_arm64.deb";
            hash = "sha256-X/c2ozK6XWCtRjNV7SXIw52hKBrPkVXML4pV/B1Hi+g=";
          };
          debCppReal = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/g/gcc-14/cpp-14-aarch64-linux-gnu_14.2.0-19_arm64.deb";
            hash = "sha256-jliKw+/gb3eEsI/qxYTHDmllt0wEW65+ToC7QpOPfb4=";
          };
          debGccBase = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/g/gcc-14/gcc-14-base_14.2.0-19_arm64.deb";
            hash = "sha256-NO6QZ5sBjA5kI0dHpMTArmt/Y1QRFQN0ZahifC37xZQ=";
          };
          debCc1 = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/g/gcc-14/libcc1-0_14.2.0-19_arm64.deb";
            hash = "sha256-9eOQtf1lQyuCCvpAOmGrTWKGlI/AfKOqy00jCoH1yaU=";
          };
          debIsl = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/i/isl/libisl23_0.28-1_arm64.deb";
            hash = "sha256-N3ZA/utsGWwvCrjWakVP+ihdexbR2oUKA3VaTRN3tOo=";
          };
          debMpfr = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/m/mpfr4/libmpfr6_4.2.2-3_arm64.deb";
            hash = "sha256-Jsefygf1o1fmKp79VNNSJPyu9PdFYDbEfx7oI+OHMfM=";
          };
          debMpc = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/m/mpclib3/libmpc3_1.3.1-3_arm64.deb";
            hash = "sha256-hVtm3VKyATQnanpkPoiNM8ndo8eX77PJbMzEC/wGC20=";
          };
          debZstd = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/libz/libzstd/libzstd1_1.5.7+dfsg-4_arm64.deb";
            hash = "sha256-6Z5uJE1sgPHI6Wy2i7BL/8aEixLj3MWjX2p3XkMy6TY=";
          };
          debStdCpp = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/g/gcc-14/libstdc++6_14.2.0-19_arm64.deb";
            hash = "sha256-ZmmwxSoufGr5rf2rzj/24oYGXN+8e4UoCGK195na6+4=";
          };
          debGccS = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/g/gcc-14/libgcc-s1_14.2.0-19_arm64.deb";
            hash = "sha256-EQi8h4eYM9bZoUXyKkoVzds04GW0tfS5e+5YatusKFE=";
          };
          debBinutils = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/b/binutils/binutils-aarch64-linux-gnu_2.47.50.20260901-1_arm64.deb";
            hash = "sha256-YCUeHPGjazE+lvQp9RL3OpI0qiixcvnYTS4aQSIVUes=";
          };
          debLibgccDev = pkgs.fetchurl {
            url = "http://ftp.debian.org/debian/pool/main/g/gcc-14/libgcc-14-dev_14.2.0-19_arm64.deb";
            hash = "sha256-ZLjrxxgqaaqVJd9uq15+uEm5R+NQ2CFUn4qKPpsZ5bo=";
          };


          # THROWAWAY bisect control #7: pristine upstream binutils 2.44
          # (no nixpkgs patches) for as/ld/nm/objcopy. Hydra binutils carry
          # the nixpkgs patchset; this isolates assembler/linker patching
          # from everything else. Delete with the bisect.
          upstreamBinutils = pkgs.stdenv.mkDerivation {
            pname = "binutils-upstream";
            version = "2.44";
            src = pkgs.fetchurl {
              url = "https://ftp.gnu.org/gnu/binutils/binutils-2.44.tar.xz";
              hash = "sha256-ziAX4FnWPmfduSQOnU7EnCiTYFA1zWDpKtUxd/Q3cjc=";
            };
            configureFlags = [
              "--disable-werror"
              "--disable-nls"
              "--disable-gdb"
              "--disable-jansson"
              "--enable-deterministic-archives"
            ];
            buildPhase = "make -j$NIX_BUILD_CORES all-binutils all-gas all-ld";
            installPhase = ''
              make install-binutils install-gas install-ld
            '';
            # only the binutils tools are wanted; drop the info dir spam
            postInstall = ''
              rm -rf $out/share
            '';
          };
          plainRootlessUpstreamBinutils = pkgs.stdenv.mkDerivation {
            pname = "linux-plain-rootless-upstream-binutils";
            version = base.kernelVersion;
            src = kernelSourceTreeForGcc13;
            nativeBuildInputs = with pkgs; [ gnumake bison flex bc perl pkg-config openssl gcc ];
            postPatch = ''
              patchShebangs scripts/
            '';
            dontConfigure = true;
            buildPhase = ''
              runHook preBuild
              export ARCH=arm64
              cp ${rootless.passthru.configfile} .config
              chmod u+w .config
              make -j$NIX_BUILD_CORES CC=gcc HOSTCC=gcc olddefconfig
              make -j$NIX_BUILD_CORES CC=gcc HOSTCC=gcc \
                AS="${upstreamBinutils}/bin/as" \
                LD="${upstreamBinutils}/bin/ld.bfd" \
                NM="${upstreamBinutils}/bin/nm" \
                OBJCOPY="${upstreamBinutils}/bin/objcopy" \
                OBJDUMP="${upstreamBinutils}/bin/objdump" \
                AR="${upstreamBinutils}/bin/ar" \
                STRIP="${upstreamBinutils}/bin/strip" \
                Image
              runHook postBuild
            '';
            installPhase = ''
              runHook preInstall
              mkdir -p $out
              cp arch/arm64/boot/Image $out/Image
              cp .config $out/config
              runHook postInstall
            '';
          };

          # THROWAWAY bisect control #6: the rootless config, plain recipe,
          # nixpkgs gcc (hydra cc1) but DEBIAN's as/ld/nm/objcopy. Isolates
          # the assembler/linker from the compiler. Delete with the bisect.
          plainRootlessDebBinutils = pkgs.stdenv.mkDerivation {
            pname = "linux-plain-rootless-deb-binutils";
            version = base.kernelVersion;
            src = kernelSourceTreeForGcc13;
            nativeBuildInputs = with pkgs; [ gnumake bison flex bc perl pkg-config openssl gcc ];
            postPatch = ''
              patchShebangs scripts/
            '';
            dontConfigure = true;
            buildPhase = ''
              runHook preBuild
              export ARCH=arm64
              cp ${rootless.passthru.configfile} .config
              chmod u+w .config
              make -j$NIX_BUILD_CORES CC=gcc HOSTCC=gcc olddefconfig
              make -j$NIX_BUILD_CORES CC=gcc HOSTCC=gcc \
                AS="${debianGcc}/usr/bin/aarch64-linux-gnu-as" \
                LD="${debianGcc}/usr/bin/aarch64-linux-gnu-ld.bfd" \
                NM="${debianGcc}/usr/bin/aarch64-linux-gnu-nm" \
                OBJCOPY="${debianGcc}/usr/bin/aarch64-linux-gnu-objcopy" \
                OBJDUMP="${debianGcc}/usr/bin/aarch64-linux-gnu-objdump" \
                AR="${debianGcc}/usr/bin/aarch64-linux-gnu-ar" \
                STRIP="${debianGcc}/usr/bin/aarch64-linux-gnu-strip" \
                Image
              runHook postBuild
            '';
            installPhase = ''
              runHook preInstall
              mkdir -p $out
              cp arch/arm64/boot/Image $out/Image
              cp .config $out/config
              runHook postInstall
            '';
          };

          plainRootlessDebGcc = pkgs.stdenv.mkDerivation {
            pname = "linux-plain-rootless-deb-gcc";
            version = base.kernelVersion;
            src = kernelSourceTreeForGcc13;
            nativeBuildInputs = with pkgs; [ gnumake bison flex bc perl pkg-config openssl gcc ];
            postPatch = ''
              patchShebangs scripts/
            '';
            dontConfigure = true;
            buildPhase = ''
              runHook preBuild
              export ARCH=arm64
              cp ${rootless.passthru.configfile} .config
              chmod u+w .config
              export LD_LIBRARY_PATH=${debianGcc}/usr/lib/aarch64-linux-gnu:${debianGcc}/lib/aarch64-linux-gnu:${pkgs.gmp}/lib:${pkgs.mpfr}/lib:${pkgs.libmpc}/lib:${pkgs.isl}/lib:${pkgs.zstd}/lib:${pkgs.zlib}/lib:${pkgs.stdenv.cc.cc.lib}/lib''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
              DEBCC="${debianGcc}/usr/bin/aarch64-linux-gnu-gcc-14 -B${debianGcc}/usr/libexec/gcc/aarch64-linux-gnu/14/ -B${debianGcc}/usr/lib/gcc/aarch64-linux-gnu/14/ -B${debianGcc}/usr/bin/"
              echo "== debian gcc probe =="
              patchelf --print-rpath ${debianGcc}/usr/libexec/gcc/aarch64-linux-gnu/14/cc1 || true
              ls ${pkgs.isl}/lib/ || true
              echo 'int main(void){return 0;}' > /tmp/probe.c
              $DEBCC /tmp/probe.c -o /tmp/probe 2>&1 | tail -20 || true
              ls -la /tmp/probe 2>/dev/null || echo "PROBE LINK FAILED"
              echo "== kconfig as-version replication =="
              ${debianGcc}/usr/bin/aarch64-linux-gnu-gcc-14 -B${debianGcc}/usr/libexec/gcc/aarch64-linux-gnu/14/ -Wa,--version -c -x assembler-with-cpp /dev/null -o /dev/null 2>&1 | head -5
              echo "as-version rc=$?"
              echo "== assembler probe =="
              which as || echo "no as on PATH"
              as --version 2>&1 | head -2 || true
              echo 'nop' > /tmp/a.s
              $DEBCC -c -x assembler /tmp/a.s -o /tmp/a.o 2>&1 | head -10 || true
              ls -la /tmp/a.o 2>/dev/null || echo "ASM FAILED"
              make -j$NIX_BUILD_CORES CC="$DEBCC" HOSTCC=gcc olddefconfig
              make -j$NIX_BUILD_CORES CC="$DEBCC" HOSTCC=gcc Image
              runHook postBuild
            '';
            installPhase = ''
              runHook preInstall
              mkdir -p $out
              cp arch/arm64/boot/Image $out/Image
              cp .config $out/config
              runHook postInstall
            '';
          };

          # THROWAWAY bisect control #4: the rootless config built by a plain
          # `make` derivation — no nixpkgs kernel machinery, no CC/LD/AR
          # overrides, no postPatch, no KBUILD_BUILD_TIMESTAMP, raw gcc from
          # PATH. Tests the nixpkgs manual-config recipe itself. Delete with
          # the bisect.
          plainRootless = pkgs.stdenv.mkDerivation {
            pname = "linux-plain-rootless";
            version = base.kernelVersion;
            src = kernelSourceTreeForGcc13;
            nativeBuildInputs = with pkgs; [ gnumake bison flex bc perl pkg-config openssl gcc ];
            postPatch = ''
              patchShebangs scripts/
            '';
            dontConfigure = true;
            buildPhase = ''
              runHook preBuild
              export ARCH=arm64
              cp ${rootless.passthru.configfile} .config
              chmod u+w .config
              make -j$NIX_BUILD_CORES CC=gcc HOSTCC=gcc olddefconfig
              make -j$NIX_BUILD_CORES CC=gcc HOSTCC=gcc Image
              runHook postBuild
            '';
            installPhase = ''
              runHook preInstall
              mkdir -p $out
              cp arch/arm64/boot/Image $out/Image
              cp .config $out/config
              runHook postInstall
            '';
          };

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
          plain-rootless-vmlinux = plainRootless;
          plain-rootless-configfile = plainRootless.configfile or (pkgs.runCommand "plain-rootless-config" { } "cp ${plainRootless}/config $out");
          plain-deb-gcc-vmlinux = plainRootlessDebGcc;
          plain-deb-binutils-vmlinux = plainRootlessDebBinutils;
          plain-upstream-binutils-vmlinux = plainRootlessUpstreamBinutils;
          plain-upstream-binutils-configfile = pkgs.runCommand "plain-upstream-binutils-config" { } "cp ${plainRootlessUpstreamBinutils}/config $out";
          plain-deb-binutils-configfile = pkgs.runCommand "plain-deb-binutils-config" { } "cp ${plainRootlessDebBinutils}/config $out";
          plain-deb-gcc-configfile = pkgs.runCommand "plain-deb-gcc-config" { } "cp ${plainRootlessDebGcc}/config $out";
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
