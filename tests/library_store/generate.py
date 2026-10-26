"""Regenerate `store/`: a store knowledge-store-builder's own stages wrote, at the
release named in `VERSION`, with the graph graphify `GRAPHIFY` extracted.

    uv run python tests/library_store/generate.py           # rebuild store/
    uv run python tests/library_store/generate.py --check   # rebuild elsewhere, compare

The library is not a dependency of this repository: it is not on PyPI, its
internal feed needs credentials CI does not hold, and installing it from git
means building it, which CI's `uv sync --no-build` refuses on purpose. So its
output is committed, and this script is how that output was made. It needs `uv`
and `git` on PATH, and network access the first time, to build the frozen
environment `requirements.txt` names.

This script writes the inputs - three git repositories, their bare remotes, the
store's `config/` and one extra layer - into a fresh temporary directory, then
runs `pipeline.py` inside the frozen environment, which runs the library's real
stages in their documented order. Everything copied into `store/` is the
library's or graphify's own bytes. Every commit carries a fixed identity and
date, so the shas recorded in `inputs.json` and every byte of the store are the
same on every run; `--check` proves that by rebuilding and comparing.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The release the committed store came from. The tests check the manifest's
# `library_version` against it, so the record cannot drift from the bytes.
VERSION = "0.19.0"
GRAPHIFY = "0.9.93"
LIBRARY = f"git+https://github.com/hmcts/knowledge-store-builder@v{VERSION}"
# The interpreter the frozen environment was compiled for.
PYTHON = "3.13"

HERE = Path(__file__).resolve().parent
OUT = HERE / "store"
NO_INDEX = HERE / "no-index"
INPUTS = HERE / "inputs.json"
REQUIREMENTS = HERE / "requirements.txt"
PIPELINE = HERE / "pipeline.py"

ORG = "example-org"
STORE_ID = "library-contract-store"

# Who made every commit. Fixed, with the dates below, so every sha is too.
IDENTITY = {
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture Author",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}

ALPHA_PACKAGE = '{\n  "name": "@example/alpha-lib",\n  "version": "1.2.0"\n}\n'
ALPHA_REFUND = """\
export function refund(amount: number): number {
  return -amount;
}

export class RefundHandler {
  handle(amount: number): number {
    return refund(amount);
  }
}
"""
ALPHA_SCHEMA = """\
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://example.org/schemas/refund.schema.json",
  "title": "Refund",
  "type": "object",
  "$defs": {
    "money": {
      "type": "object",
      "properties": {
        "amount": {"type": "number"}
      }
    }
  },
  "properties": {
    "requestedOn": {"type": "string", "format": "date"},
    "total": {"$ref": "#/$defs/money"}
  }
}
"""

# `gamma-service` is a package no clone publishes, so `relationships` records it
# under `unresolved_targets_outside_estate` with its name as the key, which the
# server answers for the repository name `gamma-service`.
BETA_PACKAGE = """\
{
  "name": "beta-app",
  "dependencies": {
    "@example/alpha-lib": "^1.2.0",
    "gamma-service": "^2.0.0"
  }
}
"""
BETA_PAY = """\
import { refund } from "@example/alpha-lib";

export class PaymentService {
  pay(amount: number): number {
    return refund(amount);
  }
}
"""
BETA_PAY_RETRY = """\
import { refund } from "@example/alpha-lib";

export class PaymentService {
  pay(amount: number): number {
    return refund(amount);
  }

  retry(amount: number): number {
    return this.pay(amount);
  }
}
"""
BETA_CODEOWNERS = "* @example-org/payments-team\n"
BETA_SECURITY = "# Security\n\nReport a vulnerability to security@example.org.\n"
BETA_DEPENDABOT = """\
version: 2
updates:
  - package-ecosystem: npm
    directory: /
    schedule:
      interval: weekly
"""
BETA_CODEQL = """\
name: CodeQL
on: [push]
jobs:
  analyse:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: javascript-typescript
      - uses: github/codeql-action/analyze@v3
"""

DELTA_VALUES = "replicas: 2\n"
DELTA_APPLICATION = "password: fixture-not-a-secret\n"

# Each repository's commits on `main`, oldest first: (subject, date, files).
# A file mapped to None is deleted. Dates are distinct and in October 2026.
COMMITS = {
    "alpha-lib": [
        (
            "DEMO-1: refund handler",
            "2026-10-01T09:00:00+00:00",
            {"package.json": ALPHA_PACKAGE, "src/refund.ts": ALPHA_REFUND},
        ),
        (
            "DEMO-2: refund schema",
            "2026-10-02T09:00:00+00:00",
            {"schemas/refund.schema.json": ALPHA_SCHEMA},
        ),
    ],
    "beta-app": [
        (
            "DEMO-1: pay via refund",
            "2026-10-05T09:00:00+00:00",
            {"package.json": BETA_PACKAGE, "src/pay.ts": BETA_PAY},
        ),
        (
            "DEMO-3: ownership",
            "2026-10-06T09:00:00+00:00",
            {
                ".github/CODEOWNERS": BETA_CODEOWNERS,
                "SECURITY.md": BETA_SECURITY,
                ".github/dependabot.yml": BETA_DEPENDABOT,
                ".github/workflows/codeql.yml": BETA_CODEQL,
            },
        ),
    ],
    "delta-deploy": [
        (
            "DEMO-3: production values",
            "2026-10-08T09:00:00+00:00",
            {
                "ansible/group_vars/prd/beta-app_values.yaml.j2": DELTA_VALUES,
                "config/application.yml": DELTA_APPLICATION,
            },
        ),
    ],
}
# One branch never merged: its ticket is the proposed one.
BRANCHES = {
    "beta-app": (
        "feature/DEMO-4-retry",
        [("DEMO-4: retry", "2026-10-07T09:00:00+00:00", {"src/pay.ts": BETA_PAY_RETRY})],
    ),
}

REPOSITORIES_TXT = "".join(f"{name}|https://github.com/{ORG}/{name}.git|main\n" for name in COMMITS)

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

# Library outputs never committed: the plain graph and index sit beside the
# archives a store commits instead.
NOT_COPIED = ("graphify-out/graph.json", "knowledge/index/store.sqlite")
# Committed although no layer names it: the partitioner record travels with the
# graph so a reader can tell whether its environment reproduces the clustering.
ALSO_COPIED = ("graphify-out/clustering-inputs.json",)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git_env() -> dict[str, str]:
    """git with no user or system configuration, so nothing on this machine -
    a signing key, a hook path, a default branch - reaches a commit."""
    return {
        **os.environ,
        **IDENTITY,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _git(repo: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env=env or _git_env(),
    )
    return completed.stdout


def _commit(repo: Path, subject: str, date: str, files: dict[str, str]) -> None:
    for rel, text in files.items():
        _write(repo / rel, text)
    _git(repo, "add", "--", *files)
    dated = {**_git_env(), "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    _git(repo, "commit", "-q", "-m", subject, env=dated)


def _sources(tmp: Path) -> dict[str, str]:
    """Each repository built in `sources/`, pushed bare to `remotes/`. Returns
    each one's `main` sha."""
    shas = {}
    for name, commits in COMMITS.items():
        repo = tmp / "sources" / name
        repo.mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "main")
        for subject, date, files in commits:
            _commit(repo, subject, date, files)
        if name in BRANCHES:
            branch, extra = BRANCHES[name]
            _git(repo, "checkout", "-q", "-b", branch)
            for subject, date, files in extra:
                _commit(repo, subject, date, files)
            _git(repo, "checkout", "-q", "main")
        shas[name] = _git(repo, "rev-parse", "HEAD").strip()
        remote = tmp / "remotes" / f"{name}.git"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(repo), str(remote)],
            check=True,
            env=_git_env(),
        )
    return shas


def _store_inputs(root: Path) -> None:
    _write(root / "config" / "repositories.txt", REPOSITORIES_TXT)
    _write(root / "config" / "store-extra-layers.json", EXTRA_LAYERS_JSON)
    _write(root / EXTRA_LAYER, EXTRA_LAYER_JSON)


def _pipeline(tmp: Path, root: Path, *flags: str) -> None:
    """Run `pipeline.py` in the frozen environment. git's `insteadOf` sends every
    `https://github.com/example-org/` URL to the bare remotes, so the real `sync`
    clones offline from the URLs `config/repositories.txt` names."""
    env = {
        **_git_env(),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{(tmp / 'remotes').as_uri()}/.insteadOf",
        "GIT_CONFIG_VALUE_0": f"https://github.com/{ORG}/",
        "PYTHONHASHSEED": "0",
    }
    command = [
        "uv",
        "run",
        "--no-project",
        "--python",
        PYTHON,
        "--with-requirements",
        str(REQUIREMENTS),
        "python",
        str(PIPELINE),
        str(root),
        *flags,
    ]
    subprocess.run(command, cwd=root, env=env, check=True)


def _copied(root: Path) -> list[str]:
    """Store-relative files to commit: every path of every layer the manifest
    marks present (but the plain graph and index), plus `ALSO_COPIED`."""
    manifest = json.loads((root / "knowledge/store-manifest.json").read_text("utf-8"))
    tables = [manifest["layers"], manifest.get("extra_layers") or {}]
    wanted = {"knowledge/store-manifest.json", *ALSO_COPIED}
    for table in tables:
        for layer in table.values():
            if not layer.get("present"):
                continue
            for rel in layer.get("paths") or [layer.get("path")]:
                if rel:
                    wanted.add(rel.rstrip("/"))
    files = set()
    for rel in wanted - set(NOT_COPIED):
        path = root / rel
        if path.is_dir():
            files.update(
                found.relative_to(root).as_posix() for found in path.rglob("*") if found.is_file()
            )
        elif path.is_file():
            files.add(rel)
    return sorted(files)


def build(tmp: Path) -> tuple[Path, dict[str, str]]:
    """Inputs and every stage, in `tmp`. Returns the store root and the shas."""
    shas = _sources(tmp)
    root = tmp / "store"
    root.mkdir()
    _store_inputs(root)
    _pipeline(tmp, root)
    return root, shas


def _inputs_json(shas: dict[str, str]) -> str:
    return json.dumps({"shas": dict(sorted(shas.items()))}, indent=1) + "\n"


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


def _export(root: Path, target: Path) -> None:
    _clear(target)
    for rel in _copied(root):
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / rel, destination)


def _differences(built: Path, committed: Path) -> list[str]:
    def listing(directory: Path) -> set[str]:
        if not directory.exists():
            return set()
        return {
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        }

    ours, theirs = listing(built), listing(committed)
    differing = sorted(ours ^ theirs)
    for rel in sorted(ours & theirs):
        if (built / rel).read_bytes() != (committed / rel).read_bytes():
            differing.append(rel)
    return sorted(differing)


def main(argv: list[str]) -> int:
    check = argv == ["--check"]
    if argv and not check:
        print("usage: generate.py [--check]", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name).resolve()
        try:
            root, shas = build(tmp)
        except subprocess.CalledProcessError as failed:
            print(f"generation failed, nothing written: {failed}", file=sys.stderr)
            return 1
        if check:
            fresh = tmp / "out"
            _export(root, fresh)
            differing = [f"store/{rel}" for rel in _differences(fresh, OUT)]
            if INPUTS.read_text("utf-8") != _inputs_json(shas):
                differing.append(INPUTS.name)
            for rel in differing:
                print(f"differs: {rel}")
            if differing:
                return 1
            print(f"store/ reproduces from {LIBRARY}")
            return 0
        _export(root, OUT)
        INPUTS.write_text(_inputs_json(shas), encoding="utf-8")
    print(f"wrote {OUT.relative_to(HERE.parent.parent)} from {LIBRARY}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
