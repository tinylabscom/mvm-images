# The builder boot ABI this repository's builder image is built to.
#
# 0 — the image carries mvm's host binaries, baked in from MVM_HOST_BIN_DIR.
# 1 — the host binaries (mvm-host-vm-init, mvm-builderd) arrive at boot in
#     mvmctl's own initramfs payload, and the image contains only what Nix
#     builds, plus the `mvm-setpriv` privilege-drop helper compiled from the
#     pinned mvm source.
# 2 — `mvm-setpriv` travels in that payload too, so the image carries no mvm
#     binary at all. mkGuest is called with `withSetpriv = false`, and the
#     guest runs every mvm binary from /run/mvm/host-bins.
#
# One value, read by everything that has to agree on it: the image itself
# (which writes /etc/mvm/builder-boot-abi), `scripts/assemble-release.py` and
# `scripts/emit-local-manifest.py` (which declare it as
# `compatibility.builder_boot_abi` in the image set). A consumer decides how to
# supply PID 1 from that declaration, so a second copy of this number is a
# builder that boots the wrong way.
#
# The flake stopped reading MVM_HOST_BIN_DIR in the same change this flipped
# to 1: the builder image bakes no mvm host binary, and the pinned mvm
# consumer supplies them at boot from its payload. Publishing an ABI-1 set is
# gated on an mvmctl that supplies the payload being the pinned consumer.
#
# It flipped to 2 with the mvm pin that adds `mvm-setpriv` to the payload
# (tinylabscom/mvm#4107). An mvmctl whose payload range stops at 1 refuses
# an ABI-2 image rather than booting one whose privilege-drop helper it never
# supplies, so publishing an ABI-2 set is gated on an mvmctl that accepts 2
# being the pinned consumer.
2
