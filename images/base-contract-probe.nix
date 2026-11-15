{ pkgs }:

# Test-only initramfs. Never included in any published base or runtime.
let
  vsockExchange = pkgs.pkgsStatic.stdenv.mkDerivation {
    pname = "base-contract-vsock-exchange";
    version = "1";
    dontUnpack = true;
    buildPhase = ''
      cat > exchange.c <<'C'
#include <errno.h>
#include <linux/vm_sockets.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static int receive_exact(int fd, char *buffer, size_t count) {
  while (count) {
    ssize_t n = read(fd, buffer, count);
    if (n <= 0) return -1;
    buffer += n;
    count -= (size_t)n;
  }
  return 0;
}

int main(int argc, char **argv) {
  if (argc != 2 || strlen(argv[1]) != 64) return 1;
  for (size_t i = 0; i < 64; ++i)
    if (!((argv[1][i] >= '0' && argv[1][i] <= '9') ||
          (argv[1][i] >= 'a' && argv[1][i] <= 'f'))) return 1;
  struct sockaddr_vm host = {
    .svm_family = AF_VSOCK, .svm_cid = VMADDR_CID_HOST, .svm_port = 40501
  };
  int fd = socket(AF_VSOCK, SOCK_STREAM, 0);
  if (fd < 0) return 1;
  if (connect(fd, (struct sockaddr *)&host, sizeof(host)) != 0) goto fail;
  size_t sent = 0;
  while (sent < 64) {
    ssize_t n = write(fd, argv[1] + sent, 64 - sent);
    if (n <= 0) goto fail;
    sent += (size_t)n;
  }
  char ack[68];
  if (receive_exact(fd, ack, sizeof(ack)) != 0 ||
      memcmp(ack, "ACK:", 4) != 0 ||
      memcmp(ack + 4, argv[1], 64) != 0) goto fail;
  close(fd);
  return 0;
fail:
  close(fd);
  return 1;
}
C
      $CC exchange.c -o exchange
    '';
    installPhase = ''
      mkdir -p "$out/bin"
      cp exchange "$out/bin/base-contract-vsock-exchange"
    '';
  };
in
pkgs.runCommand "base-contract-probe" {
  nativeBuildInputs = [ pkgs.cpio pkgs.gzip ];
} ''
  mkdir -p tree/bin tree/dev tree/proc tree/sys tree/workload "$out"
  cp ${pkgs.pkgsStatic.busybox}/bin/busybox tree/bin/busybox
  cp ${vsockExchange}/bin/base-contract-vsock-exchange tree/bin/
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
mount -t proc proc /proc || refuse
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
nonce=
for arg in $(cat /proc/cmdline); do
  case "$arg" in mvm_images_nonce=*) nonce=''${arg#mvm_images_nonce=} ;; esac
done
/bin/base-contract-vsock-exchange "$nonce" || refuse
echo MVM-IMAGES-BASE-CONTRACT-READY
while :; do sleep 3600; done
PROBE
  chmod 0755 tree/mvm-images-contract-probe
  find tree -exec touch -h -d @1 {} +
  (cd tree; find . -print0 | LC_ALL=C sort -z | cpio --null -o -H newc --reproducible --owner=0:0) \
    | gzip -n > "$out/base-contract-probe-${pkgs.stdenv.hostPlatform.parsed.cpu.name}.cpio.gz"
''
