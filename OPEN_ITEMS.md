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

## 1. Identity — decided and implemented (2026-09-01)

The owner settled the design. **A cookie per machine is correct, not a defect**:
this app is opened from one work machine per team member, so machine A → cookie
A is the intended mapping. Anonymous use is also deliberate — the site must be
usable, and show its value, before anyone has an account.

What was wrong, and what was decided:

1. **Contributors can create an account, self-service, in the UI** — username and
   password, reusing the hashing admins already use. Admin accounts stay
   CLI-only (`manage.py create-admin`); contributor sign-up is separate.
2. **Creating an account keeps everything made anonymously on that machine.**
   Claiming happens on the **first sign-in on that browser profile only**; a
   later sign-in by a different person on the same machine starts clean, so
   nobody can absorb a colleague's work.
3. **Signing in as admin must change who you are.** Signed in as `benito`, the
   sidebar, the feed and every attribution must say `benito`. Today they still
   say `Browser profile 5E8D · Anonymous`. This is a bug and is inconsistent
   with the rest of the design.
4. **Expertise routing lists only people with real accounts.** An anonymous hex
   profile must not be selectable as an expert, with an italic note on the page:
   *"Your expert is not showing up? Please check with him if he/she has created
   a full account first."*
5. **Warn before the loss.** Clearing cookies destroys an anonymous person's
   scratchpad and contributions with no recovery. Say so where it matters, and
   encourage an account as the way to keep it.

**Implemented in round 2.** All five points are in and proven:
contributors sign themselves up from the sidebar profile button; signing up
claims that browser's anonymous work once and locks the claim; signing in — as
anybody, admin or not — changes attribution everywhere; the expertise dropdown
lists only account holders and carries the owner's note verbatim; and the
profile panel warns, in red, exactly what a cookie clear destroys.
`admin_users` became `accounts` with an `is_admin` flag, so there is now one
identity system rather than two. Covered by seven API tests, including the two
that were watched failing first: the claim one-shot, and that signing out really
makes you anonymous again.

## 2. Nothing is editable or deletable by its author — decided (2026-09-01)

Your own contribution offers only `Helped me` (disabled, it is yours) and
`Details`. A typo is permanent. Documents have no delete endpoint and no delete
control, so a wrongly uploaded file is there forever.

**Owner's ruling: the author must be able to edit and delete their own work.**
Applies to contributions and to uploaded documents.

**Implemented in round 2.** Edit and Delete sit in the item's Details modal for
its author; documents have a Delete for their uploader. Deleting refuses when a
teammate has attached an answer or a correction, the same rule the question
delete always had. Deleting a document keeps excerpts other people shared from
it and clears their now-dangling source link. Editing reruns tagging, linking
and corroboration, so search finds the corrected text and not the old one.

## 3. "Endorse as expert" is offered to everyone — decided (2026-09-01)

**Owner's ruling: let anyone endorse.** Drop the server-side expert
restriction, so the button always does what it says. Expertise mapping stays an
admin action, and endorsement becomes evidence *for* it: add a tab beside the
expertise mapping listing the most-endorsed people, so an admin can map experts
based on what the team actually says. Not yet implemented.

Original description:

The button shows for anyone who is not the author, but the server requires the
endorser to be a mapped expert for a concept detected in that item, so most
clicks fail with `Only a mapped expert for this topic can endorse`. The client
has no signal for whether it will work. Fixing properly means the API telling the
client (e.g. a `can_endorse` flag).

## 4. Enter in the composer runs a search — decided (2026-09-01)

The composer is the single box on Home where you type, with Search / Ask /
Capture underneath it. Pressing Enter while writing runs a **search**. The text
survives, but the person expected to be typing.

**Owner's ruling: Enter must not search. Searching happens only when the person
clicks Search.**

**Implemented in round 2.** Enter inserts a newline like any other text box, the
hint text went with it, and the adversarial check that asserted the old
behaviour was inverted to assert the new one.

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

**Owner's ruling (2026-09-01): the graph stays at the top of the page.** The
open question is no longer whether it belongs there, only how to make it read
properly in the space it has. Agreed fixes: **(1) move concept labels below
their nodes**, as content nodes already do, and **(2) give the panel more
height** so fitting is not starved. A zoom floor is held in reserve if it still
looks cramped.

**Partly implemented in round 2, and the reserve fix was needed.** Labels moved
below their nodes. Raising the panel to 520px did fix the fit, but it pushed the
composer and the feed below the fold — worse, since the graph is not what people
came to the page for. Settled at 430px plus a floor on the zoom expressed as the
smallest readable label (9.5px): the map now renders labels at full size and
pans when it no longer fits, with "drag to see the rest" appearing in the panel
header when it does. Measured before and after: fill 34% → 64%, zoom 0.675 →
0.86, label 7.4px → 9.5px, composer still at 623px on a 950px viewport.

**Still open:** the layout is vertically oriented, so it can overflow the panel
height while leaving the width unused, and clusters can clip at the top and
bottom edges. That is a layout question, not a fit question.

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

# Fixed in round 2

## 20. The notification socket did not know who was signed in — fixed

Every REST route identified a person by their session; the notification
websocket resolved identity separately, from the browser-profile cookie. A
signed-in person's profile has no browser token at all, so their socket was
refused with a 1008 and they silently fell back to 30-second polling — and the
docstring above it still claimed the two agreed. Found by the round-2 audit.
Both now go through one resolver, `auth.profile_from_cookies`. Proven by
`test_the_notification_socket_knows_who_is_signed_in`, watched failing first.

## 21. Only one person could ever endorse a contribution — fixed

See item 3.

## 22. Copy that contradicted itself — fixed

Found by a technical-writer persona reading the screens as prose: the scratchpad
called itself "one long file" beside a "Create another scratchpad" button; its
save pill said "Autosaves as you type" and "Saved automatically" for one state;
its privacy chip said "PRIVATE TO THIS BROWSER PROFILE" even for account holders
whose scratchpad now survives a cookie clear; and a team-visible excerpt was
labelled "From your private scratchpad", which reads as a broken promise when
the item is sitting in the public feed. All reworded to say what is true.

---

# Newly found in round 2, needing the owner's decision

## 23. A stale bundle after a deploy shows a blank white page — open

Two personas hit a completely blank app with no text and no error: a hashed JS
chunk 404'd because the frontend was rebuilt while their tabs were open. In this
round that was the test harness's fault, but it is exactly what a deploy does to
anyone with the app already open. There is no error state at all — an empty
`<div id="app">`. *Recommendation: an index.html that detects a failed chunk
load and offers a reload, or filename-stable chunks. The owner may reasonably
decide a pilot does not need this.*

## 24. The best contributor could not be routed anything — open

The team lead went looking for the person who had written the most useful notes
and could not map her, because she never made an account. The explanatory note
told him exactly why, which he appreciated — but the effect is that expertise
routing skips the people who contribute most until somebody chases them to sign
up. *Recommendation: show no-account contributors in the dropdown greyed out
with the reason, so the admin sees who they are missing rather than an absence.*

## 25. Endorsing someone with no account leads nowhere — open

An admin endorsed a no-account contributor: the app said "Endorsed as an expert"
and recorded it correctly, and the Most-endorsed tab shows them with a NO
ACCOUNT badge — but they still cannot be routed anything. The endorsement is
honest, the dead end is not signposted at the moment of the click.

## 26. Nothing on the page says what the app is — open

A first-day engineer: *"the sidebar just says MDS / TEAM KNOWLEDGE with no
tagline or description anywhere on the home page — I had to infer what this was
from the search/ask/capture boxes"*. The empty state asks for a contribution
before it explains what the tool is for.

## 27. Two admins editing the same row are not told — open

One admin approved a link the other had just rejected, and only discovered it on
coming back. The curation table gives no signal that a row changed underneath
you. *Recommendation: at minimum, refresh the row and say so after a failed or
superseded action.*

## 28. Sharing a selection shows no preview of what will be published — open

A mis-drag published `uki Tanaka is a technical writer rev` to the whole team —
the app faithfully stored exactly what was selected, and four separate people
then read it as a rendering bug. Editing it afterwards is now possible (item 2),
but nothing shows you what you are about to share before you share it.

## 29. Quality findings from the round-2 audit, not yet applied — open

Each is real and each changes code the owner may want to review together:
`store.fail` was introduced as the single failure reporter and then not used by
15 call sites that inline its body; `endorse` is implemented twice with
different aftermaths (the same problem `store.markHelped` was created to end);
`add_admin` re-implements account creation that `auth.signup` already does, with
a different error message; the group dedup key format is written literally in
two places, so editing one silently breaks idempotency; and `items.helped`
decides whether to return a 400 by string-comparing a user-facing message
produced in another file.

---

# Newly found in round 1, needing the owner's decision

Each was classified by the read-only audit as changing behaviour, so it was
surfaced rather than acted on. Recommendations are given, not applied.

## 10. The admin's four curation actions sit behind native browser dialogs — fixed

Approve, Reject, Delete link and Delete concept each raise a `window.prompt` or
`window.confirm` before doing anything. They work correctly. But Chrome offers
"Prevent this page from creating additional dialogs" after repeated dialogs —
and an admin curating a list of suggested links will see many. Once ticked,
**every subsequent Approve/Reject silently does nothing**: no request, no toast,
no visual change. That is precisely the "control that silently does nothing"
root cause, reachable by an ordinary admin doing the page's main job.
**Fixed in round 2.** All six native `window.confirm`/`window.prompt` calls
(the four here plus deleting a question and naming a scratchpad) now use the
app's own `AskModal`, through one `useAsk()` helper. The contract of the dialogs
they replace is preserved exactly: cancelling resolves to `null`, confirming
resolves to the text, and an empty string is still a real answer for an optional
note. Every harness that answered a native dialog was taught the new door in the
same change.

## 11. Read paths have no error handling, so a failed load shows stale truth — partly fixed

Mutations catch and report; loaders do not (`AdminExpertiseView.loadData`,
`runPreview`, `loadState`, `removeMapping`; `ScratchpadView.load/find/createPad`;
`DocumentsView.load`; `KnowledgeGraph.refresh/loadFull/focus`;
`store.loadProfile`). **Mostly fixed in round 2**: `store.fail(e, fallback)` is the shared reporter,
and `AdminExpertiseView.loadData/runPreview/removeMapping`,
`ScratchpadView.load/find/createPad`, `DocumentsView.load`,
`KnowledgeGraph.refresh/loadFull/focus` and `store.loadProfile` all use it. The
round-2 audit found the list above had gone stale and named the ones still bare:
`App.saveName/signOut/openNotifications/markAllRead`, `HomeView.loadFeed`,
`QuestionCard.loadDetail`, `LeaderboardView.load` and `EvidenceModal`'s
`onMounted`. Those are what remains **open** here.

**The one case that actively misinformed is fixed.** `DocumentsView.open` was
verified by hand: uploading a document and then navigating *inside the app* to a
missing id left the previous document rendered under the new URL — the viewer
still read `a-real-file.txt` while the address bar read a dead id. It now clears
the pane and reports the error.

The existing adversarial check could not see this: it reached the bad id with
`goto()`, a full reload, where there is nothing stale to keep. `adversarial4.py`
now also navigates in-app, and that check was watched failing before the fix and
passing after — another instance of "a check too weak to fail".

## 12. Three more controls that do nothing and say nothing — fixed

`TESTING_LESSONS` §3 records this class as found and fixed, but the sweep missed
`saveName` (App.vue), `addAdmin` (AdminExpertiseView) and `runSearch`
(HomeView): each returns silently on empty input while their siblings in
`MapAdminPanel` all call `store.notify`. **Fixed in round 2** for Save name and Add admin, which now say what is missing.
`runSearch` on an empty box was deliberately left silent: the box is visibly
empty and the button sits directly under it, and Enter no longer searches, so a
toast there would fire on ordinary typing.

## 13. "View contribution" only closes the dialog — fixed

On Home, `SuccessModal`'s primary button emits `view`, which `HomeView` handles
identically to Close. The same button really does navigate from Documents and
Scratchpad, so one label means two things. **Fixed in round 2.** On Home it opens the contribution that was just created.
Where there is nothing to open — sharing a file with no text of its own — the
button is not offered at all rather than offered and inert.

## 14. Defining a concept never discovers links in content that already exists — fixed

Creating or renaming a concept calls `retag_everything` but never runs link
discovery; that only happens when someone posts something new. An admin who
defines two concepts that co-occur in fifty existing notes sees an empty map and
no explanation, while README promises a link is suggested "when team content
mentions two concepts together". **Fixed in round 2** exactly that way: `relationships.refresh_for_concept` runs
after a concept is created or renamed, scoped to that concept's own pairs.
Proven by a test watched failing first.

## 15. "Helped me" is implemented three times and they disagree — fixed

`HomeView` and `QuestionCard` optimistically increment the counter locally;
`ItemDetailModal` reloads from the server. The same click therefore produces
different numbers depending on which surface you clicked. **Fixed in round 2**: one `store.markHelped(item)` that applies the server's
`created` flag, used by all three surfaces. Note the round-2 audit found the
same pattern already reappearing in `endorse`, which is implemented twice — see
item 29.

## 16. Two Delete buttons in one table ask; the third does not — fixed

`deleteLink` and `deleteConcept` confirm first, `deleteType` did not. The server
refuses to delete a type still in use, so the blast radius was small, but the
inconsistency is what the next person copies.

**Fixed in round 2**: all three ask, through the same modal (item 10).

## 17. Dead surface that needs a migration to remove — decided (2026-09-01)

**Owner's ruling: backwards compatibility is not a concern while this is in
development.** Delete these with ordinary migrations; the owner will say when
production changes that. `verified` is still tied to item 1 and should be
decided with it.


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

## 18. Sharing a passage or an excerpt does not count toward "knowledge shared" — fixed

`shared_total` is computed and returned by the passage-share and scratchpad-share
endpoints but discarded by both callers, so the "that is the Nth piece of
knowledge you have shared" line appeared after Capture and not after the other
two ways of sharing.

**Fixed in round 2**: both callers now pass it through, so all three ways of
sharing say the same thing.

## 19. One sentence can produce a burst of suggested links — open

`MDS_COOCCURRENCE_MIN` defaults to 1, so a single contribution mentioning three
concepts creates a suggested link for every pair. An admin persona put it as:
*"my single throwaway capture note spawned five separate pairwise 'detected'
links, most of which are just noise from co-occurring in one sentence"*. The
README already documents the threshold as tunable; this is a note that the
default is what a curating admin actually experiences, not a defect.
