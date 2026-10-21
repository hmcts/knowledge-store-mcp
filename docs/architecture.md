# Deployable artefacts

Six things get released. Three repositories own them. The asker's machine only ever holds three of them, and the server reads the store from local disk with no network access.

![The library, generic plugin and fixture store come from knowledge-store-builder; the MCP server comes from knowledge-store-mcp; store data and the store plugin come from each store repository. On the asker's runtime the store plugin launches the MCP server, which reads a local clone of the store and talks to the agent over stdio, with no network or credentials.](architecture.svg)

Teal marks what the MCP work adds. The dashed teal line is the trust boundary: the server sees only the clone and the agent. The grey dashed edge is how the skill reads the store today, and it keeps working alongside the server.

## Who releases what

| Artefact | Released by | Versioned by | Consumed by |
|---|---|---|---|
| ① Library | The maintainer cuts a GitHub release | Git tag (hatch-vcs) | Store builds, pinned in the store's lock file |
| ② Generic plugin | Merging to `main` | Nothing: installs from `main` | Builders, through the marketplace |
| ③ Fixture store | Each ① release ([knowledge-store-builder#456](https://github.com/hmcts/knowledge-store-builder/issues/456)) | The ① tag | ⑥'s contract tests |
| ④ Store data | The store build pushes to its default branch | Commit, with the format declared in `store-manifest.json` ([#454](https://github.com/hmcts/knowledge-store-builder/issues/454)) | ⑥ and the skill, through the asker's clone |
| ⑤ Store plugin | The store repository's marketplace entry | Its default branch | Askers |
| ⑥ MCP server | A tag on this repository | Git tag | ⑤'s `mcpServers` entry, run with `uvx` at that tag (**proposed**) |

## Still open

- **Delivery of ⑥.** Running `uvx --from git+https://github.com/hmcts/knowledge-store-mcp@vX.Y.Z knowledge-store-mcp --store-id <id>` keeps the asker free of a Python install step. It needs `uv` on the host. The spec's [Installation](superpowers/specs/2026-10-20-knowledge-store-mcp-design.md#installation) section sets it out as the proposed route, awaiting the owner's confirmation.
