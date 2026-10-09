{ pkgs }:
let
  probe = pkgs.stdenv.mkDerivation {
    pname = "experimental-attribution-probe";
    version = "0";
    src = ./.;
    nativeBuildInputs = [ pkgs.llvmPackages.clang-unwrapped pkgs.pkg-config ];
    buildInputs = [ pkgs.libbpf pkgs.elfutils pkgs.zlib ];
    buildPhase = ''
      runHook preBuild
      # Nix's native cc-wrapper adds host-only hardening flags that are
      # invalid for BPF. Keep $CC wrapped/hardened for the guest executable.
      ${pkgs.llvmPackages.clang-unwrapped}/bin/clang -target bpf -O2 -g -Wall -Werror \
        -I${pkgs.linuxHeaders}/include -I${pkgs.lib.getDev pkgs.libbpf}/include \
        -c probe.bpf.c -o probe.bpf.o
      $CC -O2 -g -Wall -Wextra -Werror init.c \
        $(pkg-config --cflags --libs libbpf) -o init
      runHook postBuild
    '';
    installPhase = ''
      mkdir -p $out/bin $out/share
      cp init $out/bin/init
      cp probe.bpf.o $out/share/
    '';
    # BPF ELF must retain BTF/CO-RE sections; never feed it to target strip.
    dontStrip = true;
  };
  closure = pkgs.closureInfo { rootPaths = [ probe ]; };
  rootfs = pkgs.runCommand "experimental-attribution-probe-rootfs" {
    nativeBuildInputs = [ pkgs.e2fsprogs ];
  } ''
    mkdir -p root/{dev,proc,sys,nix/store}
    while IFS= read -r path; do cp -a "$path" root/nix/store/; done < ${closure}/store-paths
    cp ${probe}/bin/init root/init
    cp ${probe}/bin/init root/tool
    chmod 0551 root/tool
    cp ${probe}/share/probe.bpf.o root/probe.bpf.o
    # All files are image-owned uid/gid 0, including the execute-only tool.
    mkdir -p $out
    truncate -s 256M $out/rootfs.ext2
    E2FSPROGS_FAKE_TIME=1 mke2fs -q -t ext2 -F -m 0 \
      -U 00000000-0000-0000-0000-000000009042 -E root_owner=0:0 \
      -d root $out/rootfs.ext2
    # mke2fs -d preserves builder ownership; normalize every allocated inode.
    ${pkgs.python3}/bin/python3 ${./normalize-rootfs.py} $out/rootfs.ext2
  '';
in { inherit probe rootfs; }
