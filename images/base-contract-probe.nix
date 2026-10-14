{ pkgs }:

# Test-only initramfs. Never included in any published base or runtime.
pkgs.runCommand "base-contract-probe" {
  nativeBuildInputs = [ pkgs.cpio pkgs.gzip ];
} ''
  mkdir -p tree/bin tree/dev tree/sys tree/workload "$out"
  cp ${pkgs.pkgsStatic.busybox}/bin/busybox tree/bin/busybox
  for applet in mount cat sleep stat cmp; do
    ln -s busybox "tree/bin/$applet"
  done
  cat > tree/mvm-images-contract-probe <<'PROBE'
#!/bin/busybox sh
export PATH=/bin
mount -t devtmpfs devtmpfs /dev
exec </dev/console >/dev/console 2>&1
refuse() {
  echo MVM-IMAGES-BASE-CONTRACT-REFUSED
  while :; do sleep 3600; done
}
mount -t sysfs sysfs /sys || refuse
[ -d /sys/class/net/lo ] || refuse
for interface in /sys/class/net/*; do
  [ "$interface" = /sys/class/net/lo ] || refuse
done
# Observe a bound virtio-vsock device (virtio device ID 19) inside the guest,
# rather than treating a host command-line device as proof of driver support.
vsock=no
for device in /sys/bus/virtio/devices/*; do
  if [ "$(cat "$device/device")" = 0x0013 ] && [ -L "$device/driver" ]; then
    vsock=yes
  fi
done
[ "$vsock" = yes ] || refuse
n=0
while [ ! -b /dev/vda ]; do
  n=$((n + 1))
  [ "$n" -lt 100 ] || refuse
  sleep 0.1
done
mount -t ext4 -o ro,noload /dev/vda /workload || refuse
marker=/workload/etc/mvm/base-image-contract
[ ! -L "$marker" ] && [ -f "$marker" ] || refuse
[ "$(stat -c %a "$marker")" = 444 ] || refuse
printf '1\n' > /expected-contract
cmp -s /expected-contract "$marker" || refuse
echo MVM-IMAGES-BASE-CONTRACT-READY
while :; do sleep 3600; done
PROBE
  chmod 0755 tree/mvm-images-contract-probe
  find tree -exec touch -h -d @1 {} +
  (cd tree; find . -print0 | LC_ALL=C sort -z | cpio --null -o -H newc --reproducible --owner=0:0) \
    | gzip -n > "$out/base-contract-probe-${pkgs.stdenv.hostPlatform.parsed.cpu.name}.cpio.gz"
''
