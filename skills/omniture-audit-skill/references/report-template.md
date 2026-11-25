# Report template

Skeleton for the remediation document. Omit empty sections rather than writing
"none observed". Write it as a standalone client deliverable — no references to
earlier drafts or corrections.

---

```markdown
# <Client> — Adobe Analytics Implementation Audit & Prioritised Remediation

**Client:** <name> (<url>) — <one line on the business> **Audit date:** <date> —
anonymous session, then signed-in session **Auditor:** <name>, <org> **Status:**
<complete / unverified items, pointer to pending section>

> **Note on evidence.** The signed-in session used <whose> account. Hashes,
> ECIDs and customer IDs quoted are that account's own records, reproduced as
> evidence. No plaintext email appears. Strip §<n> identifier values before wide
> circulation.

---

## 1. Scope & method

Read-only inspection of live production. Not done: no order, no payment, no
profile changes, no third-party JavaScript or debugger injection, no Launch
environment overrides, no cross-origin iframe scripting.

**Session 1 — anonymous.** <journey> **Session 2 — signed in.** <journey>
**Library inspection.** <Launch property/build date, Tealium profile/version,
GTM IDs> **Out of scope:** <items with reasons>

**What browser inspection cannot prove.** AppMeasurement switches to POST for
long hits and Web SDK always POSTs JSON; processing rules, VISTA,
marketing-channel rules, datastream mapping and Event Forwarding all run inside
Adobe. Conclusions depending on these are flagged as inferred and listed in
§<n>; Adobe Debugger / Assurance, an Admin Console export or a Data Feed sample
resolves them.

---

## 2. Executive summary

Even-handed opening sentence, then where problems concentrate.

**The most consequential finding:** <…>

Remaining headline findings as one-line bullets.

Funnel, if broken in specific places:

`prodView` → **(no `scAdd`)** → `scView` → `scCheckout` → `purchase` **×2 (no
purchaseID)**

**Counts:** N × P0, N × P1, N × P2, N × P3.

---

## 3. Prioritised gap register

Severity: **P0** blocks the build, corrupts revenue/page-view data, or privacy
exposure · **P1** distorts attribution/visitor counts · **P2** data quality or
wasted spend · **P3** hygiene.

Owner key: web engineering, Launch/Tealium owner, Adobe admin (report suites,
processing rules), AEP data architect (XDM, datastreams, identity namespaces),
legal/privacy, CRM.

### P0 — Blocking

**P0-N · <finding> · Owner: <team>**

<Observation with exact variables, values and pages.> <Library evidence (rule
names, tag uids) if it narrows the diagnosis.>

_Impact:_ <business consequence> _Fix:_ <specific; say if config-only> _Evidence
basis:_ <only if inferred — support and what would settle it>

### P1 — Table: ID | Finding | Evidence | Impact | Fix | Owner

### P2 — Table: ID | Finding | Impact | Fix | Owner

### P3 — Table: ID | Finding | Fix | Owner

---

## 4. Variable map

Every eVar / prop / event / list var / context-data key / XDM field observed:
Variable | Example value (redacted) | Where set (rule/tag) | Pages | Notes
(leakage, PII, D= copy, unused).

## 5. Identity layer — reference

Identifier | Value | Where | Assessment (ECID, legacy IDs, customer IDs +
authState, identityMap namespaces, identity eVars, hashes with recipe, CRM IDs).

## 6. What is working correctly

Not optional. Name what fires correctly and design decisions worth keeping.

## 7. Tagging architecture reference

Table: TMS(s) with IDs/build dates, Adobe libraries and versions
(AppMeasurement, alloy, Visitor, at.js, DIL), report suites per environment,
datastreams, collection hosts (first-party CNAME?), org ID, deprecated
components. Then a paragraph on stack boundaries and migration state
(AppMeasurement ↔ Web SDK) and which findings trace to them.

## 8. Target / Audience Manager / A4T

Personalisation setup, flicker handling, A4T `sdid` alignment, AAM ID syncs and
signals.

## 9. Third-party tag inventory / ad-pixel PII transmission

Platform | ID | Loaded via (Launch rule / Tealium uid / GTM / hardcoded) |
Hashed PII? | Detail.

## 10. Pending verification

- 10a. Adobe Admin: processing rules, VISTA, marketing channels, report-suite
  settings
- 10b. AEP: datastream mappings, schemas, identity namespaces, Event Forwarding
- 10c. Data Feed / warehouse export
- 10d. Not verifiable from the client — what, why, supporting evidence, what
  settles it, and which findings do NOT depend on it

## 11. App measurement

Only if a mobile app exists: Adobe Mobile SDK / AEP Mobile hints, MMPs, WebView
detection in the TMS.

## 12. Recommended sequence

Numbered by impact × effort; separate TMS config changes from development work;
call out parallel tracks (privacy, media waste).

---

## Appendix — session state left behind

Cart items added/removed, beacons emitted by the audit (login events, link
clicks), and what was not done.

---

## Executive brief

- **The headline:** <…>
- **Fix this week:** <TMS config changes>
- **Escalate in parallel:** <privacy / media-waste>
- **Blocked on you:** <exports or access needed>
- **Bottom line:** <is the goal achievable and on what>
```

---

## Writing notes

- **Observed vs inferred, every time.** Overstated certainty gets found out.
- **Impact is a business consequence.** "Two `s.t()` rules fire on PDPs" is the
  defect; "product page views and every per-visit rate are overstated ~2×" is
  the impact.
- **Never assert `purchase` is absent** without a completed order or library
  evidence.
- **Owner on every finding**; name both teams where two are needed.
