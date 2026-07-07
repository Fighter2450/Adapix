# Adapix — working agreement

Two people share this repo: Rocco (Founder & CEO) and Ben (CMO — not a
co-founder). Respect the lane split below no matter who is prompting you.

## Ownership lanes

- **Rocco (Founder & CEO — technical)** owns `src/` — the FastAPI app, AI engine, campaign
  scheduler, dashboard templates, database. Deploys with `railway up`
  (Railway project `adapix`, service `adapix-web`).
- **Ben (CMO — marketing/growth)** owns `website/` — the adapixai.com marketing
  site, copy, SEO files, brand assets. Deploys with
  `netlify deploy --prod --dir _deploy --site 140230c6-37ec-47cf-bb40-3bcd29d34c94`
  (stage clean files into `_deploy/` first; never deploy `.bak` files).
- **Shared**: `admin/` (founder board), `docs/`, this file.

**Do not edit the other lane's files.** If the task requires it, instead add
a task to the other founder's lane on the board (`admin/index.html`) with a
handoff note, and say so in your reply.

## The founder board — admin/index.html

The shared task board. Open it in a browser to view.

**Which founder am I working for?** Check `git config user.name`:
`ChenetTech` → Rocco's machine; anything else → Ben's. That founder's lane
is "your" lane for the rules below.

### Keep the board current AUTOMATICALLY — do not wait to be asked

- **At the start of a session** (before substantive work): `git pull`, read
  the `TASKS` array, and if the work you're about to do matches a task in
  your founder's lane, treat that as the task you're executing.
- **When you complete a meaningful piece of work** (feature shipped, bug
  fixed, purchase wired up, campaign launched, page deployed): flip the
  matching task to `done` — or add it as `done` if it wasn't on the board.
- **When you discover new work** (a bug you can't fix now, a follow-up, a
  dependency on the other founder): add it as `next` (or `waiting` with a
  `handoff` note if it needs the other founder). New work that belongs in
  the OTHER lane always goes on the board — never just in chat.
- **When you finish something the other founder was waiting on**: flip
  their `waiting` task to `next` in the same commit.
- Batch board edits at the end of the piece of work, not per-keystroke.

### Mechanics

1. `git pull` first — the other founder may have moved tasks.
2. Edit ONLY the `TASKS` array and the `UPDATED` stamp at the top of the
   `<script>` block in `admin/index.html`. One task per line.
   Fields: `owner` ("rocco"|"ben"), `status` ("now"|"next"|"waiting"|"done"),
   `title`, `note`, optional `handoff`.
3. Commit with a `board:` prefix (e.g. `board: rocco done with A2P`) and
   push. Board commits are small and separate from code commits.
4. Prune `done` tasks older than ~a week.
5. If the push is rejected (other founder pushed first): pull --rebase and
   push again — never force-push.

## Ground rules

- `main` is the shared branch; both founders push to it. Pull before you
  start, keep commits scoped to your own lane, never force-push.
- Secrets live in `.env` (gitignored) and Railway/Netlify env vars. Never
  print secret values into chat, commit them, or copy them into new files.
- Honesty rule for the website: never publish claims the product can't
  currently do, invented numbers, or fake testimonials. If a claim depends
  on unshipped work, mark it "rolling out" or add a board handoff.
- The purchase/subscription plan lives in `docs/PURCHASES.md` — update it
  when something gets bought (move it to "already covered").
