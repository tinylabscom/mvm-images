"""A fixed real ext4 artifact for cross-platform, in-process producer tests.

Generated with mke2fs 1.47.3: 1024 blocks of 1024 bytes, ext4 without a journal,
128-byte inodes, and a regular 0444 /etc/mvm/base-image-contract containing 1\\n.
This is test data, not a Linux build, boot witness, or release artifact.
"""

import base64
import hashlib
import zlib


def contract_rootfs_bytes() -> bytes:
    encoded = (
        "eNrt3U1oHGUYB/BnZrdqt1urWEu1Ofh18aMh3gwIVUQErQcVRfygpjGK0TSw2eZmSbAUT3op4qnoRXopKPRgD4KKl"
        "ioaBQ+CBK9atVWoFQ/B7cxOFlITE2G1k25+P3gz787MzsO87P8NgX0zEcBaNZX/qBb9byoRjWybLDzhuqJNzb8cPD4"
        "zGtFqPfJr0j6veF3ovG/D/HXvyNrHN0S8mF3/5D0/PDU5fd93r7/x89wXb51++/+5m0uXPdrY8u1thx6+/+jBK489+"
        "fmXWwaSuCvq593Xfy9ZYl8127mrxz5Hff/yvK8G609I3eow8HStWcu212btlnb+G52pID765aETjXhl7p/ee/Vv179"
        "sBAEAAAAAAABgdWi1Wpvir1ZSUvmpFlCaNAthPZK0P9sW/TTt7y++w7c5aulL4xPNW58b37vn2Wh/V+5EpZKONIdNn"
        "HARy7L8/fuTm3Zt+Fv+f6wU+Qd6O/+7Z/84kPd/rxgPWGv57zt1Y0P+Qf4B+QfkH5B/QP4B+QfkH5B/QP6Biy//9Vh"
        "6/c/GrD9bqaRjk2MGC3ow/8/MvfvOxgX5ry/I/+Xt/F+V7B6aGNn+wtjQ8yPbh8f3NBtDw02DBz2Q/0fTez9r7Th/v"
        "xXRsHbkmc///M+f+7Eua5dE8RSdy7K23jQJPe32mjEAAAAAAAAAAAAAAAAAgNVq8PjMaKddqJrpTxGn7o724uPF9av"
        "t/0dUrESOqJ1J2muUO5IonlHejccOljvmD1xTbv2T68qtf/Of5dZ/89WIqemsM1CtLv78JXFFl9fftsLxr/eVe/+z7"
        "5Vbv29nufWP3BnxYT7/DCw1/6SxdZn5pz7fuvH4a/n8d7Z94Q8+nRnttPzY5qisOP9t7bL+g/uXq5+uWL/b6evwRMR"
        "N02eTvM7i+smK9fu6rL/+E7/3AQ"
        + "A" * 1294
        + "WN3OARW5Mt4="
    )
    data = zlib.decompress(base64.b64decode(encoded, validate=True))
    if len(data) != 1048576 or hashlib.sha256(data).hexdigest() != (
        "a8105d791d395a81573d43d49a260814df36d6896df027fcc07a46374d255190"
    ):
        raise AssertionError("fixed ext4 test artifact was altered")
    return data
