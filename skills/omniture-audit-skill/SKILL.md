---
name: omniture-audit
description: "Audit a website's Adobe Analytics (Omniture) / Adobe Experience Cloud implementation end-to-end via a live managed browser session — anonymous first, then signed in — and produce a severity-ranked remediation report. Covers AppMeasurement (s.t / s.tl beacons, eVars, props, events, products string), AEP Web SDK (alloy.js, XDM, datastreams, identityMap), ECID / Visitor API and customer-ID sync, Adobe Target (A4T) and Audience Manager, consent (Adobe Opt-In / Web SDK setConsent / CMP), tag-manager library inspection for Adobe Launch (Tags / legacy DTM), Tealium iQ and GTM, third-party pixel inventory, and identity/join-key readiness for CRM attribution. Use when: auditing Adobe Analytics, reviewing an Omniture or AEP Web SDK implementation, checking Launch rules or Tealium tags, validating ECID or customer ID sync, investigating double-counted page views or missing eVars, migrating AppMeasurement to Web SDK, or pre-flighting Adobe data for attribution or Snowflake modelling. Triggers: Omniture audit, Adobe Analytics audit, audit Adobe, AppMeasurement audit, Web SDK audit, alloy audit, AEP tagging audit, Launch audit, Adobe Tags audit, DTM audit, Tealium audit, utag audit, ECID check, setCustomerIDs, A4T check, report suite audit, eVar audit, s.tl double counting, tag audit, analytics audit, tag manager audit."
---

# Adobe Analytics (Omniture) Implementation Audit

Audits a live site's Adobe Experience Cloud tagging in two browser sessions — anonymous, then
signed in — inspects the published tag-manager library, and writes a severity-ranked remediation
document plus a short executive brief.

**Why two sessions:** customer IDs (`setCustomerIDs`, `identityMap` authenticated entries) only
appear once authenticated; most variable-quality and consent defects are visible anonymously.
Anonymous first gives a clean baseline to diff against.

**Why library inspection matters:** beacons show what *did* fire. The Launch / Tealium / GTM
library shows what was *supposed* to fire, on which conditions, setting which variables — where
the actionable findings usually are (overlapping page-view rules, missing Clear Variables, vendor
tags hidden in custom code).

---

## Prerequisites

- `python3` (3.8+). All bundled scripts are standard-library only.
- Browser automation tools available (the `browser` subagent).
- Signed-in phase: **the user logs in themselves**. Never ask for or handle their password.

---

## Safety rules

Non-negotiable — the audit runs against **live production** on a site the user usually does not own.

| Rule | Reason |
| --- | --- |
| **Never place an order or submit payment.** Stop before any irreversible confirmation. | Real money, real fulfilment. |
| **Never enter personal data** — no addresses, phones, cards. | PII handling; mutating the account. |
| **Never change account settings** or sign the user out. | Destructive, hard to reverse. |
| **Never load or inject third-party JavaScript** (no Adobe Debugger / Experience Platform Debugger injection, no `_satellite.setDebug` side-loading of extra libs). Built-in browser APIs only. Reading `_satellite`, `s`, `alloy` and `utag` objects is fine. | An audit must not alter what it measures. |
| **Never switch Launch environments** (e.g. `localStorage` overrides to load a dev library) without explicit user approval. | Changes what the site loads for this browser. |
| **Never script into a cross-origin iframe.** Stop and report. | Security boundary. |
| **Redact plaintext PII in the report.** Field name, populated-or-not, shape only. | The report gets circulated. |

**Hash values are the exception:** record them in full — comparing them is the point of the
identity phase. Note whose account they belong to.

---

## Workflow

### Phase 1 — Gather target and confirm scope

Use `ask_user_question`:

1. **Site URL** (text, default `https://www.example.com`).
1. **Will you log in for the signed-in phase?** Yes / No — anonymous-only is valid but cannot
   assess customer-ID sync or the join key.
1. **Known context (optional):** report suite IDs, datastream ID, or whether a Web SDK
   migration is underway — speeds up reconciliation.

**⚠️ STOP**: Do not open a browser until the URL is confirmed.

### Phase 2 — Anonymous audit

Delegate to the `browser` subagent with the **Anonymous audit prompt** in
`references/audit-prompts.md`, URL substituted. Ask it to also save every Adobe request
(URL + POST body) to a text file or HAR for Phase 5.

Report back in two or three sentences: stack(s) found (AppMeasurement / Web SDK / both), TMS and
library IDs, report suites / datastreams, CMP presence, anything obviously broken.

### Phase 3 — Login handoff

**⚠️ MANDATORY STOPPING POINT.** Tell the user:

```
The browser is open on <site>. Please sign in there yourself — I won't ask for or handle
your credentials. Land on any page once you're in, then tell me to continue.
```

Wait for explicit confirmation. If "No" in Phase 1, skip to Phase 5 and mark identity unassessed.

### Phase 4 — Signed-in audit

Load `references/identity-join-reference.md`, then delegate with the **Signed-in audit prompt**.
Cart policy: **add an item, test `scAdd` / `commerce.productListAdds`, remove it, verify
`scRemove` / `commerce.productListRemovals`.** Report exactly what was added and whether removal succeeded.

### Phase 5 — Decode beacons

```bash
python3 <SKILL_DIR>/scripts/adobe_beacon_decoder.py <captured_requests.txt|.har>
```

Turns `/b/ss/` shortcodes (`v12`, `c7`, `pe=lnk_o`, `D=`, context data, products string), Web SDK
XDM payloads, Target and demdex calls into named variables, and flags PII, missing visitor IDs,
multi-suite tagging, purchase without `purchaseID`, and AppMeasurement + Web SDK double-firing.
See `references/adobe-beacon-reference.md` for the parameter glossary.

### Phase 6 — Tag-manager library inspection

Run for every library found in Phases 2 and 4. All are public — no browser needed.

```bash
python3 <SKILL_DIR>/scripts/tms_library_audit.py launch <launch-EN….min.js URL> [--fetch-custom]
python3 <SKILL_DIR>/scripts/tms_library_audit.py tealium <account>/<profile>/prod
python3 <SKILL_DIR>/scripts/gtm_container_audit.py GTM-XXXXXXX --all-tags --raw-dir ./gtm
```

Read `references/tms-inspection-reference.md` for interpretation — overlapping `s.t()` rules,
missing Clear Variables, AppMeasurement + Web SDK both sending, PII data elements, unresolved
`%Data Element%` report suites, Tealium tags with no load rule.

**The library is the only place some identity wiring is visible** — e.g. `setCustomerIDs` in a
rule that fires only on login or purchase. Run this before concluding anything about the join key.
Server-side forwarding (Event Forwarding, Tealium EventStream, sGTM) is **not public**; say so
and recommend an export rather than presenting absence of evidence as proof.

### Phase 7 — Hash recipe identification (only if hashes were found)

```bash
python3 <SKILL_DIR>/scripts/hash_probe.py --hash <observed_hash> [--hash <another>] \
    --email "<the account email>" [--extra-value <crm_id>]
```

Run locally via bash so the plaintext email stays out of the transcript.

### Phase 8 — Write the report

Ask for the output path (default `./omniture-audit-<domain>-<YYYY-MM-DD>.md`).

**⚠️ STOP**: Confirm the path before writing.

Build from `references/report-template.md`. Produce the full document **and** an executive brief
(final section + printed in chat).

---

## Severity rubric

| | Criteria |
| --- | --- |
| **P0** | Blocks an attribution or measurement build · corrupts revenue/conversion/page-view data · privacy/compliance exposure |
| **P1** | Materially distorts attribution, visitor counts or channel reporting |
| **P2** | Degrades data quality, or wastes spend (incl. billed secondary server calls) |
| **P3** | Hygiene, redundancy, inconsistency |

- **Inflated data outranks missing data.** Two rules both calling `s.t()`, or AppMeasurement and
  Web SDK both feeding one report suite, doubles page views and silently corrupts every rate. P0.
- **Variable leakage is inflation too.** `s.tl()` without Clear Variables re-sends the previous
  page's events and eVars — inflated conversions that look plausible.
- **Separate the findings that pay for themselves** — broken ad-platform matching, PII in eVars or
  context data — and flag them for parallel escalation.

---

## Reporting standards

**Distinguish observed from inferred.** AppMeasurement switches to POST above ~2,047 chars and Web
SDK always POSTs JSON — without captured bodies a hit looks empty. Processing rules, VISTA rules
and marketing-channel rules run **server-side in Adobe** and are invisible from the browser; a
variable "missing" from the beacon may be set by a processing rule from context data. Label such
conclusions inferred and name what would settle it (Adobe Debugger / Assurance, Admin Console
processing-rule export, Data Feed).

Never assert a `purchase` is absent without a completed transaction or library evidence.

**Lead with what the client can act on**, state what works, and **tag an owner per finding**
(web engineering, Launch/Tealium owner, Adobe admin for processing rules/report suites, AEP data
architect for XDM/datastreams, legal/privacy).

---

## Tools

| Script | Purpose |
| --- | --- |
| `scripts/adobe_beacon_decoder.py` | Decode AppMeasurement, Web SDK, Target and AAM requests from a URL list or HAR. `--json`, `--include-unknown`. |
| `scripts/tms_library_audit.py` | Parse public Launch / DTM / Tealium libraries: rules, data elements, extensions, report suites, load rules, vendor tags, flags. `--json`, `--raw-dir`, `--fetch-custom`, `--no-fetch-tags`. |
| `scripts/gtm_container_audit.py` | Public GTM container parser (from ga4-audit). Use `--all-tags --raw-dir` and grep the raw container for `AppMeasurement`/`alloy` when Adobe is deployed via GTM custom HTML/templates. |
| `scripts/hash_probe.py` | Identify which normalisation of a known email produces an observed hash (~20 recipes × 4 algorithms). |

Library parsing is structural, not a JS engine. Anything labelled heuristic is a lead to verify in
the TMS UI, not a finding.

---

## Stopping Points

- ✋ **Phase 1** — URL and signed-in intent confirmed before any browser opens
- ✋ **Phase 3** — user confirms they have signed in (mandatory; never self-authenticate)
- ✋ **Phase 8** — output path confirmed before writing

**Resume rule:** on confirmation, proceed to the next phase without re-asking.

---

## Output

1. **Remediation document** at the chosen path — scope/method, executive summary, severity-ranked
   findings with evidence/impact/fix/owner, variable map, identity reference, what works, tagging
   architecture, third-party inventory, pending verification, recommended sequence.
1. **Executive brief** — closing section and printed in chat.
1. **Session state disclosure** — cart items, beacons emitted by the audit (e.g. login events),
   so the client can filter audit artefacts from real behaviour.
