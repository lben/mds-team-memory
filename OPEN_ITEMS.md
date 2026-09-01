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

**Round 1 (2026-09-01) confirmation.** Four of six fresh personas hit this
unprompted and it was the only finding more than one of them reached
independently — "wait, who is 'Browser profile 2012'? is that me? is that a
person?" (designer), "wait, who is Browser profile 808C? is that me?" (QA lead),
"the identity labels are pretty bleak by default — nobody but Wei Chen has a
real name" (the admin, about his own team). A new engineer's blocker was the
sharpest version: *"I genuinely cannot find out who is tagged as the payments
expert"* — the routing table is admin-only, and everywhere else the names are
hex. The screenshot `e2e/adv/sense/07_expertise_routing.png` shows the
contradiction in one frame: the page badge reads `ADMIN · benito` and the
sidebar 550px below reads `Browser profile D74D · Anonymous · no name set`.
A second-order effect worth noting for whatever is decided: because a name is
self-declared per browser, the team splits into named and hex people at once,
and the designer could not tell "if 'Browser profile 8E54' is a real colleague,
a test account, or several different browsers I've been switching between".

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

## 5. The knowledge graph is mostly empty space — open, now with the mechanism

Fills roughly 30% of its panel with 15 concepts and no links: a large dark area
of very little. Unclear whether it earns its position at the top of the page.

**Round 1 measured why, and it costs more than space.** With 8 nodes and 7 edges
the panel is 1172×380, the drawing is 480×320 — **34% fill** — and `cy.zoom()`
is **0.675**. The graph is zoomed *out* while two thirds of the panel is empty,
because the panel is a wide, short letterbox and fitting a roughly square layout
into it is constrained by the 380px height; the spare width cannot be used.

The zoom is also why the labels are unreadable: the 11px node style renders at
**7.4px** on screen, and the label sits *inside* a 36–50px circle, so every name
overflows its node. The component already solved exactly this for content nodes
(`text-valign: bottom`, with the comment "inside a small circle they overflowed
and collided with neighbours") but concept nodes still centre their label.

Two personas reported this as two different bugs, both actually this one: a node
read as `Paymenta` instead of `Payments`, and a node that appeared to be "just a
location-pin icon, no visible text" (it was `CI`). Neither is a rendering glitch
— both are 7.4px text.

Recommendation, for the owner's decision alongside "does the graph earn its
place": either give the panel a squarer aspect so `fit()` can use the width, or
cap the fit at zoom 1 and pan, or move concept labels below their nodes as the
content nodes already do. Not changed unasked — this is part of the same open
question about the panel.

## 6. Mobile is out of scope — decided (2026-08-31)

The sidebar takes 190px of a 390px viewport and clips the primary button. The
owner has ruled that this app will not be used from phones. **Do not file this
again and do not test it as pass/fail** — measure and note only.

## 7. Item relationships endpoint — fixed (2026-08-31)

`GET /api/items/{id}/relationships` had no caller anywhere in the frontend.
Deleted along with its test in `8d7eac5`.

---

# Found and fixed in round 1 (2026-09-01)

## 8. Any unknown `/api` path answered 200 with the application — fixed

`GET /api/anything-that-does-not-exist` returned HTTP 200 and `index.html`,
because the SPA catch-all claimed every unmatched path. A caller expecting JSON
got HTML with a success status: `res.ok` is true, so `api.ts` fell through to
`res.json()` and threw a raw `SyntaxError` rather than an `ApiError` — which the
call sites' `e instanceof ApiError` handlers turn into a generic "Search failed",
and which loaders with no `catch` turn into an unhandled rejection. It also made
a removed or mistyped endpoint look alive, and corrupted coverage measurement.

Fixed at the choke point in `backend/app/main.py`: the SPA handler now 404s for
the namespaces the server owns (`api/`, `ws/`) and serves the application for
everything else. Proven by removing the fix and watching
`test_unknown_api_path_is_a_json_404_not_the_spa` and the new adversarial2 check
both go red.

## 9. You were routed your own question — fixed

If you were a mapped expert for a concept your own question mentioned, the app
told you to answer yourself: the question was pinned to the top of your queue
with a **NEEDS YOUR EXPERTISE** badge, and its detail line listed you under
"Suggested experts". Found by a QA-lead persona: *"it's suggesting ME as the
expert to answer the question I just asked myself"*.

Root cause: neither routing surface excluded the author — `matches_me` in
`list_questions` and the `suggested_experts` query in `question_detail`. Both
fixed together; other mapped experts are still routed the question. Proven by
`test_you_are_never_routed_your_own_question`, watched red before the fix.

---

# Newly found in round 1, needing the owner's decision

Each was classified by the read-only audit as changing behaviour, so it was
surfaced rather than acted on. Recommendations are given, not applied.

## 10. The admin's four curation actions sit behind native browser dialogs — open

Approve, Reject, Delete link and Delete concept each raise a `window.prompt` or
`window.confirm` before doing anything. They work correctly. But Chrome offers
"Prevent this page from creating additional dialogs" after repeated dialogs —
and an admin curating a list of suggested links will see many. Once ticked,
**every subsequent Approve/Reject silently does nothing**: no request, no toast,
no visual change. That is precisely the "control that silently does nothing"
root cause, reachable by an ordinary admin doing the page's main job.
*Recommendation: move these four to the app's own modal, which every other
confirmation already uses.*

## 11. Read paths have no error handling, so a failed load shows stale truth — partly fixed

Mutations catch and report; loaders do not (`AdminExpertiseView.loadData`,
`runPreview`, `loadState`, `removeMapping`; `ScratchpadView.load/find/createPad`;
`DocumentsView.load`; `KnowledgeGraph.refresh/loadFull/focus`;
`store.loadProfile`). These remain **open**: the fix is a shared helper and a
decision about what each screen should show instead, which is the owner's call.

**The one case that actively misinformed is fixed.** `DocumentsView.open` was
verified by hand: uploading a document and then navigating *inside the app* to a
missing id left the previous document rendered under the new URL — the viewer
still read `a-real-file.txt` while the address bar read a dead id. It now clears
the pane and reports the error.

The existing adversarial check could not see this: it reached the bad id with
`goto()`, a full reload, where there is nothing stale to keep. `adversarial4.py`
now also navigates in-app, and that check was watched failing before the fix and
passing after — another instance of "a check too weak to fail".

## 12. Three more controls that do nothing and say nothing — open

`TESTING_LESSONS` §3 records this class as found and fixed, but the sweep missed
`saveName` (App.vue), `addAdmin` (AdminExpertiseView) and `runSearch`
(HomeView): each returns silently on empty input while their siblings in
`MapAdminPanel` all call `store.notify`. *Recommendation: add the missing
notify to Save name and Add admin. Search-on-empty is defensible as-is — the box
is visibly empty and the button sits under it — so it is called out rather than
lumped in.*

## 13. "View contribution" only closes the dialog — open

On Home, `SuccessModal`'s primary button emits `view`, which `HomeView` handles
identically to Close. The same button really does navigate from Documents and
Scratchpad, so one label means two things. *Recommendation: open the new
contribution — `HomeView` already holds `result.item.id` and an
`ItemDetailModal` bound to `detailId`.*

## 14. Defining a concept never discovers links in content that already exists — open

Creating or renaming a concept calls `retag_everything` but never runs link
discovery; that only happens when someone posts something new. An admin who
defines two concepts that co-occur in fifty existing notes sees an empty map and
no explanation, while README promises a link is suggested "when team content
mentions two concepts together". *Recommendation: run discovery for the changed
concept's pairs only, which bounds the cost.*

## 15. "Helped me" is implemented three times and they disagree — open

`HomeView` and `QuestionCard` optimistically increment the counter locally;
`ItemDetailModal` reloads from the server. The same click therefore produces
different numbers depending on which surface you clicked. *Recommendation: one
helper that applies the server's `created` flag, used by all three.*

## 16. Two Delete buttons in one table ask; the third does not — open

`deleteLink` and `deleteConcept` confirm first, `deleteType` does not. The
server refuses to delete a type still in use, so the blast radius is small, but
the inconsistency is what the next person copies.

## 17. Dead surface that needs a migration to remove — open

Deleting these is the standing rule, but each touches the schema, so they are
listed for a deliberate decision rather than removed mid-round:
`KnowledgeItem.title` (never assigned by any constructor; NULL for every row
ever created, yet five call sites defensively concatenate it and it holds a slot
in the FTS index), `Relationship.evidence` (written once, read nowhere),
`Document.status` (never anything but the default; the UI renders "TEXT
EXTRACTED" unconditionally), and the hardcoded `"verified": False` returned by
four endpoints and read by no template. The last one is entangled with item 1 —
it is a placeholder for an identity system that does not exist yet, so it should
be kept or dropped as part of that decision, not on its own.

## 18. Sharing a passage or an excerpt does not count toward "knowledge shared" — open

`shared_total` is computed and returned by the passage-share and scratchpad-share
endpoints but discarded by both callers, so the "that is the Nth piece of
knowledge you have shared" line appears after Capture and not after the other
two ways of sharing. Either wire it up or stop paying for the query.

## 19. One sentence can produce a burst of suggested links — open

`MDS_COOCCURRENCE_MIN` defaults to 1, so a single contribution mentioning three
concepts creates a suggested link for every pair. An admin persona put it as:
*"my single throwaway capture note spawned five separate pairwise 'detected'
links, most of which are just noise from co-occurring in one sentence"*. The
README already documents the threshold as tunable; this is a note that the
default is what a curating admin actually experiences, not a defect.
