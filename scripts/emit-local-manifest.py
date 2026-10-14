#!/usr/bin/env python3
"""Describe a locally built image set in mvm's image-set manifest schema.

Usage:

  PURE=1 python3 -m pip install -r scripts/requirements-local-manifest.txt

  scripts/emit-local-manifest.py --arch <aarch64|x86_64> \\
      --builder-cache-contract <n> --out <new dir> \\
      --artifact <role> <format> <path> [--artifact ...] \\
      [--capability <role> <capability> ...] [--images-checkout <dir>]

Locally composed Linux images come from one working tree, not a release.
This records the commit and working-tree state of the mvm-images checkout
(by default the one this script is in), next to each artifact's digest and size,
the guest architecture and the role each artifact plays.

The output is a directory holding a copy of each artifact under its manifest
name and `image-set.json`, in the schema `mvm_core::image_set` parses for a
released set. Only the producer differs, and it is what keeps the two apart:
a release names the repository, workflow and tag that signed it, while this
names only `local_checkouts`. It carries no repository, workflow, tag,
revocation channel, pack hash or SBOM, so nothing in it can be read as a claim
to be a release, and mvm classifies it `local-dev`.

A tree that is not clean is fingerprinted the way mvm fingerprints it — the
version-2 canonical HEAD/index/working-file encoding — so mvm can
tell whether the checkout still matches the manifest when it reads it. Its
identity is read before the artifacts are copied and again after, and a
change in between is refused rather than attributed to either reading.

Nothing is built here, and nothing is discovered: the checkout and every
artifact are named by the caller.
"""

from __future__ import annotations

import argparse
import codecs
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path

from dulwich.attrs import GitAttributes, Pattern, parse_git_attributes
from dulwich.config import ConfigFile, StackedConfig
from dulwich.errors import NotGitRepository
from dulwich.ignore import IgnoreFilter, match_pattern
from dulwich.index import ConflictedIndexEntry, IndexEntry, read_cache_entry, read_index_header
from dulwich.object_store import iter_tree_contents, peel_sha
from dulwich.objects import Blob, Commit
from dulwich.repo import Repo

from local_ext4_contract import read_base_image_contract

SCHEMA_VERSION = 3
# Semver, as the schema requires, and never a version any release is tagged.
LOCAL_SET_VERSION = "0.0.0-local"
MANIFEST_NAME = "image-set.json"

ARCHES = ("x86_64", "aarch64")

# name -> (schema role, boots directly, published once for every arch)
ROLES = {
    "builder_vm": ("builder_vm", True, False),
    "default_tenant_workload_kernel": (
        {"workload_kernel": "default_tenant"},
        True,
        False,
    ),
    "default_tenant_workload_rootfs": (
        {"workload_rootfs": "default_tenant"},
        False,
        False,
    ),
    "rootless_tenant_workload_kernel": (
        {"workload_kernel": "rootless_tenant"},
        True,
        False,
    ),
    "rootless_tenant_workload_rootfs": (
        {"workload_rootfs": "rootless_tenant"},
        False,
        False,
    ),
    "stage0_bootstrap_kernel": ("stage0_bootstrap_kernel", True, False),
    "qemu_wasm_smoke_pack": ("qemu_wasm_smoke_pack", False, True),
}

KERNEL_FORMATS = ("raw", "elf", "image", "image_gz", "image_bz2", "image_zstd", "pe", "pe_gz")
PLAIN_FORMATS = ("ext4", "verity_hash_tree", "verity_root_hash", "tar_gz", "text", "json")
CAPABILITIES = ("virtio_vsock", "virtio_blk", "dm_verity")

# Files every mvm-images checkout carries, as mvm checks them when one is
# selected.
IMAGES_MARKERS = (
    "flake.nix",
    "flake.lock",
    "kernel/flake.nix",
    "images/builder-vm/image.nix",
    "images/default-tenant/image.nix",
    "images/rootless-tenant/image.nix",
    "images/base-image-contract.nix",
)
FLAKE_LOCKS = ("flake.lock", "kernel/flake.lock")

ARTIFACT_NAME = re.compile(r"[A-Za-z0-9._-]{1,255}")


class Refusal(Exception):
    """A reason not to emit a manifest, for the operator."""


def _field(hasher, body: bytes) -> None:
    hasher.update(len(body).to_bytes(8, "little"))
    hasher.update(body)


def fingerprint_v2(head: bytes, index: list, working: list, untracked: list) -> str:
    """Canonical binary encoding; lists have already been sorted by raw path."""
    h = hashlib.sha256(b"mvm-checkout-v2\0" + head)
    h.update(b"I")
    h.update(len(index).to_bytes(8, "little"))
    for path, mode, stage, flags, oid in index:
        _field(h, path)
        for value in (mode, stage, flags):
            h.update(value.to_bytes(4, "little"))
        h.update(oid)
    for tag, files in ((b"W", working), (b"U", untracked)):
        h.update(tag)
        h.update(len(files).to_bytes(8, "little"))
        for path, mode, body in files:
            _field(h, path)
            h.update(mode.to_bytes(4, "little"))
            _field(h, body)
    return h.hexdigest()


def _read_optional(path) -> bytes | None:
    try:
        with open(path, "rb") as source:
            return source.read()
    except FileNotFoundError:
        return None


def _config(config, key: bytes, default=None):
    try:
        return config.get((b"core",), key)
    except KeyError:
        return default


def _git_boolean(value: bytes | None, label: str) -> bool:
    """Git's textual booleans or bounded base-0 integers with k/m/g units."""
    if value is None:
        return True  # A declared variable without a value.
    lowered = value.lower()
    if lowered in (b"true", b"yes", b"on"):
        return True
    if lowered in (b"", b"false", b"no", b"off"):
        return False
    number = re.fullmatch(
        rb"[\t\n\v\f\r ]*([+-]?)(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)([kKmMgG]?)",
        value,
    )
    if number is not None:
        digits = number[2]
        base = 16 if digits.lower().startswith(b"0x") else (8 if digits.startswith(b"0") else 10)
        integer = int(digits, base)
        if number[1] == b"-":
            integer = -integer
        integer *= {b"": 1, b"k": 1024, b"m": 1024 ** 2, b"g": 1024 ** 3}[number[3].lower()]
        # git_parse_maybe_bool uses git_parse_int, not an unbounded integer.
        if -(2 ** 31 - 1) <= integer <= 2 ** 31 - 1:
            return integer != 0
    raise Refusal(f"{label} is not a valid Git boolean: {value!r}")


def _config_boolean(config, section: tuple[bytes, ...], key: bytes, default: bool) -> bool:
    try:
        value = config.get(section, key)
    except KeyError:
        return default
    return _git_boolean(value, b".".join(section + (key,)).decode("ascii"))


def _autocrlf(config) -> bool:
    value = _config(config, b"autocrlf", b"false")
    return (isinstance(value, bytes) and value.lower() == b"input") or _git_boolean(
        value, "core.autocrlf"
    )


def _env_boolean(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return _git_boolean(os.fsencode(value), name)


def _windows_prefix_from_path(directories) -> Path | None:
    for directory in directories:
        executable = Path(directory) / "git.exe"
        try:
            mode = executable.stat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise Refusal(f"cannot inspect {executable}: {exc}; name GIT_CONFIG_SYSTEM explicitly") from exc
        if not stat.S_ISREG(mode):
            raise Refusal(f"{executable} is not a regular executable data path")
        executable = executable.resolve()
        if executable.parent.name.lower() not in ("cmd", "bin"):
            raise Refusal(f"cannot infer installed/portable Git prefix from {executable}; set GIT_CONFIG_SYSTEM")
        prefix = executable.parent.parent
        if prefix.name.lower() in ("mingw32", "mingw64", "usr"):
            prefix = prefix.parent
        if not (prefix / "etc").is_dir():
            raise Refusal(f"cannot inspect Git prefix {prefix}; set GIT_CONFIG_SYSTEM explicitly")
        return prefix
    return None


def _windows_prefix() -> Path | None:
    prefix = _windows_prefix_from_path(os.environ.get("PATH", "").split(os.pathsep))
    if prefix is not None:
        return prefix
    roots = set()
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            root = Path(base) / ("Programs/Git" if variable == "LOCALAPPDATA" else "Git")
            try:
                mode = (root / "etc").stat().st_mode
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise Refusal(f"cannot inspect Git installation {root}: {exc}; set GIT_CONFIG_SYSTEM") from exc
            if not stat.S_ISDIR(mode):
                raise Refusal("installed Git etc path is not a directory")
            roots.add(root.resolve())
    if len(roots) > 1:
        raise Refusal("ambiguous Windows Git installations; set GIT_CONFIG_SYSTEM to the intended config file")
    return next(iter(roots), None)


@dataclass(frozen=True)
class GitSystemContext:
    """Resolved V2 policy: no host system rules unless explicitly selected."""

    config: Path | None
    attributes: Path | None
    prefix: Path | None


def _system_context() -> GitSystemContext:
    explicit = os.environ.get("GIT_CONFIG_SYSTEM")
    config_disabled = _env_boolean("GIT_CONFIG_NOSYSTEM", default=explicit is None)
    attributes_disabled = _env_boolean("GIT_ATTR_NOSYSTEM", default=True)
    # A proven Windows data layout can resolve prefix-dependent opt-ins.
    # POSIX executable paths cannot prove compiled prefix/sysconfdir.
    needs_prefix = (not config_disabled and explicit is None) or not attributes_disabled
    prefix = _windows_prefix() if os.name == "nt" and needs_prefix else None
    if not config_disabled and explicit is None and prefix is None:
        raise Refusal("unresolved Git system configuration prefix; name GIT_CONFIG_SYSTEM "
                      "explicitly or use the V2 default with GIT_CONFIG_NOSYSTEM unset")
    if not attributes_disabled and prefix is None:
        raise Refusal("unresolved Git system attributes prefix; use the V2 default with "
                      "GIT_ATTR_NOSYSTEM unset and name attribute rules with absolute data paths")
    path = None
    if config_disabled:
        explicit = None
    if explicit is not None:
        if not explicit:
            raise Refusal("GIT_CONFIG_SYSTEM is empty; name an accessible file or set GIT_CONFIG_NOSYSTEM=1")
        path = Path(explicit)
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            raise Refusal(f"reading Git config {path}: {exc}; set GIT_CONFIG_SYSTEM or GIT_CONFIG_NOSYSTEM=1") from exc
        if stat.S_ISDIR(mode):
            raise Refusal(f"Git config {path} is a directory")
    elif not config_disabled:
        path = prefix / "etc" / "gitconfig"
    attributes = None if attributes_disabled else prefix / "etc" / "gitattributes"
    return GitSystemContext(path, attributes, prefix)


def _system_config() -> tuple[Path | None, Path | None]:
    context = _system_context()
    return context.config, context.prefix


def _context_with_prefix(config, context: GitSystemContext) -> GitSystemContext:
    """Resolve Windows installation data only for a contributing prefix path."""
    needs_prefix = any(
        isinstance(value := _config(config, key), bytes) and value.startswith(b"%(prefix)")
        for key in (b"worktree", b"excludesfile", b"attributesfile")
    )
    if context.prefix is None and needs_prefix and os.name == "nt":
        return replace(context, prefix=_windows_prefix())
    return context


def _matches_gitdir(repo, pattern: str, config_dir: str | None, ignorecase: bool) -> bool:
    """Match both lexical and resolved git-dir paths, retaining filesystem bytes."""
    pattern = os.path.expanduser(pattern)
    if pattern.startswith("./"):
        if config_dir is None:
            raise Refusal("relative gitdir condition has no config directory")
        pattern = os.path.join(config_dir, pattern[2:])
    if os.name == "nt":
        pattern = pattern.replace("\\", "/")
    if not pattern.startswith(("/", "**")) and not (
        len(pattern) >= 2 and pattern[1] == ":"
    ):
        pattern = "**/" + pattern
    if pattern.endswith("/"):
        pattern += "**"
    lexical = os.path.abspath(repo.controldir())
    resolved = os.path.realpath(repo.controldir())
    for candidate in (lexical, resolved):
        if os.name == "nt":
            candidate = candidate.replace("\\", "/")
        if match_pattern(os.fsencode(candidate).lstrip(b"/"),
                         os.fsencode(pattern).lstrip(b"/"), ignorecase=ignorecase):
            return True
    return False


def _strict_config(path: Path, repo, prefix: Path | None):
    """Keep library config parsing but make includes lossless and fail closed."""
    active = {path.resolve()}

    class DataConfig(ConfigFile):
        def _process_include(self, section, path_value, *, config_dir, include_depth,
                             max_include_depth, file_opener, condition_matchers):
            include = os.fsdecode(path_value)
            if include.startswith("%(prefix)"):
                raise Refusal("unsupported %(prefix) include; name an absolute include data path")
            if len(section) > 1 and section[0].lower() == b"includeif":
                condition = os.fsdecode(section[1])
                if "%(prefix)" in condition:
                    raise Refusal("unsupported %(prefix) include; name an absolute include data path")
                if condition.startswith("hasconfig:"):
                    raise Refusal("unsupported hasconfig conditional include; use supported gitdir/onbranch or explicit data paths")
                if condition.startswith("gitdir/i:"):
                    matches = _matches_gitdir(repo, condition[len("gitdir/i:"):], config_dir, True)
                elif condition.startswith("gitdir:"):
                    matches = _matches_gitdir(repo, condition[len("gitdir:"):], config_dir, False)
                elif condition.startswith("onbranch:"):
                    matches = self._evaluate_includeif_condition(condition, config_dir, condition_matchers)
                else:
                    raise Refusal(f"unsupported conditional config include {condition!r}")
                if not matches:
                    return
            include = self._resolve_include_path(include, config_dir)
            canonical = Path(include).resolve()
            if canonical in active or include_depth >= max_include_depth:
                raise Refusal("recursive config include or maximum depth exceeded")
            active.add(canonical)
            try:
                try:
                    source = open(canonical, "rb")
                except FileNotFoundError:
                    return
                except OSError as exc:
                    raise Refusal(f"cannot read configured include {canonical}: {exc}") from exc
                with source:
                    child = type(self).from_file(
                        source, config_dir=str(canonical.parent), include_depth=include_depth + 1,
                        max_include_depth=max_include_depth, condition_matchers=condition_matchers)
                self._merge_config(child)
            finally:
                active.remove(canonical)

    return DataConfig.from_path(str(path), max_include_depth=10,
                                condition_matchers=repo._get_config_condition_matchers())


def _config_stack(repo, context: GitSystemContext | None = None):
    if os.environ.get("GIT_CONFIG_PARAMETERS") or os.environ.get("GIT_CONFIG_COUNT", "0") != "0":
        raise Refusal("unsupported command-scope Git config; use explicit config data files")
    context = context if context is not None else _system_context()
    system, prefix = context.config, context.prefix
    try:
        local = _strict_config(Path(repo.commondir()) / "config", repo, prefix)
    except FileNotFoundError:
        local = ConfigFile()
    backends = []
    if _config_boolean(local, (b"extensions",), b"worktreeconfig", False):
        worktree = Path(repo.controldir()) / "config.worktree"
        try:
            backends.append(_strict_config(worktree, repo, prefix))
        except FileNotFoundError:
            pass
    backends.append(local)
    home = _declared_home()
    xdg_value = os.environ.get("XDG_CONFIG_HOME")
    xdg = Path(xdg_value) if xdg_value else (None if home is None else home / ".config")
    global_override = os.environ.get("GIT_CONFIG_GLOBAL")
    globals = ([global_override] if global_override is not None else
               ([] if home is None else [home / ".gitconfig"])
               + ([] if xdg is None else [xdg / "git" / "config"]))
    for path in globals:
        try:
            backends.append(_strict_config(Path(path), repo, prefix))
        except FileNotFoundError:
            if global_override is not None:
                raise Refusal(f"cannot read GIT_CONFIG_GLOBAL {path}")
            continue
    if system is not None:
        try:
            backends.append(_strict_config(system, repo, prefix))
        except FileNotFoundError:
            pass
    return StackedConfig(backends)


def _bitmap(data: bytes, offset: int, limit: int) -> tuple[set[int], int]:
    """Decode a bounded EWAH bitmap from a split-index link extension."""
    def integer(size):
        nonlocal offset
        value = data[offset:offset + size]
        if len(value) != size:
            raise Refusal("truncated split-index bitmap")
        offset += size
        return int.from_bytes(value, "big")

    bit_count, word_count = integer(4), integer(4)
    if word_count > (len(data) - offset - 4) // 8:
        raise Refusal("truncated split-index bitmap words")
    words = [integer(8) for _ in range(word_count)]
    rlw = integer(4)
    if word_count and rlw >= word_count:
        raise Refusal("invalid split-index bitmap cursor")
    result = set()
    pos = cursor = 0
    while cursor < len(words):
        word = words[cursor]
        cursor += 1
        repeat = (word >> 1) & 0xffffffff
        literals = word >> 33
        if literals > len(words) - cursor:
            raise Refusal("invalid split-index literal count")
        end = pos + repeat * 64
        if word & 1:
            if min(end, bit_count) > limit:
                raise Refusal("split-index bitmap exceeds shared index")
            result.update(range(pos, min(end, bit_count)))
        pos = end
        for value in words[cursor:cursor + literals]:
            while value:
                bit = (value & -value).bit_length() - 1
                index = pos + bit
                if index >= bit_count or index >= limit:
                    raise Refusal("split-index bitmap exceeds shared index")
                result.add(index)
                value &= value - 1
            pos += 64
        cursor += literals
    return result, offset


def _index_entries(path: str, active: set | None = None, expected: bytes | None = None) -> list:
    """Dulwich decodes records; this adapter hydrates its unsupported link extension."""
    active = set() if active is None else active
    path = os.path.realpath(path)
    if path in active or len(active) >= 64:
        raise Refusal("recursive split index")
    active.add(path)
    try:
        body = _read_optional(path)
        if body is None:
            if expected is not None:
                raise Refusal("missing shared index")
            return []
        if len(body) < 32:
            raise Refusal("truncated index")
        checksum = body[-20:]
        actual = hashlib.sha1(body[:-20]).digest()
        if (checksum != bytes(20) and checksum != actual) or (
            expected is not None and (actual != expected or checksum != expected)
        ):
            raise Refusal("index checksum mismatch")
        stream = BytesIO(body[:-20])
        version, count = read_index_header(stream)
        records = []
        previous = b""
        for _ in range(count):
            entry = read_cache_entry(stream, version, previous)
            previous = entry.name
            records.append((entry.name, IndexEntry.from_serialized(entry)))
        link = None
        sparse_directory = False
        while stream.tell() < len(body) - 20:
            header = stream.read(8)
            if len(header) != 8:
                raise Refusal("truncated index extension")
            signature, size = header[:4], int.from_bytes(header[4:], "big")
            data = stream.read(size)
            if len(data) != size:
                raise Refusal("truncated index extension body")
            if signature == b"link":
                if link is not None:
                    raise Refusal("duplicate split-index extension")
                link = data
            elif signature == b"sdir":
                if sparse_directory or data:
                    raise Refusal("invalid or duplicate sparse-directory extension")
                sparse_directory = True
            elif signature[:1].islower():
                raise Refusal(f"unsupported mandatory index extension {signature!r}")
        if link is None:
            return records
        if len(link) < 20:
            raise Refusal("truncated split-index link")
        shared = _index_entries(os.path.join(os.path.dirname(path), "sharedindex." + link[:20].hex()),
                                active, link[:20])
        if len(link) == 20:
            return records
        deleted, offset = _bitmap(link, 20, len(shared))
        replaced, offset = _bitmap(link, offset, len(shared))
        if offset != len(link) or deleted & replaced or len(replaced) > len(records):
            raise Refusal("invalid split-index replacement/deletion map")
        for cursor, index in enumerate(sorted(replaced)):
            name, entry = records[cursor]
            if name or not shared[index][0]:
                raise Refusal("invalid split-index replacement path")
            shared[index] = (shared[index][0], entry)
        shared = [entry for index, entry in enumerate(shared) if index not in deleted]
        shared.extend(records[len(replaced):])
        shared.sort(key=lambda e: (e[0], e[1].stage().value))
        return shared
    finally:
        active.remove(path)


def _file(root: bytes, name: bytes) -> tuple:
    _safe_path(name)
    ancestor = root
    for part in name.split(b"/")[:-1]:
        ancestor = os.path.join(ancestor, part)
        try:
            mode = os.lstat(ancestor).st_mode
        except FileNotFoundError:
            return name, 0, b""
        if not stat.S_ISDIR(mode):
            return name, 0, b""
    path = os.path.join(root, name)
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return name, 0, b""
    if stat.S_ISLNK(mode):
        return name, 0o120000, os.readlink(path)
    if stat.S_ISREG(mode):
        with open(path, "rb") as source:
            return name, 0o100755 if os.name == "posix" and mode & 0o100 else 0o100644, source.read()
    if stat.S_ISDIR(mode):
        return name, 0o040000, b""
    raise Refusal(f"unsupported special file {os.fsdecode(path)}")


def _safe_path(path: bytes) -> None:
    if b"\0" in path or any(p in (b"", b".", b"..") or p.lower() == b".git"
                           for p in path.split(b"/")):
        raise Refusal("unsafe repository path")
    if os.name == "nt" and (b"\\" in path or b":" in path):
        raise Refusal("unsafe Windows repository path")


def _worktree_root(repo, config, context: GitSystemContext | None = None) -> Path:
    if _config_boolean(config, (b"core",), b"bare", repo.bare):
        raise Refusal("checkout has no working tree")
    value = _config(config, b"worktree")
    if value is None:
        return Path(repo.path)
    path = Path(os.path.expanduser(os.fsdecode(value)))
    if os.fsdecode(value).startswith("%(prefix)"):
        context = (context if context is not None else
                   _context_with_prefix(config, _system_context()))
        prefix = context.prefix
        if prefix is None:
            raise Refusal("configured path needs a Git installation prefix; name an absolute data path")
        path = prefix / os.fsdecode(value)[len("%(prefix)"):].lstrip("/")
    return path if path.is_absolute() else Path(repo.controldir()) / path


def _declared_home() -> Path | None:
    value = os.environ.get("HOME")
    if value is None:
        value = os.environ.get("USERPROFILE")
    return None if value is None else Path(value)


def _rule_path(value: bytes | None, basename: str, root: bytes, prefix: Path | None) -> Path:
    """Resolve rule resources without ambient cwd or native/pwd home discovery."""
    if value is None:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        home = _declared_home()
        base = Path(xdg) if xdg else (None if home is None else home / ".config")
        path = Path(os.devnull) if base is None else base / "git" / basename
    elif value.startswith(b"%(prefix)"):
        if prefix is None:
            raise Refusal("configured path needs a Git installation prefix; name an absolute data path")
        path = prefix / os.fsdecode(value[len(b"%(prefix)"):].lstrip(b"/"))
    elif value.startswith(b"~/"):
        home = _declared_home()
        if home is None:
            raise Refusal("HOME is required for configured ~/ data path")
        path = home / os.fsdecode(value[2:])
    else:
        path = Path(os.fsdecode(value))
    if path.is_absolute() or (os.name == "nt" and str(path).upper() == "NUL"):
        return path
    return Path(os.fsdecode(root)) / path


class RepositoryRules:
    """Library-parsed ignore/attribute rules, with worktree/index precedence."""

    def __init__(self, repo, config, indexed, root: bytes,
                 context: GitSystemContext | None = None):
        self.repo, self.config, self.indexed = repo, config, indexed
        self.root = root
        # Validate contributing booleans even for empty/unchanged checkouts;
        # malformed configuration must never short-circuit to a clean result.
        self.casefold = _config_boolean(config, (b"core",), b"ignorecase", False)
        self.filemode = _config_boolean(config, (b"core",), b"filemode", True)
        self.symlinks = _config_boolean(config, (b"core",), b"symlinks", True)
        self.autocrlf = _autocrlf(config)
        self.cache = {}
        self.global_ignore = []
        context = (context if context is not None else
                   _context_with_prefix(config, _system_context()))
        prefix = context.prefix
        excludes = _rule_path(_config(config, b"excludesfile"), "ignore", root, prefix)
        for path in (
            excludes,
            os.path.join(repo.commondir(), "info", "exclude"),
        ):
            body = _read_optional(path)
            if body is not None:
                self.global_ignore.append(
                    IgnoreFilter(body.splitlines(), ignorecase=self.casefold)
                )
        attrs = _rule_path(_config(config, b"attributesfile"), "attributes", root, prefix)
        self.global_attrs = _read_optional(attrs)
        self.system_attrs = (_read_optional(context.attributes)
                             if context.attributes is not None else None)
        self.info_attrs = _read_optional(os.path.join(repo.commondir(), "info", "attributes"))

    def body(self, path: bytes):
        if path not in self.cache:
            _, mode, body = _file(self.root, path)
            if mode not in (0, 0o100644, 0o100755):
                raise Refusal("unsupported non-regular ignore/attribute resource")
            if mode == 0:
                body = None
            if body is None and path in self.indexed and not path.endswith(b".gitignore"):
                entry = self.indexed[path]
                body = self.repo.object_store[entry[4].hex().encode()].data
            self.cache[path] = body
        return self.cache[path]

    def ignored(self, path: bytes, directory: bool) -> bool:
        query = path + (b"/" if directory else b"")
        matched = None
        for rule in self.global_ignore:
            value = rule.is_ignored(query)
            if value is not None:
                matched = value
        parts = path.split(b"/")
        for depth in range(len(parts)):
            prefix = b"/".join(parts[:depth])
            body = self.body((prefix + b"/" if prefix else b"") + b".gitignore")
            if body is not None:
                rule = IgnoreFilter(body.splitlines(), ignorecase=self.casefold)
                value = rule.is_ignored(b"/".join(parts[depth:]) + (b"/" if directory else b""))
                if value is not None:
                    matched = value
        return bool(matched)

    def attributes(self, path: bytes) -> dict:
        attrs = {}
        macros = {b"binary": {b"text": False, b"diff": False, b"merge": False}}

        def apply(body, relative):
            if body is None:
                return
            patterns = []
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith(b"#"):
                    continue
                if line.startswith(b'"'):
                    end = 1
                    while end < len(line):
                        if line[end] == 92:
                            end += 2
                        elif line[end] == 34:
                            break
                        else:
                            end += 1
                    if end >= len(line):
                        raise Refusal("unterminated quoted attribute pattern")
                    pattern = codecs.escape_decode(line[1:end])[0]
                    _, values = next(parse_git_attributes(BytesIO(b"p " + line[end + 1:])))
                else:
                    pattern, values = next(parse_git_attributes(BytesIO(line)))
                if pattern.startswith(b"[attr]"):
                    macros[pattern[6:]] = values
                else:
                    patterns.append((Pattern(pattern.lower() if self.casefold else pattern), values))
            values = GitAttributes(patterns).match_path(relative.lower() if self.casefold else relative)
            pending = list(values.items())
            seen = set()
            while pending:
                name, value = pending.pop(0)
                attrs[name] = value
                if value is True and name in macros and name not in seen:
                    seen.add(name)
                    pending[0:0] = list(macros[name].items())

        apply(self.system_attrs, path)
        apply(self.global_attrs, path)
        parts = path.split(b"/")
        for depth in range(len(parts)):
            prefix = b"/".join(parts[:depth])
            apply(self.body((prefix + b"/" if prefix else b"") + b".gitattributes"),
                  b"/".join(parts[depth:]))
        apply(self.info_attrs, path)
        return attrs


def _text_stats(body: bytes) -> tuple[bool, int]:
    printable = nonprintable = crlf = 0
    lone_cr = null = False
    i = 0
    while i < len(body):
        value = body[i]
        i += 1
        if value == 13:
            if i < len(body) and body[i] == 10:
                crlf += 1
                i += 1
            else:
                lone_cr = True
        elif value == 10:
            continue
        elif value == 127 or (value < 32 and value not in (8, 9, 27, 12)):
            nonprintable += 1
            null |= value == 0
        else:
            printable += 1
    return lone_cr or null or (printable >> 7) < nonprintable, crlf


def _normalize(body: bytes, mode: int, indexed: bytes, attrs: dict, autocrlf: bool) -> bytes:
    for name in (b"filter", b"working-tree-encoding"):
        value = attrs.get(name)
        if value is True or isinstance(value, bytes):
            raise Refusal("unsupported external filter or working-tree-encoding")
    if mode not in (0o100644, 0o100755):
        return body
    text = attrs.get(b"text")
    auto = text == b"auto"
    if text == b"input":
        text = True
    if text not in (True, False) and not auto:
        legacy = attrs.get(b"crlf")
        if legacy in (True, False, b"input"):
            text = True if legacy == b"input" else legacy
    eol = attrs.get(b"eol") in (b"lf", b"crlf")
    automatic = text is not True and text is not False and (auto or autocrlf)
    binary, _ = _text_stats(body)
    indexed_binary, indexed_crlf = _text_stats(indexed)
    convert = text is True or (text is not False and eol and not auto) or (
        automatic and not binary and (indexed_binary or not indexed_crlf)
    )
    if convert:
        body = body.replace(b"\r\n", b"\n")
    if attrs.get(b"ident") is True or isinstance(attrs.get(b"ident"), bytes):
        body = re.sub(rb"\$Id:[^\n$]*\$", b"$Id$", body)
    return body


def _observe(root: Path, active: set) -> dict:
    canonical = root.resolve()
    if canonical in active or len(active) >= 64:
        raise Refusal("recursive submodule cycle or maximum depth exceeded")
    active.add(canonical)
    try:
        with Repo(str(canonical)) as repo:
            if repo.bare:
                raise Refusal(f"{root}: checkout has no working tree")
            context = _system_context()
            config = _config_stack(repo, context)
            context = _context_with_prefix(config, context)
            working_root = _worktree_root(repo, config, context).resolve()
            head = repo.head()
            _, commit = peel_sha(repo.object_store, head)
            if not isinstance(commit, Commit):
                raise Refusal(f"{root}: HEAD is not a commit")
            head = commit.id
            items = _index_entries(repo.index_path())
            expanded = []
            for path, entry in items:
                if entry.mode == 0o040000:
                    prefix = path.rstrip(b"/")
                    _safe_path(prefix)
                    for leaf in iter_tree_contents(repo.object_store, entry.sha):
                        expanded.append((prefix + b"/" + leaf.path, replace(
                            entry, mode=leaf.mode, sha=leaf.sha,
                            extended_flags=entry.extended_flags | 0x4000)))
                else:
                    expanded.append((path, entry))
            items = expanded
            indexed = []
            for path, entry in items:
                _safe_path(path)
                stages = ((1, entry.ancestor), (2, entry.this), (3, entry.other)) if (
                    isinstance(entry, ConflictedIndexEntry)
                ) else ((entry.stage().value, entry),)
                for stage, e in stages:
                    if e is not None:
                        flags = int(bool(e.flags & 0x8000)) | (
                            int(bool(e.extended_flags & 0x2000)) << 1
                        ) | (int(bool(e.extended_flags & 0x4000)) << 2)
                        indexed.append((path, e.mode, stage, flags, bytes.fromhex(e.sha.decode())))
            indexed.sort(key=lambda e: (e[0], e[2]))
            committed = sorted((e.path, e.mode, 0, 0, bytes.fromhex(e.sha.decode()))
                               for e in iter_tree_contents(repo.object_store, commit.tree))
            dirty = any(e[2] or e[3] & 2 for e in indexed) or (
                [e[:3] + e[4:] for e in indexed] != [e[:3] + e[4:] for e in committed]
            )
            distinct = {}
            for entry in indexed:
                if entry[0] not in distinct or distinct[entry[0]][2] != 2 or entry[2] == 2:
                    distinct[entry[0]] = entry
            root_bytes = os.fsencode(working_root)
            rules = RepositoryRules(repo, config, distinct, root_bytes, context)
            working = []
            for path, mode, stage, flags, oid in distinct.values():
                if mode == 0o160000:
                    sub = os.path.join(root_bytes, path)
                    try:
                        sub_mode = os.lstat(sub).st_mode
                    except FileNotFoundError:
                        sub_mode = None
                    if sub_mode is not None and not stat.S_ISDIR(sub_mode):
                        file = _file(root_bytes, path)
                        dirty = True
                        working.append(file)
                        continue
                    metadata = os.path.lexists(os.path.join(sub, b".git"))
                    try:
                        empty = not os.listdir(sub)
                    except FileNotFoundError:
                        empty = True
                    if empty and not metadata:
                        body = oid + b"\0"
                    else:
                        if not metadata:
                            raise Refusal(f"submodule {os.fsdecode(sub)} has no repository metadata")
                        identity = _observe(Path(os.fsdecode(sub)), active)
                        state = identity["worktree"]
                        body = bytes.fromhex(identity["commit"]) + (
                            b"\0" if state["state"] == "clean" else
                            b"\1" + bytes.fromhex(state["fingerprint"])
                        )
                    file = (path, 0o160000, body)
                    dirty |= body != oid + b"\0"
                else:
                    file = _file(root_bytes, path)
                    blob = repo.object_store[oid.hex().encode()]
                    if not isinstance(blob, Blob):
                        raise Refusal("index entry does not name a blob")
                    normalized = (file[2] if mode == 0o120000 else
                                  _normalize(file[2], file[1], blob.data, rules.attributes(path), rules.autocrlf))
                    actual_mode = file[1]
                    if mode == 0o120000 and actual_mode in (0o100644, 0o100755):
                        if not rules.symlinks:
                            actual_mode = mode
                    if mode in (0o100644, 0o100755) and actual_mode in (0o100644, 0o100755):
                        if not rules.filemode:
                            actual_mode = mode
                    if file[1] != 0 or not flags & 4:
                        dirty |= actual_mode != mode or normalized != blob.data
                working.append(file)
            untracked = []

            def walk(relative=b""):
                for name in sorted(os.listdir(os.path.join(root_bytes, relative))):
                    if name.lower() == b".git":
                        continue
                    path = (relative + b"/" if relative else b"") + name
                    if path in distinct or (rules.casefold and any(p.lower() == path.lower() for p in distinct)):
                        continue
                    full = os.path.join(root_bytes, path)
                    directory = stat.S_ISDIR(os.lstat(full).st_mode)
                    children = any(p.startswith(path + b"/") or (
                        rules.casefold and p.lower().startswith(path.lower() + b"/")) for p in distinct)
                    if not children and rules.ignored(path, directory):
                        continue
                    if directory:
                        walk(path)
                    else:
                        untracked.append(_file(root_bytes, path))

            walk()
            untracked.sort()
            state = {"state": "clean"}
            if dirty or untracked:
                state = {"state": "dirty", "fingerprint_version": 2,
                         "fingerprint": fingerprint_v2(bytes.fromhex(head.decode()),
                                                       indexed, working, untracked)}
            return {"commit": head.decode(), "worktree": state}
    finally:
        active.remove(canonical)


def repo_identity(root: Path) -> dict:
    try:
        return _observe(root, set())
    except Refusal:
        raise
    except Exception as exc:
        raise Refusal(f"cannot observe checkout {root}: {exc}") from exc


def worktree_state(root: Path) -> dict:
    return repo_identity(root)["worktree"]


def checkout_root(given: str, what: str, markers: tuple[str, ...]) -> Path:
    """The canonical root of an explicitly named checkout.

    Symlinks are resolved, the result must be the root of its own git work
    tree, and every marker must be a regular file inside it.
    """
    root = Path(os.path.realpath(given))
    if not root.is_dir():
        raise Refusal(f"{what} {given}: not a directory")
    try:
        with Repo.discover(str(root)) as repo:
            if repo.bare:
                raise Refusal(f"{what} {root}: not a git checkout")
            context = _system_context()
            config = _config_stack(repo, context)
            context = _context_with_prefix(config, context)
            top = _worktree_root(repo, config, context).resolve()
    except NotGitRepository as exc:
        raise Refusal(f"{what} {root}: not a git checkout") from exc
    except Refusal:
        raise
    except Exception as exc:
        raise Refusal(f"cannot observe checkout root {root}: {exc}") from exc
    if top != root:
        raise Refusal(f"{what} {root}: not the root of its git checkout (the root is {top})")
    for marker in markers:
        mode = os.lstat(root / marker).st_mode if os.path.lexists(root / marker) else None
        if mode is None or not stat.S_ISREG(mode):
            raise Refusal(f"{what} {root}: not an {what} checkout (no regular file {marker})")
    return root


def base_image_contract(images: Path) -> int:
    path = images / "images/base-image-contract.nix"
    body = path.read_text().strip()
    if body != "1":
        raise Refusal(f"unsupported base-image contract in {path}: {body!r}")
    return int(body)


def artifact_format(spec: str):
    if spec.startswith("kernel:"):
        kernel = spec.removeprefix("kernel:")
        if kernel in KERNEL_FORMATS:
            return {"kernel": kernel}
    elif spec in PLAIN_FORMATS:
        return spec
    choices = ", ".join([f"kernel:{k}" for k in KERNEL_FORMATS] + list(PLAIN_FORMATS))
    raise Refusal(f"unknown artifact format {spec!r} (one of {choices})")


def artifact_name(role: str, arch: str, given: str) -> str:
    stem = role.replace("_", "-")
    prefix = stem if ROLES[role][2] else f"{stem}-{arch}"
    name = f"{prefix}-{os.path.basename(given)}"
    if not ARTIFACT_NAME.fullmatch(name):
        raise Refusal(f"{given}: {name!r} is not a usable artifact name")
    return name


def regular_file(given: str) -> Path:
    """The file an artifact path names. Links are followed here, deliberately:
    a Nix output is mostly links into the store, and what is recorded is the
    copy, which is always a regular file."""
    resolved = Path(os.path.realpath(given))
    try:
        mode = os.stat(resolved).st_mode
    except FileNotFoundError:
        raise Refusal(f"{given}: no such file") from None
    if not stat.S_ISREG(mode):
        raise Refusal(f"{given}: not a regular file")
    return resolved


def copy_and_hash(source: Path, dest: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with open(source, "rb") as src, open(dest, "xb") as out:
        while chunk := src.read(1 << 20):
            hasher.update(chunk)
            out.write(chunk)
            size += len(chunk)
    os.chmod(dest, 0o644)
    return hasher.hexdigest(), size


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def output_dir(given: str, checkouts: list[Path]) -> Path:
    """A new directory outside the checkout whose identity is recorded."""
    out = Path(os.path.realpath(given))
    for root in checkouts:
        if is_within(out, root):
            raise Refusal(f"--out {out} is inside the checkout {root}")
    if os.path.lexists(out) and (not out.is_dir() or any(out.iterdir())):
        raise Refusal(f"--out {out} already exists")
    if not out.parent.is_dir():
        raise Refusal(f"--out {out}: {out.parent} is not a directory")
    return out


def issued_at() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    moment = (
        datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc)
        if epoch
        else datetime.datetime.now(datetime.timezone.utc)
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_members(args, staging: Path) -> list[dict]:
    members: dict[str, dict] = {}
    names: set[str] = set()
    for role, fmt, given in args.artifact:
        if role not in ROLES:
            raise Refusal(f"unknown role {role!r} (one of {', '.join(ROLES)})")
        schema_role, bootable, independent = ROLES[role]
        name = artifact_name(role, args.arch, given)
        if name in names:
            raise Refusal(f"two artifacts would both be named {name}")
        names.add(name)
        fmt_value = artifact_format(fmt)
        digest, size = copy_and_hash(regular_file(given), staging / name)
        if size == 0:
            raise Refusal(f"{given}: empty; the schema refuses a zero-size artifact")
        member = members.setdefault(
            role,
            {
                "role": schema_role,
                "target": "arch_independent" if independent else {"arch": args.arch},
                **({"boot_protocol": "linux_direct"} if bootable else {}),
                "artifacts": [],
                "required_capabilities": [],
            },
        )
        member["artifacts"].append(
            {"name": name, "format": fmt_value, "sha256": digest, "size": size}
        )
    for role, capability in args.capability:
        if role not in members:
            raise Refusal(f"--capability names {role!r}, which has no --artifact")
        if capability not in CAPABILITIES:
            raise Refusal(
                f"unknown capability {capability!r} (one of {', '.join(CAPABILITIES)})"
            )
        caps = members[role]["required_capabilities"]
        if capability not in caps:
            caps.append(capability)
    return list(members.values())


def workload_base_contract(members: list[dict], staging: Path, expected: int) -> int:
    """Derive agreement from the copied workload roots; no root means partial."""
    contracts = set()
    for member in members:
        role = member["role"]
        if not isinstance(role, dict) or "workload_rootfs" not in role:
            continue
        roots = [a for a in member["artifacts"] if a["format"] == "ext4"]
        if len(roots) != 1:
            raise Refusal("a supplied workload root requires exactly one ext4 artifact")
        path = staging / roots[0]["name"]
        try:
            contracts.add(read_base_image_contract(path))
        except (OSError, ValueError) as error:
            raise Refusal(f"cannot inspect supplied workload root {path.name}: {error}") from error
    if contracts and contracts != {expected}:
        raise Refusal(f"inconsistent workload base-image contracts: {contracts}")
    return next(iter(contracts)) if contracts else expected


def verify_staged_artifacts(members: list[dict], staging: Path) -> None:
    """Do not publish bytes changed after copying or contract inspection."""
    for member in members:
        for artifact in member["artifacts"]:
            path = staging / artifact["name"]
            try:
                changed = (sha256_file(path) != artifact["sha256"] or
                           path.stat().st_size != artifact["size"])
            except OSError as error:
                raise Refusal(f"cannot recheck staged artifact {path.name}: {error}") from error
            if changed:
                raise Refusal(f"artifact changed while the set was being recorded: {path.name}")


def builder_boot_abi(images_root: Path) -> int:
    """The builder boot ABI this repository builds its builder image to.

    Read from `images/builder-vm/boot-abi.nix`, the same file the image and the
    release assembly read, so a local set declares the ABI its own builder image
    was built to rather than a number copied here.
    """
    path = images_root / "images" / "builder-vm" / "boot-abi.nix"
    try:
        text = path.read_text()
    except OSError as exc:
        raise Refusal(f"cannot read the builder boot ABI at {path}: {exc}") from exc
    values = re.findall(r"^\s*(\d+)\s*$", text, re.MULTILINE)
    if len(values) != 1:
        raise Refusal(
            f"{path} must hold exactly one bare integer, found {len(values)}"
        )
    return int(values[0])


def emit(args) -> Path:
    images_given = args.images_checkout or str(Path(__file__).resolve().parent.parent)
    images = checkout_root(images_given, "mvm-images", IMAGES_MARKERS)
    if args.builder_cache_contract < 1:
        raise Refusal("--builder-cache-contract must be a positive integer")
    boot_abi = builder_boot_abi(images)
    if not args.artifact:
        raise Refusal("no --artifact given; a set has at least one member")
    out = output_dir(args.out, [images])

    before = {"images": repo_identity(images)}
    contract = base_image_contract(images)
    locks = [
        {"reference": f"mvm-images:{lock}", "lock_hash": sha256_file(images / lock)}
        for lock in FLAKE_LOCKS
    ]

    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        members = build_members(args, staging)
        contract = workload_base_contract(members, staging, contract)
        after = {"images": repo_identity(images)}
        if after != before:
            raise Refusal(
                "a checkout changed while the set was being recorded; "
                f"was {json.dumps(before)}, now {json.dumps(after)}"
            )
        verify_staged_artifacts(members, staging)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "set_version": LOCAL_SET_VERSION,
            "issued_at": issued_at(),
            "producer": {"local_checkouts": before},
            "compatibility": {
                "base_image_contract": contract,
                "builder_cache_contract": args.builder_cache_contract,
                "builder_boot_abi": boot_abi,
            },
            "nix_inputs": {"flake_locks": locks, "source_revisions": []},
            "members": members,
        }
        (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
        if out.is_dir():
            out.rmdir()
        os.rename(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return out / MANIFEST_NAME


def parse_args(argv: list[str]):
    parser = argparse.ArgumentParser(
        description="Describe a locally built image set in mvm's manifest schema."
    )
    parser.add_argument(
        "--images-checkout",
        help="the mvm-images checkout (default: the one this script is in)",
    )
    parser.add_argument("--arch", required=True, choices=ARCHES, help="guest architecture")
    parser.add_argument(
        "--builder-cache-contract",
        required=True,
        type=int,
        help="the builder image cache contract the paired mvm host expects",
    )
    parser.add_argument("--out", required=True, help="new directory for the set")
    parser.add_argument(
        "--artifact",
        nargs=3,
        action="append",
        default=[],
        metavar=("ROLE", "FORMAT", "PATH"),
        help="one built file of one role; repeat for every file",
    )
    parser.add_argument(
        "--capability",
        nargs=2,
        action="append",
        default=[],
        metavar=("ROLE", "CAPABILITY"),
        help="a guest device the role needs; repeat as required",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        print(emit(args))
    except Refusal as refusal:
        print(f"emit-local-manifest: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
