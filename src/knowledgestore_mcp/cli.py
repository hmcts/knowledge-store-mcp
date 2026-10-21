"""Command line entry point: find the store, open it, serve it over stdio.

stdout is the protocol channel from the first byte, so nothing else is ever
written there: logging goes to stderr, and a store that cannot be served is
reported on stderr with exit status 2, before the server starts.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from . import __version__, index, locate
from .locate import AmbiguousStoreId, NotFound
from .manifest import Refused
from .results import DEFAULT_MAX_RESPONSE_BYTES
from .server import INSTRUCTIONS, KnowledgeStoreServer

# The exit status of a store that cannot be served, as argparse uses for bad usage.
REFUSED = 2


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be a whole number of bytes, 1 or more: {value!r}")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="knowledge-store-mcp",
        description="A read-only MCP server that answers questions from a knowledge store.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--store", metavar="PATH", help="the store's root directory")
    parser.add_argument(
        "--store-id",
        metavar="ID",
        help="the store.id of a clone listed in ~/.config/knowledge-store/locations",
    )
    parser.add_argument(
        "--max-response-bytes",
        type=_positive,
        default=DEFAULT_MAX_RESPONSE_BYTES,
        metavar="N",
        help="the most bytes one answer may be; a longer list is paged "
        f"(default {DEFAULT_MAX_RESPONSE_BYTES})",
    )
    parser.add_argument(
        "--print-instructions",
        action="store_true",
        help="print the answering guidance the server sends to clients, and exit; "
        "for a client that drops it",
    )
    args = parser.parse_args(argv)
    if args.print_instructions:
        # Bytes, not text: the copy must be exactly what is sent, whatever the
        # platform does to line endings. No store is resolved or opened.
        sys.stdout.buffer.write(INSTRUCTIONS.encode("utf-8"))
        sys.stdout.flush()
        return 0
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    home = Path.home()
    try:
        resolved = locate.resolve(args, os.environ, home)
        served = KnowledgeStoreServer(
            resolved.root,
            index.cache_home(os.environ, home),
            max_response_bytes=args.max_response_bytes,
        )
    except (NotFound, AmbiguousStoreId, Refused) as refusal:
        print(f"knowledge-store-mcp: {refusal}", file=sys.stderr)
        return REFUSED
    try:
        asyncio.run(served.serve_stdio())
    finally:
        served.close()
    return 0
