{
  description = "mvm-images — reproducible Linux kernels and base filesystems";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    microvm = {
      url = "github:microvm-nix/microvm.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };
  outputs = { self, nixpkgs, microvm, ... }:
    let
      systems = [ "aarch64-linux" "x86_64-linux" ];
      image = file: (import file).outputs { inherit self nixpkgs microvm; };
      builderVm = image ./images/builder-vm/image.nix;
      defaultTenant = image ./images/default-tenant/image.nix;
      rootlessTenant = image ./images/rootless-tenant/image.nix;
      qemuWasmFor = system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in rec {
          qemu-wasm-engine = pkgs.callPackage ./qemu-wasm/qemu-wasm.nix { };
          qemu-wasm-smoke-image = pkgs.callPackage ./qemu-wasm/qemu-wasm-smoke-image.nix {
            inherit qemu-wasm-engine;
          };
          qemu-wasm-smoke-pack = pkgs.callPackage ./qemu-wasm/qemu-wasm-smoke-pack.nix {
            inherit qemu-wasm-engine qemu-wasm-smoke-image;
          };
        };
    in {
      legacyPackages = nixpkgs.lib.genAttrs systems (system: {
        builder-vm = builderVm.packages.${system};
        default-tenant = defaultTenant.packages.${system};
        rootless-tenant = rootlessTenant.packages.${system};
        qemu-wasm = qemuWasmFor system;
      });
    };
}
