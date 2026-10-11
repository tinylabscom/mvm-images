#!/usr/bin/env bash
set -euo pipefail

# Kernel symbol budgets belong to this Linux layer in kernel/budgets.json.

arch="${1:?usage: build-kernel-artifacts.sh <aarch64|x86_64>}"
case "$arch" in
  aarch64|x86_64) ;;
  *) echo "unsupported kernel architecture: $arch" >&2; exit 2 ;;
esac

system="${arch}-linux"
kernel_file=vmlinux
[[ "$arch" != aarch64 ]] || kernel_file=Image
mkdir -p staging
for variant in builder workload rootless; do
  if [[ "$variant" == rootless ]]; then
    ref="./kernel#packages.${system}.rootless-vmlinux"
  else
    ref=".#legacyPackages.${system}.builder-vm.${variant}-kernel"
  fi
  store=$(nix build "$ref" --impure --no-link --print-out-paths)
  if [[ ! -f "$store/$kernel_file" ]]; then
    echo "no architecture-matched kernel $kernel_file in $store" >&2
    ls -la "$store" >&2
    exit 1
  fi
  cp "$store/$kernel_file" "staging/vmlinux-${arch}-${variant}"
  if [[ "$variant" == workload ]]; then
    workload_store="$store"
  fi
done

for variant in builder workload rootless; do
  if [[ "$variant" == builder ]]; then
    config_ref=".#legacyPackages.${system}.builder-vm.kernel-configfile"
  elif [[ "$variant" == rootless ]]; then
    config_ref="./kernel#packages.${system}.rootless-configfile"
  else
    config_ref=".#legacyPackages.${system}.builder-vm.workload-kernel-configfile"
  fi
  config=$(nix build "$config_ref" --impure --no-link --print-out-paths)
  cp "$config" "staging/${variant}-config-${arch}"
done

scripts/check_no_network_devices.py \
  --kernel-config "staging/builder-config-${arch}" \
  --kernel-config "staging/workload-config-${arch}" \
  --kernel-config "staging/rootless-config-${arch}"
scripts/check_rootless_tenant.py \
  --kernel-config "staging/rootless-config-${arch}"

config="staging/workload-config-${arch}"
kernel_version=$(basename "$workload_store" | sed -E 's/^linux-//')
config_hash=$(sha256sum "$config" | cut -d' ' -f1)
artifact_hash=$(sha256sum "staging/vmlinux-${arch}-workload" | cut -d' ' -f1)
printf '{"kernel_version":"%s","config_hash":"%s","artifact_hash":"%s"}\n' \
  "$kernel_version" "$config_hash" "$artifact_hash" > "staging/kernel-${arch}.json"

symbol_count=$(grep -c '=y$' "$config")
python3 scripts/check_linux_layer.py --arch "$arch" --kernel-config "$config"
format=elf
[[ "$arch" != aarch64 ]] || format=image
for variant in builder workload rootless; do
  python3 scripts/check_linux_layer.py --arch "$arch" --format "$format" \
    --kernel "staging/vmlinux-${arch}-${variant}"
done

raw_bytes=$(stat -c%s "staging/vmlinux-${arch}-workload")
gzip_bytes=$(gzip -c "staging/vmlinux-${arch}-workload" | wc -c)
printf '{"arch":"%s","y_symbol_count":%d,"vmlinux_bytes":%d,"vmlinux_gz_bytes":%d}\n' \
  "$arch" "$symbol_count" "$raw_bytes" "$gzip_bytes" \
  > "staging/kernel-metrics-${arch}.json"

rootless_config="staging/rootless-config-${arch}"
rootless_symbols=$(grep -c '=y$' "$rootless_config")
rootless_raw=$(stat -c%s "staging/vmlinux-${arch}-rootless")
rootless_gzip=$(gzip -c "staging/vmlinux-${arch}-rootless" | wc -c)
printf '{"arch":"%s","y_symbol_count":%d,"vmlinux_bytes":%d,"vmlinux_gz_bytes":%d}\n' \
  "$arch" "$rootless_symbols" "$rootless_raw" "$rootless_gzip" \
  > "staging/rootless-kernel-metrics-${arch}.json"

(
  cd staging
  sha256sum "vmlinux-${arch}-"* > "kernel-${arch}-checksums-sha256.txt"
  cat "kernel-${arch}-checksums-sha256.txt" \
    "kernel-${arch}.json" \
    "kernel-metrics-${arch}.json"
  du -h "vmlinux-${arch}-"*
)
