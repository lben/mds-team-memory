# Testing lessons for MDS Team Knowledge

A living record of what testing this app has actually taught us. Read it before
testing; update it after. It exists so the next pass starts from what we already
know instead of rediscovering it.

The standing bar is **not** "no errors and tests passed". It is: a person using
this should never have to say "what the hell". Absence of exceptions is the
floor. Coherence is the bar.

---

## 1. The three bars, in order

1. **It does not break.** No 5xx, no uncaught JS error, no hang, no data loss.
2. **It refuses badly-formed things clearly.** Every rejection says why, in
   words the person can act on. A control that does nothing and says nothing is
   a defect even when the data stays correct.
3. **It makes sense.** The story a real user and a real admin live through
   holds together: names mean something, actions offered are actions you can
   take, numbers add up, and nothing important is unrecoverable.

Bars 1 and 2 are largely met today. **Bar 3 has a hole at its centre** — see §5.

---

## 2. Tests that lied to us (the expensive lessons)

These are the specific ways a green check has been wrong here. Check for them
before trusting any new suite.

- **A check that asserts the aftermath without asserting the action.** We
  "tested deleting a document" for a whole pass. There is no delete endpoint;
  the request returned 405 and the check still passed, because it only asserted
  "the app still works afterwards". **Always assert the operation succeeded
  (status, or the row actually gone) before asserting its consequences.**
- **Counting feed rows to detect duplicates.** The feed folds identical
  contributions into one corroboration row with `group_size: 2`. Three
  double-submit checks passed while duplicates were being created. **Count
  stored items (sum `group_size`), never visible rows.**
- **A check too weak to fail.** "The phone layout does not scroll sideways"
  passed on a layout where the sidebar ate half the screen and the primary
  button was clipped off the edge. **Ask what the check would let through.**
- **Fixing without watching it fail.** Guards added by root-cause extension had
  their tests written afterwards, so nobody ever saw them go red. Two of them
  then appeared to pass without the guard — which is how the folded-row bug
  above was found. **For every fix, remove the fix, watch the check fail,
  restore it.**
- **Substring matches on the whole page.** Searching for a secret and grepping
  `.page` text hits the search banner echoing your own query. Scope assertions
  to the results region, and prefer asserting on API payloads for privacy.
- **Harness bugs that look like product bugs.** Repeatedly. Before changing
  product code, prove the failure by hand. Known traps: a row's name moves into
  an `<input>` in edit mode so `has_text` stops matching (hold the row id and
  use `#row-{id}`); dropdowns live inside the table you are counting, so option
  text inflates counts (read the API instead); Playwright's sync API cannot be
  called from worker threads (read cookies on the main thread, then race with
  plain HTTP).

---

## 3. What has actually broken here

Every real defect found so far came from one of four causes. Look for these
first in any new code.

- **No in-flight guard on a creating action.** Post answer, Propose correction,
  Share selection, Share passage all posted twice on a double-click. The draft
  or selection only clears after the server replies, so the control stays live.
  Fix: a `busy` ref that also disables the button, plus a `catch` that reports.
- **`?? ''` collapsing "cancelled" into "empty".** Cancelling the "why is this
  link wrong?" prompt performed the rejection anyway. Null and empty string are
  different answers whenever the input is optional.
- **A stale view that never resyncs.** A failed curation action left rows that
  the server no longer had, so the only thing that row could do was fail again.
  Fix in the shared failure handler, not per call site.
- **A control that silently does nothing.** Three "Add" buttons ignored an
  empty field with no message. Reads as broken.

**Root-cause rule (standing instruction):** when one of these is found, fix the
whole class at the shared choke point and check every other place the cause
exists — not just the reported instance.

---

## 4. Coverage: how we know we tested everything

Measure, do not assert. The method that works:

1. Run the suites with uvicorn's access log captured to a file.
2. Enumerate real endpoints from `app.openapi()["paths"]` — walking
   `app.routes` does not work, routers are wrapped in `_IncludedRouter`.
3. Normalise ids (`/[0-9a-f]{16,}` → `/{id}`) and diff.

This is what found that 22% of endpoints were untouched after two passes that
felt thorough, and that `GET /api/items/{id}/relationships` had no caller at all
(since deleted). **Reach every endpoint by clicking, not by calling it.** An
endpoint no UI can reach is dead surface — delete it rather than test it.

---

## 5. Coherence findings live in `OPEN_ITEMS.md`

Everything currently known-wrong or known-undecided — the identity problem, what
cannot be edited or deleted, the endorsement button, the composer's Enter key,
the graph's empty space, and the mobile decision — is tracked in `OPEN_ITEMS.md`,
which is the single source of truth for them. Do not copy them back here; one
list, one place to update.

**Never put that file's contents into a dogfooding subagent's prompt.** A persona
told what to expect will not discover it independently, and the "what the hell"
count stops measuring anything.

The lesson worth keeping *here* is the method: a walkthrough that records what a
person actually sees, read as prose, found a product-level hole that 276 passing
adversarial checks could not. Robustness and coherence are separate axes and need
separate passes.

---

## 6. The attack catalogue that has proven worth running

Cheap and has caught things: hostile text in every field (script tags, `onerror`
images, SQL, 6k characters, emoji/RTL/zero-width) verified as *inert DOM text*
rather than by string matching; FTS-breaking searches (`"unterminated`, bare
`AND OR NOT`, `*`, `((()))`, `NEAR/`); empty and whitespace-only on every
button; double-click on every creating control; back/forward/refresh mid-flow;
two tabs where one deletes what the other still shows; sign-out with a stale tab
open; forged and cleared cookies; deep links to deleted and never-existent ids;
wrong/oversized/mislabelled uploads; simultaneous requests from two identities;
cascade deletes with a stale page still holding the reference; abandoned
requests from hammered navigation; empty-database first-run states.

Always listen for `pageerror` and any HTTP ≥ 500 on every page in the run, and
fail the run on them independently of the assertions.

---

## 7. Facts about this app worth not rediscovering

- Documents cannot be deleted: no endpoint, no UI. Uploads are permanent.
- Endorsement requires the endorser to be a mapped expert for a concept
  detected in that item (403 otherwise).
- Corroboration folds identical contributions into one feed row.
- The feed/search UI escapes content; injected markup becomes inert text.
- Admin login and contributor identity are entirely separate systems.
- `manage.py create-admin` seeds admins; `reset-database` wipes and remigrates.
- The frontend must be rebuilt (`npm run build`) before any browser test — the
  server serves `frontend/dist`, which is gitignored.

---

*Last updated: 2026-08-31, after four adversarial passes (276 checks) plus a
coherence walkthrough.*
