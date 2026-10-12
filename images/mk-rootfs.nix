# Linux-layer composition only. PID 1 and all guest runtime binaries arrive
# from the authenticated CLI boot payload, never from this filesystem.
{ pkgs }:
{ name, packages ? [ ], extraFiles ? { }, builderUid ? null,
  sealed ? true }:
assert builderUid == null || builderUid == 902;
let
  inherit (pkgs) lib;
  busybox = pkgs.pkgsStatic.busybox;
  roots = [ busybox ] ++ packages;
  closure = pkgs.closureInfo { rootPaths = roots; };
  contract = import ./base-image-contract.nix;
  files = lib.concatStringsSep "\n" (lib.mapAttrsToList (path: value:
    if builtins.isAttrs value then ''
      mkdir -p "$(dirname root${lib.escapeShellArg path})"
      cp ${pkgs.writeText "base-file" value.content} root${lib.escapeShellArg path}
      chmod ${value.mode or "0444"} root${lib.escapeShellArg path}
    '' else ''
      mkdir -p "$(dirname root${lib.escapeShellArg path})"
      ln -sfn ${lib.escapeShellArg value} root${lib.escapeShellArg path}
    '') extraFiles);
in
pkgs.runCommand "${name}-rootfs" {
  nativeBuildInputs = [
    pkgs.e2fsprogs pkgs.coreutils pkgs.findutils pkgs.fakeroot pkgs.libfaketime
  ];
  passthru = {
    rootfsClosureInfo = closure;
    # Retained cache metadata, not a guest-agent protocol declaration.
    mvm = {
      inherit name sealed;
      accessible = !sealed;
      entrypointKind = if sealed then "command" else "shell";
      initSystem = "cli-initramfs";
      # Retain the cache's existing budget, not a newly measured boot time.
      expectedBootMs = 300;
      agentBinary = "";
      rootlessEntrypoint = true;
      hypervisor = "firecracker";
      overlayAware = true;
      runtimeLean = true;
      baseImageContract = contract;
    };
  };
} ''
  mkdir -p root/{bin,sbin,usr/bin,usr/sbin,usr/local/bin,etc/mvm,etc/ssl/certs} \
    root/{dev,proc,sys,run,tmp,var/tmp,nix/store,nix/var/nix,home/mvm-worker,root,mnt,data} \
    root/{nix-store,job,out,work,mvm-bins,closure-seed,mvm/runtime,mvm/sdk}
  while IFS= read -r path; do
    cp -a "$path" root/nix/store/
  done < ${closure}/store-paths
  # Every applet and packaged executable gets a conventional absolute path.
  # Later packages take precedence over busybox (e.g. Nix and util-linux).
  for package in ${lib.concatStringsSep " " (map toString roots)}; do
    for dir in bin sbin; do
      if [ -d "$package/$dir" ]; then
        for exe in "$package/$dir/"*; do
          [ -e "$exe" ] || continue
          for target in bin sbin usr/bin usr/sbin usr/local/bin; do
            ln -sfn "$exe" "root/$target/$(basename "$exe")"
          done
        done
      fi
    done
  done
  cat > root/etc/passwd <<'EOF'
  root:x:0:0:root:/root:/bin/sh
  mvm-worker:x:901:901:workload:/home/mvm-worker:/bin/sh
  mvm-dns:x:987:987:dns:/nonexistent:/bin/false
  mvm-crng:x:988:988:crng:/nonexistent:/bin/false
  mvm-egress:x:989:989:egress:/nonexistent:/bin/false
  ${lib.optionalString (builderUid != null) "mvm-builder:x:${toString builderUid}:${toString builderUid}:builder:/home/mvm-builder:/bin/sh"}
  EOF
  cat > root/etc/group <<'EOF'
  root:x:0:
  mvm-worker:x:901:
  mvm-dns:x:987:
  mvm-crng:x:988:
  mvm-egress:x:989:
  ${lib.optionalString (builderUid != null) "mvm-builder:x:${toString builderUid}:"}
  EOF
  printf '${toString contract}\n' > root/etc/mvm/base-image-contract
  chmod 0444 root/etc/mvm/base-image-contract
  # TLS trust is Linux-layer data, not a runtime binary. Copy the bundle
  # rather than retaining an otherwise unused cacert store path.
  cp ${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt root/etc/ssl/certs/ca-bundle.crt
  ln -s /etc/ssl/certs/ca-bundle.crt root/etc/ssl/certs/ca-certificates.crt
  chmod 0644 root/etc/ssl/certs/ca-bundle.crt
  # A real file is required as the CLI's read-only-root bind mount target.
  # Only the injected loopback DNS adapter can resolve guest requests.
  printf 'nameserver 127.0.0.1\n' > root/etc/resolv.conf
  chmod 0644 root/etc/resolv.conf
  chmod 1777 root/tmp root/var/tmp
  # The CLI's builder init loads this seed into its writable store database.
  # This is nixpkgs' ext4 image convention, not a runtime implementation.
  cp ${closure}/registration root/nix-path-registration
  chmod 0444 root/nix-path-registration
  ${lib.optionalString (builderUid != null) "mkdir -p root/home/mvm-builder"}
  ${files}
  # mke2fs imports uid/gid from the staging tree. fakeroot supplies fixed
  # numeric ownership without requiring host root privileges.
  test ! -e root/init
  test ! -e root/sbin/mvm-setpriv
  find root -exec touch -h -d @1 {} +
  bytes=$(du -sb root | cut -f1)
  size=$(( (bytes * 2 / 1048576 + 64) * 1048576 ))
  truncate -s "$size" "$out"
  export E2FSPROGS_FAKE_TIME=1
  faketime -f '1970-01-01 00:00:01' fakeroot -- bash -eu <<'IMAGE'
    chown -hR 0:0 root
    chown 901:901 root/home/mvm-worker
    ${lib.optionalString (builderUid != null) "chown ${toString builderUid}:${toString builderUid} root/home/mvm-builder"}
    mkfs.ext4 -q -F -d root -L mvm-base \
      -U 00000000-0000-0000-0000-000000000001 \
      -E lazy_itable_init=0,lazy_journal_init=0,hash_seed=00000000-0000-0000-0000-000000000002 \
      "$out"
  IMAGE
  fsck.ext4 -n -f "$out"
''
