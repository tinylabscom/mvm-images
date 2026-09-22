{
  description = "mvm generic rootless tenant image — sealed, NIC-less OCI capability floor";

  # Both variants build-validated (aarch64-linux) via
  # nix-in-docker on the authoring host: `default`/`prod` emits
  # {vmlinux, rootfs.ext4, rootfs.verity, rootfs.roothash,
  # rootfs-closure-paths, mvm-meta.json} with a valid 64-hex verity roothash and a
  # `sealed:true, accessible:false,
  # overlayAware:true, rootlessEntrypoint:true` sidecar; `dev` emits
  # {vmlinux, rootfs.ext4, mvm-meta.json} with `sealed:false, accessible:true`.
  # The x86_64-linux build + the actual VM boot run in CI / on a runtime host.
  # Inputs come from this repository's root `flake.nix`, which pins nixpkgs,
  # microvm.nix and the exact `mvm` commit once for every image and calls
  # `outputs` below with them. This file is not a flake on its own.

  outputs =
    { self, nixpkgs, microvm, mvm-src, ... }:
    let
      systems = [ "aarch64-linux" "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;

      # The `mvm` source tree at the commit the root flake pins. Everything this
      # image reads from `mvm` resolves against it; there is no local override.
      workspaceRoot = mvm-src.outPath;

      # Filtered workspace (mirrors builder-vm/runtime-overlay) for mkGuest.
      workspace =
        (import (workspaceRoot + "/nix/lib/workspace-filter.nix") {
          inherit (nixpkgs) lib;
        })
        { inherit workspaceRoot; };

      # The `mvm` flake, evaluated against this flake's pinned inputs and the
      # filtered workspace; mkGuest is its user-facing `lib.<system>` output.
      mvm = (import (workspaceRoot + "/nix/flake.nix")).outputs {
        self = { };
        inherit nixpkgs microvm;
        mvm-workspace = workspace;
      };

      libFor = system: mvm.lib.${system};

      # Rootless is a distinct workload posture. It shares the audited kernel
      # base but keeps its broader namespace/cgroup floor out of the minimal
      # default-tenant kernel.
      kernelBaseFor = pkgs:
        import ../../kernel/base.nix { inherit pkgs; };
      mkRootlessKernel = pkgs:
        import ../../kernel/rootless.nix
          { inherit pkgs; base = kernelBaseFor pkgs; };

      # Verity determinism — copied verbatim from runtime-overlay
      # (nix/images/runtime-overlay/flake.nix:180-203). MUST stay in lockstep
      # with `mvm_build::oci_to_rootfs::verity::VeritysetupOptions::default` and
      # the guest agent's DATA_BLOCK_SIZE.
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

      # Host<->guest contract this rootfs speaks. MUST stay in lockstep with
      # `PROTOCOL_VERSION_AUTHENTICATED` in
      # crates/mvm-contract/src/policy/security.rs — a rootfs that claims a
      # version it does not speak is worse than one that claims none.
      guestProtocolVersion = 2;

      # Which published image line these bytes belong to. Supplied by the
      # release build; empty for a local build of a working tree, which
      # genuinely belongs to no published line. Deliberately not a constant in
      # this file: a hardcoded tag would keep reading as truth after the line
      # moved past it.
      bootImageTag = builtins.getEnv "MVM_BOOT_IMAGE_TAG";

      # The commit whose mk-guest.nix produced this rootfs. mk-guest.nix is
      # `mvm`'s, so this is the `mvm` input's commit — never this repository's
      # own revision, which would tie the rootfs bytes to unrelated commits here.
      # A local mvm checkout passed with `--override-input mvm path:<dir>` has no
      # revision to resolve and leaves this empty rather than inventing one,
      # exactly as mvm's own flake does for a `path:` build.
      generatorRev = mvm-src.rev or mvm-src.dirtyRev or "";

      # Serialize mkGuest's `passthru.mvm` into the GuestSidecar wire shape
      # (crates/mvm-build/src/builder_vm.rs, #[serde(rename_all="camelCase")]).
      #
      # `source` and `builtAt` describe how the bytes reached a cache, which is
      # a fact about the installing host, not about this build. A Nix build has
      # no clock, so `builtAt` stays empty here and is stamped by whoever puts
      # the image on disk; `source` starts as the truth this build knows and is
      # overwritten by the fetch path, for which it is no longer true.
      sidecarJson = mvm:
        builtins.toJSON {
          # `runtimeLean` belongs in this list for the same reason
          # `overlayAware` does: the required-overlay admission gate reads both,
          # and a field mkGuest sets but this serializer drops reaches the gate
          # as its `false` default. That is how a rootfs carrying no baked agent
          # at all came to be refused as one that might silently degrade to one.
          inherit (mvm)
            name accessible sealed entrypointKind initSystem
            expectedBootMs agentBinary rootlessEntrypoint hypervisor overlayAware
            runtimeLean;
          imageTag = bootImageTag;
          source = "built-local";
          builtAt = "";
          protocolVersion = guestProtocolVersion;
          inherit generatorRev;
        };

      # One variant. `sealed = true` → prod (verity-sealed, rootless, no
      # do_exec); `sealed = false` → dev (accessible, exec-able).
      mkVariant = { system, sealed, smoke ? false }:
        let
          pkgs = import nixpkgs { inherit system; };
          lib = libFor system;
          kernelPkg = mkRootlessKernel pkgs;
          # What the consumer's loader takes, not what the build happens to
          # leave lying around: arm64 loaders want `Image`, Firecracker's
          # x86_64 loader wants an uncompressed ELF `vmlinux`. base.nix emits
          # the ELF beside the bzImage for exactly this.
          kernelFile = if pkgs.stdenv.hostPlatform.isAarch64 then "Image" else "vmlinux";
          qemuKernelFile = if pkgs.stdenv.hostPlatform.isAarch64 then "Image" else "bzImage";
          imageName =
            if smoke then "mvm-rootless-microvm-smoke"
            else if sealed then "mvm-rootless-microvm"
            else "mvm-rootless-microvm-dev";
          rootlessProbe = ''
            #!/bin/sh
            set -eu
            test "$(id -u)" = 1000
            test -w /sys/fs/cgroup/mvm-workload
            probe=/sys/fs/cgroup/mvm-workload/mvm-rootless-probe-$$
            mkdir "$probe"
            rmdir "$probe"
            test ! -e /dev/net/tun
            for dev in /sys/class/net/*; do
              test "$(basename "$dev")" = lo
            done
            unshare -Urmpf /bin/true
            crun --version >/dev/null
            fuse-overlayfs --version >/dev/null
            echo MVM-ROOTLESS-READY
            exec sleep infinity
          '';
          rootfsPkg = lib.mkGuest {
            name = imageName;
            # command form → sealed/prod; shell form → dev/accessible.
            entrypoint =
              if sealed
              then {
                command =
                  if smoke
                  then [ "/usr/local/bin/mvm-rootless-probe" ]
                  else [ "/bin/sleep" "infinity" ];
              }
              else { shell = "/bin/sh"; };
            # mkGuest supplies its own static busybox and pre-installs its
            # applet links. Re-adding the dynamic package here would pull the
            # glibc runtime closure into the sealed tenant image.
            packages = [
              pkgs.crun
              pkgs.fuse-overlayfs
            ];
            extraFiles = nixpkgs.lib.optionalAttrs smoke {
              "/usr/local/bin/mvm-rootless-probe" = {
                content = rootlessProbe;
                mode = "0555";
              };
              # The lean rootfs has no free blocks at boot. These sentinels
              # make the init-created mountpoints exist without resizing the
              # image; the runtime still mounts its data over them.
              "/mnt/config/.mvm-rootless-smoke" = {
                content = "";
                mode = "0444";
              };
              "/mnt/secrets/.mvm-rootless-smoke" = {
                content = "";
                mode = "0444";
              };
            };
            # mkGuest's `kernel` arg supplies the in-rootfs module tree; the
            # rootless kernel is module-free (DM_VERITY built-in), passed for
            # parity with a real workload image.
            kernel = kernelPkg;
          };
          meta = rootfsPkg.passthru.mvm;
        in
        pkgs.runCommand imageName
          {
            nativeBuildInputs = [ pkgs.e2fsprogs (pinnedCryptsetupFor pkgs) pkgs.coreutils ];
            # Expose the inner mkGuest rootfs as `passthru.rootfs` (the
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

            # Direct-QEMU test artifact. On x86 QEMU's Linux loader consumes
            # a bzImage while Firecracker consumes the ELF above; both are
            # built from the exact same resolved kernel configuration.
            if [ -f ${kernelPkg}/${qemuKernelFile} ]; then
              cp ${kernelPkg}/${qemuKernelFile} $out/kernel.img
            else
              echo "kernel ${kernelPkg} produced no ${qemuKernelFile}" >&2
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

            # Rootfs → rootfs.ext4 (mkGuest emits a single ext4).
            if [ -f ${rootfsPkg} ]; then
              cp ${rootfsPkg} $out/rootfs.ext4
            else
              img=$(find ${rootfsPkg} -maxdepth 1 \( -name '*.img' -o -name '*.ext4' \) | head -1)
              [ -n "$img" ] || { echo "mkGuest output ${rootfsPkg} has no .img/.ext4" >&2; ls -la ${rootfsPkg} >&2; exit 1; }
              cp "$img" $out/rootfs.ext4
            fi

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
              $out/kernel.img \
              $out/rootfs.ext4 \
              $out/rootfs-closure-paths \
              $out/mvm-meta.json
          ''
          + nixpkgs.lib.optionalString sealed ''

            # dm-verity seal (prod only) — runtime-overlay recipe verbatim.
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
        smoke = mkVariant { inherit system; sealed = true; smoke = true; };
      });
    };
}
