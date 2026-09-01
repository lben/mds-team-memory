# Open items

Known-wrong or known-undecided things in MDS Team Knowledge. This is the single
source of truth for them — `TESTING_LESSONS.md` teaches *how to test*, this file
tracks *what is currently outstanding*. Update the status here when something is
decided, fixed, or newly found. Do not duplicate these into other documents.

> **For the orchestrating agent: never paste this file, or anything from it, into
> a dogfooding subagent's prompt.** The point of a fresh persona is that it
> discovers these independently. A persona told about the identity problem will
> not trip over it, and the "what the hell" count stops measuring anything. Read
> this yourself; let them find it themselves; compare afterwards.

Status values: **decided** (owner has ruled, do not re-litigate) · **open**
(needs the owner's decision) · **fixed** (resolved, kept for history).

---

## 1. Identity is a per-browser cookie — open

The biggest gap, and a product decision rather than a defect. Everything
human-facing inherits it:

- A contributor is `Browser profile 3135`. Every name carries `unverified` or
  `Anonymous · no name set` forever, because nobody is ever verified.
- **Signing in as admin does not change who you are.** Signed in as `benito`,
  the sidebar still reads `Browser profile 5E8D · Anonymous · no name set`, and
  the admin's own contributions are credited to a hex code.
- The expertise routing screen — the admin area's whole purpose — asks the admin
  to pick an expert from a list of hex codes. Unanswerable as designed.
- The leaderboard ranks hex codes, and shows rank `—` for everyone on zero impact.
- The same person on a laptop and a desktop is two contributors. Clearing cookies
  creates a third and **permanently destroys their scratchpad**, which has no
  login, no export and no recovery.

Until this is decided, "makes sense" cannot be fully certified.
*Observed 2026-08-31 via `e2e/sensecheck.py`.*

## 2. Nothing is editable or deletable by its author — open

Your own contribution offers only `Helped me` (disabled, it is yours) and
`Details`. A typo is permanent. Documents have no delete endpoint and no delete
control, so a wrongly uploaded file is there forever.

## 3. "Endorse as expert" is offered to everyone — open

The button shows for anyone who is not the author, but the server requires the
endorser to be a mapped expert for a concept detected in that item, so most
clicks fail with `Only a mapped expert for this topic can endorse`. The client
has no signal for whether it will work. Fixing properly means the API telling the
client (e.g. a `can_endorse` flag).

## 4. Enter in the composer runs a search — open

Pressing Enter while writing a contribution runs a **search**. The text survives
in the box, so nothing is lost, but the person expected to be typing.
Shift+Enter does insert a newline.

## 5. The knowledge graph is mostly empty space — open

Fills roughly 30% of its panel with 15 concepts and no links: a large dark area
of very little. Unclear whether it earns its position at the top of the page.

## 6. Mobile is out of scope — decided (2026-08-31)

The sidebar takes 190px of a 390px viewport and clips the primary button. The
owner has ruled that this app will not be used from phones. **Do not file this
again and do not test it as pass/fail** — measure and note only.

## 7. Item relationships endpoint — fixed (2026-08-31)

`GET /api/items/{id}/relationships` had no caller anywhere in the frontend.
Deleted along with its test in `8d7eac5`.
