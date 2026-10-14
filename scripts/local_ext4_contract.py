"""Read local base markers in-process, without a filesystem utility delegate.

The release/build inspector uses debugfs; the local producer must also work at
the paired Rust producer's no-subprocess boundary. Both adapters use the same
strict marker rule in ext4_contract. The pinned ext4 package validates metadata
checksums and reads the image without mounting or extracting it.
"""

from importlib import metadata
import os
from pathlib import Path
import stat


def read_base_image_contract(image: Path) -> int:
    """Return the contract of an actual regular ext4 artifact, or refuse.

    Parent directories and the marker are inspected as inodes; no guest symlink
    is followed. Read at most three marker bytes, so an oversized declaration
    cannot cause an unbounded read. Filesystem parse errors are never a fallback
    to the checkout's declaration.
    """
    from ext4_contract import validate_base_image_contract_marker

    try:
        if metadata.version("ext4") != "1.4.1":
            raise ValueError("requires pinned ext4==1.4.1")
        from ext4 import Directory, File, Volume
    except (ImportError, metadata.PackageNotFoundError) as error:
        raise ValueError(
            "install scripts/requirements-local-manifest.txt in the local "
            "producer environment; ext4==1.4.1 is required"
        ) from error

    before = image.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{image}: rootfs artifact must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(image, flags)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError(f"{image}: rootfs changed while opening")
        try:
            volume = Volume(stream)
            for path in ("/", "/etc", "/etc/mvm"):
                node = volume.inode_at(path)
                if not isinstance(node, Directory) or not stat.S_ISDIR(node.i_mode):
                    raise ValueError(f"{path}: marker ancestor must be a directory")
            marker = volume.inode_at("/etc/mvm/base-image-contract")
            if not isinstance(marker, File) or marker.i_size != 2:
                raise ValueError("marker must be a regular file containing exactly 1\\n")
            with marker.open() as content:
                contract = validate_base_image_contract_marker(
                    marker.i_mode, content.read(3)
                )
        except Exception as error:
            # The library has distinct errors for corrupt metadata, unsupported
            # features, missing paths and checksum failures. All are refusals.
            raise ValueError(f"{image}: invalid base-image contract: {error}") from error
        after = os.fstat(stream.fileno())
    current = image.lstat()
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
    )
    # Compare metadata within each query family: Windows fstat and lstat can
    # report different timestamp precision for the same unchanged file.
    # The opening device/inode gate above ties the descriptor to the path.
    if identity(opened) != identity(after) or identity(before) != identity(current):
        raise ValueError(f"{image}: rootfs changed during marker inspection")
    return contract
