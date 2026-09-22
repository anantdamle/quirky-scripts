# Report template

Skeleton for the remediation document. Adapt section numbering to what the audit actually found —
omit empty sections rather than writing "none observed" throughout.

The report is a client deliverable. Write it as a single standalone document: no references to
earlier drafts, no "revised from", no correction tables. If an earlier working conclusion changed
during the audit, state the final finding as a finding.

---

```markdown
# <Client> — GA4 Implementation Audit & Prioritised Remediation

**Client:** <name> (<url>) — <one line on the business: channels, footprint>
**Audit date:** <date> — anonymous session, then signed-in session
**Auditor:** <name>, <org>
**Status:** <what is complete, what remains unverified, pointer to the pending section>

> **Note on evidence.** The signed-in session used <whose> account. Hash values and identifiers
> quoted below are that account's own records, reproduced because they are the evidence for the
> defects. No plaintext email address appears in this document. Strip §<n> identifier values if this
> document is circulated widely.

---

## 1. Scope & method

Read-only inspection of live production sessions. State plainly what was NOT done: no order placed,
no payment submitted, no profile data changed, no third-party JavaScript loaded, no cross-origin
iframe scripted into.

**Session 1 — anonymous.** <journey>
**Session 2 — signed in.** <journey>, plus container inspection of <container IDs and tag counts>.

**Out of scope:** <each item with the reason>

**A note on GA4 POST hits, because it limits what browser inspection can prove.** GA4 switches from
GET to POST for large payloads, moving the event name (`en`) and all parameters into the request
body. Those bodies cannot be read from the page: `gtag.js` captures its `fetch` reference at load, so
any hook installed afterwards never observes its sends. A small number of conclusions below therefore
rest on hit-count reconciliation, `_s` sequence ordering and container logic rather than on reading
`en` directly. Each is flagged where it occurs, and §<n> lists them together. GA4 DebugView or GTM
Preview resolves all of them with container access.

---

## 2. Executive summary

Open with an even-handed sentence on overall quality, then where the problems concentrate.

**The most consequential finding:** <the one thing that changes what the client should do>

Then the remaining headline findings as bullets — one line each, no elaboration.

If the ecommerce funnel is broken in specific places, draw it, because it communicates faster than
prose:

`view_item` → **(no `add_to_cart`)** → `view_cart` → `begin_checkout` → **(no `add_shipping_info`)**
→ `purchase` **×2**

**Counts:** N × P0, N × P1, N × P2, N × P3.

---

## 3. Prioritised gap register

Severity: **P0** blocks the build, corrupts revenue data, or is a privacy exposure · **P1**
materially distorts attribution · **P2** degrades data quality or wastes spend · **P3** hygiene.

Owner key: <the actual team names — web engineering, checkout engineering, GTM owner, server
container owner, legal/privacy, CRM>

### P0 — Blocking

Give each P0 its own subsection with room to show the evidence. A table cannot carry the reasoning,
and P0s are the findings that need to survive being forwarded to someone who was not in the room.

**P0-N · <finding> · Owner: <team>**

<What was observed, with exact parameter names, values and where.>

<Container evidence, if it narrows the diagnosis.>

*Impact:* <business consequence, not a restatement of the defect.>

*Fix:* <specific and actionable. If the fix is a config change, say so — it changes the timeline.>

*Evidence basis:* <only when inferred rather than directly observed: what supports it, and what
would settle it.>

### P1 — Materially distorts attribution

Table: ID | Finding | Evidence | Impact | Fix | Owner

### P2 — Data quality / wasted spend

Table: ID | Finding | Impact | Fix | Owner

### P3 — Hygiene

Table: ID | Finding | Fix | Owner

---

## 4. Identity layer — reference

Every person-level identifier observable in the browser, in one table: Identifier | Value | Where |
Assessment. This is the raw material for any join design — having it in one place prevents the wrong
column being chosen later.

---

## 5. What is working correctly

Not optional. It is honest, it shows the audit was even-handed, and it stops a competent
implementation with specific faults reading as broadly broken. Name the events that fire correctly,
the settings that are right, and any design decisions worth preserving.

---

## 6. Tagging architecture reference

Table of components: GA4 properties, GTM containers with versions and tag counts, server-side
endpoint, deprecated tags still live, ad platforms.

Then a paragraph on the architecture — client-side vs server-side, and the boundaries between stacks.
Name which findings trace back to a stack boundary; it turns a list of defects into one root cause.

---

## 7. Third-party tag inventory / ad-pixel PII transmission

Table: Platform | ID | Hashed PII sent? | Detail. Follow with a net-position line — which platforms
are receiving a correct identifier and which are not.

---

## 8. Pending verification

Group by who can answer it and keep it as checkboxes the client can work through.

### 8a. Server-side container configuration
### 8b. The data export (BigQuery / warehouse)
### 8c. Downstream systems (CRM, POS, app, loyalty)
### 8d. Not verifiable from the client

For each item in 8d, say what it is, why it could not be verified, what the supporting evidence is,
and what would settle it. Also state which findings do NOT depend on it — that keeps an honest
limitation from undermining the conclusions that stand on their own.

---

## 9. App measurement

Only if there is a mobile app. Cover: MMP/deep-link SDK presence, app-store IDs, whether store links
carry attribution parameters, and any signal that the app reuses web tagging (WebView detection
parameters, user-agent-based blocking rules in the container). State the consequence for whether app
conversions can come from analytics at all.

---

## 10. Recommended sequence

Numbered, ordered by a blend of impact and effort — not by severity alone. Distinguish config changes
from development work, because it changes what the client can do this week. Call out anything that
should run in parallel rather than queue, particularly privacy findings and live media waste, which
have their own owners and their own urgency.

---

## Appendix — session state left behind

What the audit itself changed or emitted: cart items added and whether they were removed, events
fired by the audit (including any the site fired in response, such as a sign-in event), and
confirmation of what was not done. The client needs this to distinguish audit artefacts from real
user behaviour in their data.

---

## Executive brief

Five or six lines, written to be forwarded without the rest of the document.

- **The headline:** <the single most consequential finding and what it means>
- **Fix this week:** <config-level changes with disproportionate impact>
- **Escalate in parallel:** <privacy or media-waste findings with different owners>
- **Blocked on you:** <what the client must provide>
- **Bottom line:** <one sentence on whether the goal is achievable and what it depends on>
```

---

## Writing notes

**Separate observed from inferred, every time.** An audit that overstates certainty is worse than one
with acknowledged gaps, because it will be found out and the rest becomes suspect.

**Impact must be a business consequence.** "`add_to_cart` does not fire" is the defect. "No
cart-abandonment analysis and no mid-funnel intent signal" is the impact. Clients prioritise on
impact.

**Never assert a `purchase` event is absent** without either a completed transaction or container
evidence. Inference from an incomplete funnel is insufficient — a tag can exist, be correctly
triggered, and simply not have been observed.

**Tag an owner on every finding.** Findings with no owner do not get fixed. Where a finding needs two
teams (typically an engineering fix plus a privacy review), name both.
