"""Normalize populated ext2 ownership without root privileges."""
import re
import subprocess
import sys

image = sys.argv[1]
stats = subprocess.check_output(["dumpe2fs", "-h", image], text=True)
count = int(re.search(r"^Inode count:\s+(\d+)$", stats, re.M)[1])
# Setting free inode ownership is harmless; allocated files and directories
# must all be root-owned even when Nix builds as an unprivileged nixbld user.
commands = "".join(
    f"set_inode_field <{ino}> uid 0\nset_inode_field <{ino}> gid 0\n"
    for ino in range(1, count + 1)
)
result = subprocess.run(
    ["debugfs", "-w", "-f", "/dev/stdin", image],
    input=commands, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
if result.returncode or "error" in result.stderr.lower():
    raise SystemExit(result.stderr)
