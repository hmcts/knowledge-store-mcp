"""Find the one store this process serves, and keep every path inside it.

The root is `realpath`-resolved once, here. Every later path is joined to it and
passed through `contained`, so a symlink or `..` in a store cannot lead a read
outside the clone.
"""

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MANIFEST = Path("knowledge") / "store-manifest.json"

SOURCES = ("--store", "KNOWLEDGE_STORE_PATH", "--store-id", "CLAUDE_PLUGIN_ROOT")


@dataclass(frozen=True)
class Resolved:
    root: Path
    source: str


class NotFound(Exception):
    """No rule resolved a store. `checked` names every place looked at."""

    def __init__(self, checked: list[str]):
        self.checked = checked
        super().__init__("no knowledge store found. Checked: " + "; ".join(checked))


class AmbiguousStoreId(Exception):
    """Two locations entries carry the same `store.id`, so neither can be picked."""

    def __init__(self, store_id: str, paths: list[Path]):
        self.store_id = store_id
        self.paths = paths
        super().__init__(
            f"store id {store_id!r} is carried by more than one clone in the locations file: "
            + ", ".join(str(p) for p in paths)
            + ". Remove the entries you do not mean, or start with --store PATH."
        )


class OutsideStore(Exception):
    """A path resolved, after symlinks, to somewhere outside the store root."""

    def __init__(self, root: Path, rel: str | Path, resolved: Path):
        self.resolved = resolved
        super().__init__(f"{rel} resolves to {resolved}, which is outside the store at {root}")


def contained(root: Path, rel: str | Path) -> Path:
    """`root / rel` after `realpath`, or `OutsideStore` if it left `root`.

    `root` must already be a `realpath`-resolved path (as `resolve` returns).
    """
    resolved = (root / rel).resolve()
    if resolved != root and root not in resolved.parents:
        raise OutsideStore(root, rel, resolved)
    return resolved


def _has_manifest(root: Path) -> bool:
    try:
        return contained(root, MANIFEST).is_file()
    except OutsideStore:
        return False


def _store_id_of(root: Path) -> str | None:
    """The `store.id` in the clone's manifest, or None if it cannot be read as one."""
    if not _has_manifest(root):
        return None
    try:
        document = json.loads(contained(root, MANIFEST).read_text(encoding="utf-8"))
        store_id = document["store"]["id"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return store_id if isinstance(store_id, str) else None


def locations_file(env: Mapping[str, str], home: Path) -> Path:
    config = env.get("XDG_CONFIG_HOME") or str(home / ".config")
    return Path(config) / "knowledge-store" / "locations"


def _listed_clones(path: Path) -> list[Path]:
    """Clone roots in file order: blank lines, `#` comments and vanished paths skipped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    clones = []
    for line in lines:
        entry = line.strip()
        # A `#` comment is never an absolute path, so the is_absolute test drops it.
        candidate = Path(entry)
        if candidate.is_absolute() and candidate.is_dir():
            clones.append(candidate.resolve())
    return clones


def _by_store_id(store_id: str, listing: Path) -> Path | None:
    matches: list[Path] = []
    for clone in _listed_clones(listing):
        # The same clone listed twice, or by a symlinked path, is one entry.
        if _store_id_of(clone) == store_id and clone not in matches:
            matches.append(clone)
    if len(matches) > 1:
        raise AmbiguousStoreId(store_id, matches)
    return matches[0] if matches else None


def resolve(args: argparse.Namespace, env: Mapping[str, str], home: Path) -> Resolved:
    """The spec's order; the first rule that resolves wins."""
    checked: list[str] = []

    explicit = getattr(args, "store", None)
    if explicit:
        return Resolved(Path(explicit).resolve(), "--store")
    checked.append("--store (not given)")

    from_env = env.get("KNOWLEDGE_STORE_PATH")
    if from_env:
        return Resolved(Path(from_env).resolve(), "KNOWLEDGE_STORE_PATH")
    checked.append("KNOWLEDGE_STORE_PATH (not set)")

    store_id = getattr(args, "store_id", None)
    if store_id:
        listing = locations_file(env, home)
        found = _by_store_id(store_id, listing)
        if found is not None:
            return Resolved(found, "--store-id")
        checked.append(f"--store-id {store_id!r} (no clone with that store.id listed in {listing})")
    else:
        checked.append("--store-id (not given)")

    plugin_root = env.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        root = Path(plugin_root).resolve()
        if _has_manifest(root):
            return Resolved(root, "CLAUDE_PLUGIN_ROOT")
        checked.append(f"CLAUDE_PLUGIN_ROOT ({root} holds no {MANIFEST.as_posix()})")
    else:
        checked.append("CLAUDE_PLUGIN_ROOT (not set)")

    raise NotFound(checked)
