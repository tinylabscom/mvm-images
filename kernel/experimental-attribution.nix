# Development only. Deliberately wrap the workload derivation without changing
# any production recipe. See experimental-attribution.md for the 6.12 dependency
# audit and the additional attack surface (including the mandatory BPF LSM JIT).
{ pkgs, base }:
let
  lib = pkgs.lib;
  contract = builtins.fromJSON (builtins.readFile ./experimental-attribution.json);
  workload = import ./workload.nix { inherit pkgs base; };
  words = lib.splitString " ";
  join = lib.concatStringsSep " ";
  without = removed: value: lib.filter (s: !(builtins.elem s removed)) (words value);
  configfile = workload.passthru.configfile.overrideAttrs (old: {
    name = "mvm-experimental-attribution-config";
    nativeBuildInputs = old.nativeBuildInputs ++ [
      pkgs.buildPackages.pahole
      pkgs.buildPackages.python3
    ];
    enableList = join (lib.unique ((words old.enableList) ++ contract.enables));
    disableList = join ((without contract.enables old.disableList) ++ contract.disables);
    requiredDisableList = join (
      (without contract.enables old.requiredDisableList) ++ contract.disables
    );
    # LSM membership must be resolved by Kconfig, not patched into its output.
    buildCommand = builtins.replaceStrings
      [ ''make SHELL="$SHELL" olddefconfig'' ]
      [ ''
        ./scripts/config --set-str LSM ${lib.escapeShellArg contract.lsm}
        make SHELL="$SHELL" olddefconfig
      '' ]
      old.buildCommand + ''
        python3 ${./check-experimental-attribution.py} \
          --contract ${./experimental-attribution.json} "$out"
      '';
  });
in
# Reuse the pinned source, toolchain and architecture-specific image installation,
# including x86's extracted ELF. No dependency on the mvm source input.
workload.override { inherit configfile; }
