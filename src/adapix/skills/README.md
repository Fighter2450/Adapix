# Skills

Adapix uses an Anthropic-style **skill system**. Each skill is a self-contained capability — a focused task Adapix can run during a conversation. Examples: brainstorm 12 business names, build a pricing model, generate a launch checklist.

The agent loads a **short index** of all available skills into its system prompt at startup. When the conversation calls for one, it names the skill explicitly and the full body of that SKILL.md gets loaded.

---

## Why skills (vs. one giant system prompt)

- **Bounded context window** — the system prompt stays small; full skill instructions are only injected when needed.
- **Maintainability** — each capability is a separate file. Edit one without touching the others.
- **Extensibility** — `mkdir new-skill/` + drop a `SKILL.md`. The loader picks it up. No Python changes.
- **Mode-gated** — a skill marked `mode: new` only shows up for users who picked the "starting a business" branch in the welcome wizard.

---

## Directory layout

```
src/adapix/skills/
├── README.md                ← this file
├── __init__.py
├── loader.py                ← walks the tree, parses SKILL.md files
└── new_business/            ← skills for mode: new
    ├── name-brainstorm/
    │   └── SKILL.md
    ├── service-catalog/
    │   └── SKILL.md
    ├── pricing-strategy/
    │   └── SKILL.md
    ├── brand-voice/
    │   └── SKILL.md
    ├── launch-checklist/
    │   └── SKILL.md
    └── first-customer-plan/
        └── SKILL.md
```

Skill folders for the **existing-business** mode go under `skills/existing_business/` when those land.

---

## SKILL.md format

YAML-ish frontmatter (between `---` lines), then a markdown body.

```markdown
---
name: name-brainstorm
title: Brainstorm business names
description: Generate 12 candidate names for a new business, with reasoning. Run when the user is in new-business mode and hasn't settled on a name, or asks for alternatives.
mode: new
triggers:
  - "help me name"
  - "name ideas"
  - "what should i call"
  - "brainstorm names"
  - missing_field=practice.name
---
# Name brainstorming

You are helping a founder pick a name for their new business. Most people get this step wrong: they either spend three weeks agonizing in private, or they settle for the first thing that sounds OK. Your job is to push them past both failure modes…

## Step 1 — Get three pieces of context

Ask in one short message…

## Step 2 — Generate 12 candidates

Output exactly 12 names. Spread them across these archetypes…
```

### Frontmatter fields

| Field | Type | Required | What |
|---|---|---|---|
| `name` | string | yes | Slug. Matches the folder name. Used as the unique key. |
| `title` | string | no | Human-readable title shown in dashboards. Defaults to title-cased `name`. |
| `description` | string | yes | One-line summary that goes into the agent's system prompt. Be specific about WHEN to use this skill, not just what it does. |
| `mode` | enum | yes | `new`, `existing`, or `any`. Determines which welcome-wizard branch the skill is loaded for. |
| `triggers` | list of strings | no | Phrases or conditions that hint when this skill applies. Free-text phrases AND structured triggers like `missing_field=practice.name` are both fine. The loader doesn't act on these — they're hints for the agent. |

### Body

Everything after the closing `---` is the skill body. This is what the agent reads when it decides to run the skill. Write it as instructions to the model: step by step, with the structure of the output explicit. Treat it like a prompt for a junior employee who's seeing this task for the first time.

Good body conventions:

- **Start with a goal**, one sentence: "You are helping a founder pick a name…"
- **Numbered steps**, with each step doing one thing
- **Concrete output formats** with examples
- **Anti-patterns** at the end ("Don't recommend a logo iteration — the logo will not earn them a single customer")
- **Style rules** about tone, format, length

The skill body is markdown, but it's not rendered as HTML anywhere. It's just the prompt the model receives.

---

## Loader API

```python
from adapix.skills import list_skills, get_skill, skills_index_block

# Get all skills for a mode
skills = list_skills(mode="new")
# → [Skill(slug='brand-voice', ...), Skill(slug='first-customer-plan', ...), ...]

# Get one skill's full body
skill = get_skill("name-brainstorm")
# → Skill(slug='name-brainstorm', name='Brainstorm business names',
#         description='...', mode='new', triggers=[...], body='# Name brainstorming\n\n...')

# Render the short index block for the system prompt
print(skills_index_block(mode="new"))
# → SKILLS AVAILABLE (for mode='new'):
#       - `name-brainstorm` — Generate 12 candidate names…
#       - `service-catalog` — Turn a vague idea into a structured catalog…
#       ...
```

The agent's system prompt gets the **index block** (short, one line per skill). The full body is only injected when the agent decides to invoke that specific skill — keeping the per-turn context size manageable.

---

## API endpoints

The dashboard reads skills via:

- `GET /api/v1/skills?mode=new` — list of `{slug, name, description, mode, triggers}`
- `GET /api/v1/skills/{slug}` — full SKILL.md body for the named skill

Used by the Workshop tab to render tool cards.

---

## Writing a new skill

```bash
mkdir src/adapix/skills/new_business/<slug>/
touch src/adapix/skills/new_business/<slug>/SKILL.md
```

Open the file and write the frontmatter + body. Save. Hit the dashboard, the skill is live. No restart needed unless you're caching aggressively.

Suggested structure for a body that does its job:

```markdown
# <Title>

You are helping <who> with <what>. Most people get this wrong by <typical failure mode>. Your job is to <the specific value this skill delivers>.

## Step 1 — <single action>

<exact instruction>

## Step 2 — <next action>

<format spec, with an example>

```
<example output>
```

## Anti-patterns

- <thing the model is tempted to do that you don't want>
- <another thing>

## Style

- <length cap>
- <tone rule>
- <format rule>
```

Keep skill bodies under ~2 KB. If you're going longer, the skill probably wants to be split into two.

---

## Don't write skills for…

- **One-off questions** ("what's the difference between LLC and S-corp" — the agent already knows this)
- **Things that need real data** (a skill can't query a database; build a tool for that instead)
- **General knowledge** (no skill for "explain what a brand is" — too broad, the agent's base capability covers it)

Skills are best for **structured, multi-step tasks with a clear output format** that you want consistent results from. Brainstorming, catalogs, checklists, plans, voice docs — yes. Trivia, single facts, free-form chat — no.
