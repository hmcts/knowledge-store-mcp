"""Regenerate `store/`: a two-repository store written by knowledge-store-builder's
own `relationships` and `manifest` stages, at the release named in `LIBRARY`.

    uv run python tests/library_store/generate.py

The library is not a dependency of this repository: it is not on PyPI, its
internal feed needs credentials CI does not hold, and installing it from git
means building it, which CI's `uv sync --no-build` refuses on purpose. So its
output is committed, and this script is how that output was made. It needs `uvx`
and `git` on PATH and network access to GitHub.

The inputs below are the only hand-written part: two clones, the repository
list, and one extra layer the store declares inside the relationships directory.
Everything copied into `store/` is the library's own bytes. The relationships
stage writes no example URLs here, because nothing records where the clones were
built from; that is the library's output for such a store, not an omission.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The release the committed store came from. The test checks the manifest's
# `library_version` against VERSION, so the record cannot drift from the bytes.
VERSION = "0.18.0"
LIBRARY = f"git+https://github.com/hmcts/knowledge-store-builder@v{VERSION}"

HERE = Path(__file__).resolve().parent
OUT = HERE / "store"

# alpha-lib publishes an npm package; beta-app depends on it.
CLONES = {
    "alpha-lib": {
        "package.json": '{"name": "@example/alpha-lib", "version": "1.2.0"}\n',
    },
    "beta-app": {
        "package.json": (
            '{\n  "name": "beta-app",\n  "dependencies": {\n'
            '    "@example/alpha-lib": "^1.2.0"\n  }\n}\n'
        ),
    },
}
REPOSITORIES_TXT = "".join(
    f"{name}|https://github.com/example-org/{name}.git|main\n" for name in CLONES
)

# A layer the store owns, declared in `extra_layers` and kept beside the library's
# relationships files: it must never be read as a repository.
EXTRA_LAYER = "knowledge/relationships/ownership-notes.json"
EXTRA_LAYERS_JSON = (
    '{\n  "ownership_notes": {\n'
    f'    "path": "{EXTRA_LAYER}",\n'
    '    "description": "Which team owns each shared library",\n'
    '    "kind": "answers"\n  }\n}\n'
)
EXTRA_LAYER_JSON = (
    '{\n  "who-owns-alpha-lib": {\n'
    '    "title": "Who owns alpha-lib?",\n'
    '    "html": "<p>The platform team publishes alpha-lib for every web application.</p>",\n'
    '    "source": "docs/answers/who-owns-alpha-lib.md"\n  }\n}\n'
)

# What is copied: the layer directory whole, and the manifest.
COPIED = ("knowledge/relationships", "knowledge/store-manifest.json")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _inputs(root: Path) -> None:
    for name, files in CLONES.items():
        clone = root / "repositories" / name
        for rel, text in files.items():
            _write(clone / rel, text)
        # The stage reads a directory as a clone only when it holds `.git`.
        git = ["git", "-C", str(clone), "-c", "user.name=fixture", "-c", "user.email=f@x.invalid"]
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "fixture"], check=True)
    _write(root / "config" / "repositories.txt", REPOSITORIES_TXT)
    _write(root / "config" / "store-extra-layers.json", EXTRA_LAYERS_JSON)
    _write(root / EXTRA_LAYER, EXTRA_LAYER_JSON)


def _stage(root: Path, *arguments: str) -> None:
    env = {
        **os.environ,
        "KSB_ROOT": str(root),
        "KSB_GITHUB_ORG": "example-org",
        "KSB_STORE_ID": "library-contract-store",
    }
    command = ["uvx", "--from", LIBRARY, "knowledgestore", *arguments]
    subprocess.run(command, cwd=root, env=env, check=True)


def _clear(directory: Path) -> None:
    """Empty `directory` file by file, so a file the library no longer writes
    does not survive a regeneration."""
    if not directory.exists():
        return
    for path in sorted(directory.rglob("*"), reverse=True):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _inputs(root)
        _stage(root, "relationships")
        _stage(root, "manifest")
        _clear(OUT)
        for rel in COPIED:
            source, target = root / rel, OUT / rel
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    print(f"wrote {OUT.relative_to(HERE.parent.parent)} from {LIBRARY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
