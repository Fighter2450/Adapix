"""Skill loader — walks the skills directory tree, parses SKILL.md files,
returns a structured catalog Adapix's chat layer can introspect.

Why hand-rolled frontmatter parsing instead of PyYAML?
  - Skill frontmatter is intentionally a tiny subset: `key: value` lines,
    and the `triggers` list as a top-level array. We don't need nested
    structures or anchors, so a 30-line parser is simpler than pulling
    in a dependency.
  - Keeping the loader dependency-free means skills are easy to ship in
    third-party plugins later without forcing a PyYAML install.

A SKILL.md file looks like:

    ---
    name: name-brainstorm
    description: Brainstorm names for a brand-new business.
    mode: new
    triggers:
      - "help me name"
      - "what should i call"
      - missing_field=practice.name
    ---
    # Name brainstorming

    When the user asks for help naming their business...

The loader yields a Skill dataclass with metadata + the markdown body.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# Directory layout: skills/<mode>/<skill-slug>/SKILL.md
SKILLS_ROOT = Path(__file__).parent


@dataclass
class Skill:
    """One skill, loaded from its SKILL.md file."""
    slug: str                         # folder name, e.g. "name-brainstorm"
    name: str                         # human-readable name
    description: str                  # one-line description shown to Adapix
    mode: str                         # "new", "existing", or "any"
    triggers: list[str] = field(default_factory=list)
    body: str = ""                    # the markdown body (instructions)
    path: Path | None = None          # path to the SKILL.md file

    def short_line(self) -> str:
        """How the skill appears in the agent's index — slug + description."""
        return f"  - `{self.slug}` — {self.description}"


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into (metadata dict, body string).

    Frontmatter is a YAML-like block at the top, between two `---` lines.
    We support:
      - `key: value` pairs (string-valued)
      - `key:` followed by indented `- item` lines (list-valued)
    Anything else is left in the body unchanged.
    """
    m = _FM_RE.match(text)
    if not m:
        return ({}, text)
    fm_text, body = m.group(1), m.group(2)
    meta: dict = {}
    current_list_key: str | None = None
    for raw in fm_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            current_list_key = None
            continue
        # list item under the last `key:` line
        if line.lstrip().startswith("- ") and current_list_key:
            item = line.lstrip()[2:].strip()
            # strip surrounding quotes (single or double)
            if (item.startswith('"') and item.endswith('"')) or (
                item.startswith("'") and item.endswith("'")
            ):
                item = item[1:-1]
            meta.setdefault(current_list_key, []).append(item)
            continue
        # `key: value` or `key:` (start of a list)
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "":
                current_list_key = key
                meta.setdefault(key, [])
            else:
                current_list_key = None
                # strip surrounding quotes if present
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                meta[key] = val
    return (meta, body)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
def _iter_skill_files(root: Path) -> Iterable[Path]:
    """Find every SKILL.md under the skills root."""
    if not root.exists():
        return []
    return root.rglob("SKILL.md")


def _load_one(skill_md: Path) -> Skill | None:
    """Load one SKILL.md into a Skill. Returns None on parse failure so a
    single malformed skill can't take the whole catalog down."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _parse_frontmatter(text)
    slug = skill_md.parent.name
    return Skill(
        slug=meta.get("name") or slug,
        name=meta.get("title") or meta.get("name") or slug.replace("-", " ").title(),
        description=meta.get("description", "").strip(),
        mode=(meta.get("mode") or "any").strip().lower(),
        triggers=list(meta.get("triggers") or []),
        body=body.strip(),
        path=skill_md,
    )


def list_skills(mode: str | None = None) -> list[Skill]:
    """Return every skill available, optionally filtered to a single mode.

    `mode=None` returns the full catalog. `mode="new"` returns skills marked
    `mode: new` plus anything marked `mode: any`. Same for `"existing"`.
    """
    out: list[Skill] = []
    for f in _iter_skill_files(SKILLS_ROOT):
        skill = _load_one(f)
        if skill is None:
            continue
        if mode is None or skill.mode in (mode, "any"):
            out.append(skill)
    # Stable order so the agent always sees the same list
    out.sort(key=lambda s: s.slug)
    return out


def get_skill(slug: str) -> Skill | None:
    """Look up a single skill by its slug (folder name / `name:` field)."""
    for f in _iter_skill_files(SKILLS_ROOT):
        if f.parent.name == slug:
            return _load_one(f)
    # Fall back to scanning by `name:` field
    for skill in list_skills():
        if skill.slug == slug:
            return skill
    return None


def skills_index_block(mode: str) -> str:
    """Format the skill catalog as a block to drop into a system prompt.

    Tells Adapix what's available, in one line each. The agent should
    mention the skill name when invoking it so the dashboard UI can
    surface a "running skill X" indicator later.
    """
    skills = list_skills(mode=mode)
    if not skills:
        return ""
    lines = [
        f"SKILLS AVAILABLE (for mode={mode!r}):",
        "  These are the named capabilities you can run during this",
        "  conversation. If the user's request matches one, name it",
        "  explicitly in your reply (e.g. \"let's run `name-brainstorm`\")",
        "  before executing it. Skills are NOT auto-invoked — you decide",
        "  when to use them based on the conversation.",
        "",
    ]
    for s in skills:
        lines.append(s.short_line())
    return "\n".join(lines)
