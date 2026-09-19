{
  description = "mvm-images — the system images an mvm host boots, built from a pinned mvm commit";

  # ── One place pins everything ─────────────────────────────────────
  #
  # The guest and builder binaries inside these images are compiled from
  # `mvm` source against `mvm`'s Cargo.lock, by recipes that stay in `mvm`.
  # This flake takes that source as the `mvm` input, pinned to one exact
  # commit here and in flake.lock, and nowhere else. Every image, and
  # `scripts/build-host-binaries.sh`, reads the commit from this input.
  # Advancing it is an edit to the URL below plus `nix flake lock`.
  #
  # `mvm` is taken as plain source (`flake = false`), not as a flake, on
  # purpose. As a flake (`github:tinylabscom/mvm/<rev>?dir=nix`) its
  # `mvm-workspace = path:..` input does resolve, but to the whole unfiltered
  # repository, and it brings its own nixpkgs and microvm.nix locks. Every
  # guest binary would then be a different derivation from the one `mvm`'s
  # own image flakes build. Instead each image does exactly what `mvm`'s
  # in-tree image flakes do: filter the source through `mvm`'s
  # `nix/lib/workspace-filter.nix` and call `nix/flake.nix`'s `outputs` with
  # this flake's nixpkgs and microvm.nix. For the same commit the derivations
  # are identical.
  #
  # nixpkgs and microvm.nix are pinned to the revisions `mvm`'s image flakes
  # lock at that commit. The initramfs has its own nixpkgs pin there, so it
  # has its own input here; converging the two changes the initramfs bytes and
  # is a decision of its own, not something to fold into a move.

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    microvm = {
      url = "github:microvm-nix/microvm.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixpkgs-initramfs.url = "github:NixOS/nixpkgs/nixos-25.11";
    mvm = {
      url = "github:tinylabscom/mvm/6717e2451e155672fafc85a1a729094869af8dd8";
      flake = false;
    };
  };

  outputs =
    { self, nixpkgs, microvm, nixpkgs-initramfs, mvm, ... }:
    let
      systems = [ "aarch64-linux" "x86_64-linux" ];

      # `mvm`'s nix/flake.nix swaps its source for `$MVM_WORKSPACE_PATH` when
      # that variable is set, and the builder and default images evaluate
      # impurely. Left unguarded, an ambient variable would build these images
      # from whatever checkout it names while every label still said the
      # pinned commit. Refuse instead: local mvm sources are a separate,
      # explicit workflow.
      pinnedMvm =
        if builtins.getEnv "MVM_WORKSPACE_PATH" != "" then
          throw ''
            MVM_WORKSPACE_PATH is set. mvm-images builds only from the mvm
            commit pinned in flake.nix and flake.lock; unset it.
          ''
        else
          mvm;

      image = file: args: (import file).outputs ({ self = { }; mvm-src = pinnedMvm; } // args);

      builderVm = image ./images/builder-vm/image.nix { inherit nixpkgs microvm; };
      defaultTenant = image ./images/default-tenant/image.nix { inherit nixpkgs microvm; };
      runtimeOverlay = image ./images/runtime-overlay/image.nix { inherit nixpkgs; };
      initramfs = image ./images/initramfs/image.nix { nixpkgs = nixpkgs-initramfs; };

      # The browser (QEMU/WebAssembly) smoke pack, evaluated exactly as `mvm`'s
      # nix/packages/default.nix does: `callPackage` on the pinned nixpkgs.
      qemuWasmFor = system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        rec {
          qemu-wasm-engine = pkgs.callPackage ./qemu-wasm/qemu-wasm.nix { mvm-src = pinnedMvm; };
          qemu-wasm-smoke-image = pkgs.callPackage ./qemu-wasm/qemu-wasm-smoke-image.nix {
            inherit qemu-wasm-engine;
          };
          qemu-wasm-smoke-pack = pkgs.callPackage ./qemu-wasm/qemu-wasm-smoke-pack.nix {
            inherit qemu-wasm-engine qemu-wasm-smoke-image;
          };
        };
    in
    {
      # One attribute set per image role, carrying that role's attributes under
      # the names `mvm`'s in-tree flake used, so `nix/images/<role>#<attr>` in
      # `mvm` is `.#<role>.<attr>` here. The kernel has no `mvm` dependency and
      # stays a flake of its own under `kernel/`.
      legacyPackages = nixpkgs.lib.genAttrs systems (system: {
        builder-vm = builderVm.packages.${system};
        default-tenant = defaultTenant.packages.${system};
        runtime-overlay = runtimeOverlay.packages.${system};
        initramfs = initramfs.packages.${system};
        qemu-wasm = qemuWasmFor system;
      });
    };
}
