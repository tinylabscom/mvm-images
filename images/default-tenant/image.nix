{
  description = "Generic default tenant Linux base — dev and verity-sealed variants";

  # `default` emits
  # {vmlinux, rootfs.ext4, rootfs.verity, rootfs.roothash,
  # rootfs-closure-paths, mvm-meta.json} with a valid 64-hex verity roothash and a
  # `sealed:true, accessible:false,
  # overlayAware:true, rootlessEntrypoint:true` sidecar; `dev` emits
  # {vmlinux, rootfs.ext4, mvm-meta.json} with `sealed:false, accessible:true`.
  # Both architectures require Linux build, reproduction and boot validation.
  # Inputs come from this repository's root flake. No guest-runtime source
  # participates in composition; the CLI supplies PID 1 at boot.

  outputs =
    { self, nixpkgs, microvm, ... }:
    let
      systems = [ "aarch64-linux" "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      baseImageContract = import ../base-image-contract.nix;

      # Workload kernel — the single shared definition in this repository's
      # `kernel/`, identical to the one builder-vm builds. Consuming the shared
      # `workload.nix` keeps the workload symbol set in ONE place — no second
      # enable list to drift.
      kernelBaseFor = pkgs:
        import ../../kernel/base.nix { inherit pkgs; };
      mkWorkloadKernel = pkgs:
        import ../../kernel/workload.nix
          { inherit pkgs; base = kernelBaseFor pkgs; };

      # Linux-layer verity format: fixed block sizes, salt and UUID make the
      # sealed root reproducible and match the verified-boot artifact contract.
      verityDataBlockSize = 1024;
      verityHashBlockSize = 4096;
      veritySalt = "0000000000000000000000000000000000000000000000000000000000000000";
      verityHashAlgorithm = "sha256";
      # Mirrors `mvm_fs::oci_to_rootfs::verity::MVM_VERITY_PINNED_UUID`, so the
      # hash device's superblock carries no random UUID.
      verityUuid = "00000000-0000-0000-0000-000000000003";
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

      # Which published image line these bytes belong to. Supplied by the
      # release build; empty for a local build of a working tree, which
      # genuinely belongs to no published line. Deliberately not a constant in
      # this file: a hardcoded tag would keep reading as truth after the line
      # moved past it.
      bootImageTag = builtins.getEnv "MVM_BOOT_IMAGE_TAG";

      generatorRev = self.rev or self.dirtyRev or "";

      # Serialize the locally composed base's metadata into the cache wire shape
      # (crates/mvm-build/src/builder_vm.rs, #[serde(rename_all="camelCase")]).
      #
      # `source` and `builtAt` describe how the bytes reached a cache, which is
      # a fact about the installing host, not about this build. A Nix build has
      # no clock, so `builtAt` stays empty here and is stamped by whoever puts
      # the image on disk; `source` starts as the truth this build knows and is
      # overwritten by the fetch path, for which it is no longer true.
      sidecarJson = mvm:
        builtins.toJSON {
          # These cache flags agree: no baked agent or fallback exists.
          inherit (mvm)
            name accessible sealed entrypointKind initSystem
            expectedBootMs agentBinary rootlessEntrypoint hypervisor overlayAware
            runtimeLean;
          imageTag = bootImageTag;
          source = "built-local";
          builtAt = "";
          inherit baseImageContract;
          inherit generatorRev;
        };

      # One variant. `sealed = true` → prod (verity-sealed, rootless, no
      # do_exec); `sealed = false` → dev (accessible, exec-able).
      mkVariant = { system, sealed }:
        let
          pkgs = import nixpkgs { inherit system; };
          kernelPkg = mkWorkloadKernel pkgs;
          # What the consumer's loader takes, not what the build happens to
          # leave lying around: arm64 loaders want `Image`, Firecracker's
          # x86_64 loader wants an uncompressed ELF `vmlinux`. base.nix emits
          # the ELF beside the bzImage for exactly this.
          kernelFile = if pkgs.stdenv.hostPlatform.isAarch64 then "Image" else "vmlinux";
          imageName = if sealed then "mvm-default-microvm" else "mvm-default-microvm-dev";
          rootfsPkg = (import ../mk-rootfs.nix { inherit pkgs; }) {
            name = imageName;
            inherit sealed;
            # Only static busybox is needed in the default Linux base.
            packages = [ ];
          };
          meta = rootfsPkg.passthru.mvm;
        in
        pkgs.runCommand imageName
          {
            nativeBuildInputs = [ pkgs.e2fsprogs (pinnedCryptsetupFor pkgs) pkgs.coreutils ];
            # Expose the inner base rootfs as `passthru.rootfs` (the
            # convention builder-vm uses): the builder VM's nix-build cmd.sh
            # emits mvm-meta.json by eval'ing `<attr>.passthru.rootfs.passthru.mvm`
            # for runCommand-wrapped images (builder_vm_runtime.rs). Without
            # this the dev-build path would produce an image admission refuses.
            passthru = { rootfs = rootfsPkg; kernel = kernelPkg; };
          }
          (''
            set -euo pipefail
            mkdir -p $out

            # Kernel → vmlinux.
            #
            # The asset is named `vmlinux`, but that name has never carried a
            # format. On aarch64 it is an arm64 `Image`, and that is correct:
            # arm64 loaders take `Image`. On x86_64 Firecracker loads an
            # uncompressed ELF and nothing else, so a bzImage published under
            # this name dies in the loader — before init, before userspace —
            # with "Kernel Loader: Invalid Elf magic number". v0.17.0 shipped
            # exactly that, and the only lane that would have caught it boots
            # the same asset and had never produced a result.
            #
            # So pick by what the consumer's loader needs, and then assert the
            # bytes. A probe chain that accepts whichever file happens to exist
            # is how the format stopped being checked at all.
            if [ -f ${kernelPkg}/${kernelFile} ]; then
              cp ${kernelPkg}/${kernelFile} $out/vmlinux
            else
              echo "kernel ${kernelPkg} produced no ${kernelFile}" >&2
              ls -la ${kernelPkg} >&2
              exit 1
            fi

            # Assert the emitted format. Reading the first bytes is the whole
            # check: ELF is "\x7fELF" at 0, an arm64 Image carries "ARMd" at
            # offset 56 (Documentation/arm64/booting.rst).
            magic0=$(${pkgs.coreutils}/bin/od -An -tx1 -N4 "$out/vmlinux" | tr -d ' \n')
            magic56=$(${pkgs.coreutils}/bin/od -An -c -j56 -N4 "$out/vmlinux" | tr -d ' \n')
            ${
              if pkgs.stdenv.hostPlatform.isAarch64 then ''
              if [ "$magic56" != "ARMd" ]; then
                echo "ERROR: $out/vmlinux is not an arm64 Image (magic@56='$magic56', want 'ARMd')." >&2
                exit 1
              fi
              '' else ''
              if [ "$magic0" != "7f454c46" ]; then
                echo "ERROR: $out/vmlinux is not an ELF kernel (magic@0=0x$magic0, want 0x7f454c46)." >&2
                echo "Firecracker's x86_64 loader takes an uncompressed ELF vmlinux only. A" >&2
                echo "bzImage here boots nothing: it fails in the loader with 'Invalid Elf" >&2
                echo "magic number', before init runs, so the guest console says nothing." >&2
                echo "Emit the ELF the kernel build produces alongside its bzImage, or" >&2
                echo "decompress the bzImage with the kernel tree's scripts/extract-vmlinux." >&2
                exit 1
              fi
              ''
            }

            cp ${rootfsPkg} $out/rootfs.ext4

            # Preserve the exact registered runtime closure as a release
            # provenance artifact. The footprint ledger validates every line
            # as a complete hash-anchored store path before counting it.
            cp ${rootfsPkg.passthru.rootfsClosureInfo}/store-paths \
              $out/rootfs-closure-paths

            # Overlay-aware sidecar so `admit_overlay_aware` passes.
            cat > $out/mvm-meta.json <<'META'
            ${sidecarJson meta}
            META

            chmod 0644 \
              $out/vmlinux \
              $out/rootfs.ext4 \
              $out/rootfs-closure-paths \
              $out/mvm-meta.json
          ''
          + nixpkgs.lib.optionalString sealed ''

            # dm-verity seal (prod only) in the Linux-layer artifact format.
            touch $out/rootfs.verity
            veritysetup_out=$(
              veritysetup format \
                --data-block-size=${toString verityDataBlockSize} \
                --hash-block-size=${toString verityHashBlockSize} \
                --salt=${veritySalt} \
                --hash=${verityHashAlgorithm} \
                --uuid=${verityUuid} \
                $out/rootfs.ext4 \
                $out/rootfs.verity
            )
            echo "$veritysetup_out" \
              | grep -i '^Root hash:' \
              | sed 's/^[Rr]oot [Hh]ash:[[:space:]]*//' \
              | tr 'A-F' 'a-f' \
              > $out/rootfs.roothash
            chmod 0644 $out/rootfs.verity $out/rootfs.roothash
          '');

    in
    {
      packages = forAllSystems (system: {
        # `default` = prod (the variant the release job ships).
        default = mkVariant { inherit system; sealed = true; };
        prod = mkVariant { inherit system; sealed = true; };
        dev = mkVariant { inherit system; sealed = false; };
      });
    };
}
