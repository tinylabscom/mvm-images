# The builder boot ABI this repository's builder image is built to.
#
# 0 — the image carries mvm's host binaries, baked in from MVM_HOST_BIN_DIR.
# 1 — the host binaries arrive at boot in mvmctl's own initramfs payload, and
#     the image contains only what Nix builds.
#
# One value, read by everything that has to agree on it: the image itself
# (which writes /etc/mvm/builder-boot-abi), `scripts/assemble-release.py` and
# `scripts/emit-local-manifest.py` (which declare it as
# `compatibility.builder_boot_abi` in the image set). A consumer decides how to
# supply PID 1 from that declaration, so a second copy of this number is a
# builder that boots the wrong way.
#
# It flips to 1 in the same change that stops the flake reading
# MVM_HOST_BIN_DIR. Publishing an ABI-1 set is gated on an mvmctl that supplies
# the payload being the pinned consumer.
0
