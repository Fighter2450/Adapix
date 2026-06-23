"""Adapix skills system — Anthropic-style skill bundles.

A skill is a self-contained capability Adapix can use during conversation:
"brainstorm a business name", "draft a launch checklist", "reply to a
Google review", etc. Each skill lives in its own folder under
`src/adapix/skills/<mode>/<skill-name>/SKILL.md` with YAML-like
frontmatter describing when to use it, plus a markdown body containing
the actual instructions Adapix follows when the skill fires.

The agent's system prompt only includes the SHORT description of each
available skill (so we don't blow up the context window). The full body
gets loaded on demand when the agent decides to invoke a skill — either
because the user explicitly asked or because a trigger condition fired.

Public API:
    from adapix.skills import list_skills, get_skill
"""
from .loader import Skill, list_skills, get_skill, skills_index_block

__all__ = ["Skill", "list_skills", "get_skill", "skills_index_block"]
