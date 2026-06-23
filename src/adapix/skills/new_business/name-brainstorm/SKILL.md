---
name: name-brainstorm
title: Brainstorm business names
description: Generate 12 candidate names for a brand-new business, with reasoning and a tone tag on each. Run when the user is in new-business mode and hasn't settled on a name, or asks for alternatives.
mode: new
triggers:
  - "help me name"
  - "name ideas"
  - "what should i call"
  - "brainstorm names"
  - missing_field=practice.name
---
# Name brainstorming

You are helping a founder pick a name for their new business. Most people
get this step wrong: they either spend three weeks agonizing in private,
or they settle for the first thing that sounds OK. Your job is to push
them past both failure modes by generating a lot of decent options
quickly, then helping them feel which one is right.

## Step 1 — Get three pieces of context

Ask in one short message (not three separate ones):

1. **Tone**: playful, serious, premium, scrappy, calming, bold, friendly
2. **Industry**: what they're actually selling (you may already know this
   from `practice_type_label`)
3. **Geographic flavor**: should the name feel local (place name, dialect)
   or globally generic?

If they already gave any of these earlier in the conversation, don't
re-ask — use what you have.

## Step 2 — Generate 12 candidates

Output exactly 12 names. Spread them across these archetypes — three each:

- **Descriptive**: clearly says what the business does. *(Brooklyn Bagel Co.)*
- **Compound**: two real words mashed together. *(SunBranch, MorningOrder)*
- **Coined**: invented word with a memorable shape. *(Zappos, Calendly)*
- **Evocative**: a single concrete word that hints at the vibe. *(Anchor, Lantern, Daybreak)*

Format each as:

```
1. **Anchor Coffee Co.** — Evocative / warm + grounded
   Hints at consistency without saying "coffee" twice. Easy to spell.
```

After the list, add one line: *"Want me to push harder on any of these,
or try a different angle?"*

## Style rules

- No names containing "AI", "Smart", "Pro", "Hub", "Solutions", or "Services" — exhausted territory.
- No names with numbers or hyphens.
- Avoid names that are already famous in any major industry (Apple,
  Amazon, Tesla, etc.) even if their industry is different.
- Prefer names that are easy to spell over the phone.
- One word is fine. Two words is fine. Three+ words usually isn't.

## When to step back

If the user gives you a name and you can already tell it's strong (clear,
spellable, available-sounding), don't run this skill — just say so and
keep moving. Brainstorming is for when they're stuck or want options.
