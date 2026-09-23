#!/usr/bin/env python3
"""Build the switch_root probe initramfs (newc cpio, gzip)."""
import os, sys, gzip

def pad4(b):
    return b + b"\0" * ((4 - (len(b) % 4)) % 4)

def rec(name, data, mode, nlink=1):
    name_b = name.encode() + b"\0"
    fields = [0, mode, 0, 0, nlink, 0, len(data), 3, 0, 0, 0, len(name_b), 0]
    return pad4(b"070701" + b"".join(b"%08x" % f for f in fields) + name_b) + pad4(data)

src, out = sys.argv[1], sys.argv[2]
entries = []
for root, dirs, files in os.walk(src):
    rel = os.path.relpath(root, src)
    for d in dirs:
        p = os.path.normpath(os.path.join(rel, d))
        entries.append((p, b"", 0o40755))
    for f in files:
        p = os.path.normpath(os.path.join(rel, f))
        fp = os.path.join(root, f)
        with open(fp, "rb") as fh:
            entries.append((p, fh.read(), 0o100644))
out_b = b"".join(rec(n, d, m) for n, d, m in entries) + rec("TRAILER!!!", b"", 0)
with open(out, "wb") as fh:
    fh.write(gzip.compress(out_b, 9))
print(f"{out}: {len(out_b)} bytes cpio")
