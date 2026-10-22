"""README headings that other repositories deep-link must keep their anchors."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def slugify(heading: str) -> str:
    """GitHub's rule: lowercase, drop characters other than alphanumerics,
    spaces and hyphens, then turn spaces into hyphens."""
    kept = re.sub(r"[^a-z0-9 \-]", "", heading.lower())
    return kept.replace(" ", "-")


def readme_headings() -> list[str]:
    headings = []
    in_fence = False
    for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            match = re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)
            if match:
                headings.append(match.group(1))
    return headings


class ReadmeAnchorsTest(unittest.TestCase):
    def test_every_declared_anchor_has_a_heading(self):
        """Catches: a README heading renamed while another repository's deep
        link to it still points at the old anchor."""
        declared = [
            line.strip()
            for line in (ROOT / "docs" / "load-bearing-anchors.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        self.assertTrue(declared, "no anchors declared, so nothing is guarded")
        slugs = {slugify(heading) for heading in readme_headings()}
        missing = [anchor for anchor in declared if anchor not in slugs]
        self.assertEqual(missing, [], f"README has no heading for: {missing}")

    def test_slugify_follows_github(self):
        """Catches: the slug rule drifting from GitHub's, which would let a
        renamed heading pass or a kept heading fail."""
        self.assertEqual(
            slugify("Claude Code and Claude Desktop"), "claude-code-and-claude-desktop"
        )
        self.assertEqual(slugify("Microsoft 365 Copilot"), "microsoft-365-copilot")
        self.assertEqual(slugify("A (hosted) agent: v2"), "a-hosted-agent-v2")


if __name__ == "__main__":
    unittest.main()
