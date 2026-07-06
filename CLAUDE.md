# Adapix — working agreement

Two founders share this repo. Respect the lane split below no matter which
founder is prompting you.

## Ownership lanes

- **Rocco (technical)** owns `src/` — the FastAPI app, AI engine, campaign
  scheduler, dashboard templates, database. Deploys with `railway up`
  (Railway project `adapix`, service `adapix-web`).
- **Ben (marketing/growth)** owns `website/` — the adapixai.com marketing
  site, copy, SEO files, brand assets. Deploys with
  `netlify deploy --prod --dir _deploy --site 140230c6-37ec-47cf-bb40-3bcd29d34c94`
  (stage clean files into `_deploy/` first; never deploy `.bak` files).
- **Shared**: `admin/` (founder board), `docs/`, this file.

**Do not edit the other lane's files.** If the task requires it, instead add
a task to the other founder's lane on the board (`admin/index.html`) with a
handoff note, and say so in your reply.

## The founder board — admin/index.html

The shared task board. Open it in a browser to view. To update:

1. `git pull` first — the other founder may have moved tasks.
2. Edit ONLY the `TASKS` array and the `UPDATED` stamp at the top of the
   `<script>` block in `admin/index.html`. One task per line.
   Fields: `owner` ("rocco"|"ben"), `status` ("now"|"next"|"waiting"|"done"),
   `title`, `note`, optional `handoff`.
3. Commit with a `board:` prefix (e.g. `board: rocco done with A2P`) and push.
4. Prune `done` tasks older than ~a week.

When a founder finishes something the other is waiting on, flip the other's
`waiting` task to `next` in the same commit.

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
