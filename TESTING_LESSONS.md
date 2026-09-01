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
- **Asserting a status code without asserting the content type.** A burst check
  hammered eight URLs and asserted `status == 200`. One of them,
  `/api/leaderboard`, does not exist; the SPA fallback answered it with
  index.html and a 200, so the check passed on HTML and would have passed with
  every real endpoint deleted. **Assert what came back, not just that something
  did.** (The fallback itself was the defect — see OPEN_ITEMS.)
- **Harness bugs that look like product bugs.** Repeatedly. Before changing
  product code, prove the failure by hand. Known traps: a row's name moves into
  an `<input>` in edit mode so `has_text` stops matching (hold the row id and
  use `#row-{id}`); dropdowns live inside the table you are counting, so option
  text inflates counts (read the API instead); Playwright's sync API cannot be
  called from worker threads (read cookies on the main thread, then race with
  plain HTTP).
- **Native dialogs are auto-dismissed, which silently disables the action
  behind them.** `Approve`, `Reject`, `Delete link` and `Delete concept` all
  raise a `window.prompt`/`window.confirm` first. Playwright dismisses dialogs
  by default, so `prompt()` returns `null`, the handler correctly aborts, and
  **no request is made** — which reads exactly like a dead button. A whole
  dogfooding round reported these four controls as broken with "zero POST/PATCH
  fired"; driving them by hand with `page.on("dialog", ...)` showed all four
  working. **Any driver must answer dialogs, and a report of "the button does
  nothing" must be re-run with a dialog handler before it is believed.**
- **A brand-new Chromium profile directory loses the cookies written in its
  first session.** A persona's identity therefore changes once, between their
  first and second script, and is stable forever after. Three personas
  independently reported this as the app losing their account. It is not: the
  profile cookie is `max-age` ten years and survives crashes. **Warm a fresh
  profile dir with one throwaway open-and-close before using it.**
- **"Nothing reaches it" has to include the tests.** The search response's
  `terms` field was deleted as dead surface on frontend-only evidence; a backend
  regression test used it as the only observation point for "function words must
  not produce results". Grep `backend/tests` and `e2e` too, and when only a test
  reaches a field, decide whether it is dead surface or a diagnostic before
  deleting it.
- **An unanswered in-app modal silently swallows every later click.** The four
  admin curation actions moved from `window.confirm`/`prompt` to the app's own
  modal. A driver that only knows how to answer *native* dialogs no longer sees
  anything to answer, the modal stays open, and the next click times out after
  30s against an invisible backdrop with no error. Two admin personas lost most
  of a session to this and reported it as the app hanging. The persona kit now
  has `p.confirm()` / `p.confirm(cancel=True)`; harnesses use `answer_ask(pg)`.
  **When a confirmation moves from the browser to the app, every driver has to
  be taught the new door on the same commit.**
- **`Control+a` selects nothing on macOS — the shortcut is `Meta+a`.** Two
  personas in two different rounds reported "Select any text… but the Share
  button stays disabled and nothing explains why". Measured: after `Control+a`
  the textarea selection is `[0, 0]`; after `Meta+a` it is the whole field and
  the button enables. The app was right both times. **Check the selection
  actually happened before believing the control ignored it.**
- **Text that looks corrupted may be faithfully stored.** Four personas
  independently reported an excerpt reading `uki Tanaka is a technical writer
  rev` as an off-by-one truncation bug. The scratchpad row held the full
  sentence and the excerpt row held exactly what had been selected — a bad
  drag-selection, published verbatim. **Read the stored row before diagnosing a
  rendering bug**; and note that one person's bad input becomes everybody's
  "obvious bug" when it is shared.
- **A failed frontend build makes a red/green run meaningless.** Removing a fix
  left an unused function, `vue-tsc` refused, `dist` was never rewritten, and
  the harness happily tested the *previous* bundle — so three checks that should
  have gone red stayed green and nearly convinced me they were too weak.
  **Assert `✓ built` before believing any browser run**, especially the run
  where you have deliberately broken something.
- **Reverting a fix needs the same assertion as applying one.** The first
  attempt at that revert silently matched nothing; only the second, with
  `assert s != before` on every replacement, actually changed the file.
- **`batch_alter_table` on SQLite recreates the table and drops its triggers.**
  Dropping a column from `knowledge_items` destroyed the three FTS sync triggers
  created moments earlier in the same migration, and search returned nothing for
  every new item. Do the column drops first and (re)create the index and its
  triggers afterwards, against the table that will actually exist.
- **A dark backdrop over a dark sidebar looks like a missing backdrop.** The
  modal overlay is `inset:0` and does cover the sidebar; navy-at-48%-opacity
  over navy simply does not visibly change. Read the CSS before filing a layout
  bug seen in a screenshot.

---

## 2b. A whole class of defect our harnesses structurally cannot see

Every assertion we write reads `inner_text()`, counts elements, or inspects JSON.
None of that **looks** at the app. An entire family of "what the hell" moments is
invisible to it, and the ones found so far were all caught by a human looking at
a screen:

- Avatar initials rendering as `NI` for "Benito", and sitting off-centre in their
  circle. The text assertion would have returned `"NI"` and passed.
- The phone layout, where a check that the page did not scroll sideways passed on
  a screen with the primary button clipped off the edge.
- The knowledge graph filling ~30% of a large dark panel — perfectly valid DOM.

Others in this class: overlapping or clipped elements, misaligned columns, poor
contrast, a control that looks disabled but is not (or the reverse), a modal
behind its backdrop, text overflowing its container, and screens that simply look
unfinished.

What looking found this round that no assertion could: concept labels in the
knowledge graph render at **7.4px** on screen and overflow their circles, because
the graph fits a roughly square layout into a wide, short letterbox panel and
zooms to 0.675 to do it. Two personas reported it as separate glitches — one read
"Payments" as "Paymenta", another saw a node as "a pin icon with no label at
all". Both were reading the same illegible text. Measure the rendered zoom and
the effective font size (`cy.zoom()` × the style's `font-size`) rather than
trusting the model values.

**The rule: anyone testing this must take screenshots and actually view them**,
not just assert on the DOM. Text checks confirm the words are right; only looking
confirms the app is right. Screenshot every significant screen and state —
including modals, error states, empty states and the graph at realistic size —
and read the images.

## 2c. Running personas against one shared instance

Worth the noise, but know what the noise looks like:

- **Rows move under you.** Another persona posts while you are clicking, the
  feed reorders, and a click aimed at a fixed position lands on nothing. Target
  by id or by text, never by position, and re-find the row after any wait.
- **Two admins editing the same thing is real and unguarded.** One approved a
  link the other had just rejected and only found out by coming back to it. The
  app says nothing when a row changes underneath you.
- **Never rebuild `frontend/dist` while a shared instance is being used.** The
  chunk filenames are hashed, so a rebuild 404s the bundle the open tabs are
  still asking for and the app goes blank white with no error state. Two
  personas reported the app as broken; it was the build. Results from a round
  where this happened are contaminated — rerun them.

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

- **One fact resolved in two places, only one of which was updated.** When
  identity moved from the browser cookie to the account session, every REST
  route changed — and the notification websocket, which resolved identity
  separately from the same cookie, did not. A signed-in person's socket was
  refused and they silently dropped to 30-second polling; the docstring above it
  still asserted the two agreed. **After changing what decides who someone is,
  grep for every place that answers that question, including the ones that are
  not routes.**

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

This is now implemented as `e2e/coverage.py`: run the harnesses, then
`.venv/bin/python e2e/coverage.py`. It reads `e2e/adv/access*.log`, ignores 404s
and 405s (a route that was never executed is not covered), and prints both what
was never reached and **what was requested but is not in the spec** — the second
list is how the `/api/leaderboard` phantom above was caught.

This is what found that 22% of endpoints were untouched after two passes that
felt thorough, and that `GET /api/items/{id}/relationships` had no caller at all
(since deleted). **Reach every endpoint by clicking, not by calling it.** An
endpoint no UI can reach is dead surface — delete it rather than test it.

Standing at 56/56 (100%) across `adversarial{,2,3,4}.py`.

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
- The e2e harnesses derive their own `ROOT` from `__file__`; they no longer
  hardcode one laptop's path, so they run from any checkout.
- The admin's four curation actions (approve, reject, delete link, delete
  concept) each raise a native dialog before doing anything. Cancelling is a
  deliberate, tested no-op.
- `e2e/sensecheck.py` screenshots every screen it walks into `e2e/adv/sense/`
  and prints the index. Reading its log is half the pass; the images are the
  other half.

---

*Last updated: 2026-09-01, after round 3 of the quality loop: 308 adversarial
checks, 46 API tests, 100% endpoint coverage, three read-only code audits and
eighteen fresh dogfooding personas across three shared instances.*
