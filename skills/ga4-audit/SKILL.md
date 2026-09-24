---
name: ga4-audit
description: "Audit a website's Google Analytics 4 implementation end-to-end via a live managed browser session — anonymous first, then signed in — and produce a severity-ranked remediation report. Covers GA4 event coverage and parameter quality, ecommerce funnel integrity, Consent Mode v2 / CMP, GTM container inspection (client-side and server-side tagging), third-party pixel inventory, and identity/join-key readiness for CRM attribution. Use when: auditing GA4, reviewing analytics implementation, checking GA4 tracking, validating user_id or hashed-email join keys, investigating why a GA4 event is missing, assessing GA4 data for attribution or BigQuery/Snowflake modelling, or pre-flighting a client's tagging before an attribution build. Triggers: GA4 audit, audit GA4, analytics audit, tag audit, tagging audit, check GA4 implementation, GA4 gaps, GA4 implementation review, why is add_to_cart missing, GA4 user_id, hashed email join key, GA4 attribution readiness, sGTM audit, GTM container audit, measurement audit, pixel audit, consent mode check."
---

# GA4 Implementation Audit

Audits a live site's GA4 implementation in two browser sessions — anonymous, then signed in — and
writes a severity-ranked remediation document plus a short executive brief.

**Why two sessions:** identity only appears once authenticated, but most data-quality and consent
defects are visible anonymously and are easier to read without a logged-in user's PII on screen.
Running anonymous first also gives a clean baseline to diff the signed-in state against.

**Why GTM container inspection matters:** network observation shows what *did* fire. It can never
show why something *didn't*. Parsing the container reveals whether a tag is missing, paused,
mis-triggered, blocked, or duplicated — which is usually where the actionable findings are.

---

## Prerequisites

- `python3` (3.8+). Both bundled scripts are standard-library only — no `pip install`, no `uv`.
- Browser automation tools available (the `browser` subagent).
- For the signed-in phase: **the user must have working credentials** for the target site and log in
  themselves. Never ask for or handle their password.

---

## Safety rules

These are non-negotiable because the audit runs against **live production** on a site the user
usually does not own.

| Rule | Reason |
|---|---|
| **Never place an order or submit payment.** Walk checkout only as far as it goes without submitting. Stop before any irreversible confirmation. | Real money, real fulfilment. |
| **Never enter personal data** — no addresses, phone numbers, card details. Stop at validation errors instead. | Mutating the user's account, and PII handling. |
| **Never change account settings**, saved addresses or cards. Never sign the user out. | Destructive and hard to reverse. |
| **Never load or inject third-party JavaScript.** Use built-in browser APIs only. | An audit must not alter what it measures, and injected code is unverifiable. |
| **Never script into a cross-origin iframe.** If an action would target a frame on a different domain (payment frames especially), stop and report. | Security boundary; payment frames are off limits. |
| **Redact plaintext PII in the report** — email addresses, phone numbers, names, addresses. Report field name, populated-or-not, and shape only. | The report gets circulated. |

**Hash values are the exception:** record them in full. Comparing hashes is the entire point of the
identity phase, and a hash cannot be verified from a description. Note in the report whose account
they belong to, since a hashed email is still a pseudonymous identifier of a real person.

---

## Workflow

### Phase 1 — Gather target and confirm scope

Use `ask_user_question` for the site address and the signed-in intent. Suggest a default rather than
asking the user to type from scratch.

Ask:
1. **Portal URL** (text input, e.g. `https://www.example.com`).
2. **Will you log in for the signed-in phase?** Yes / No — anonymous-only is a valid shorter run,
   but say plainly that it cannot assess the join key, which is usually the point.

**⚠️ STOP**: Do not open a browser until the URL is confirmed.

### Phase 2 — Anonymous audit

Delegate to the `browser` subagent using the **Anonymous audit prompt** in
`references/audit-prompts.md`, with the confirmed URL substituted in.

Delegating rather than driving the browser inline protects the main context window — these sessions
generate a lot of network data. The managed browser persists between subagent calls, which is what
makes the login handoff in Phase 3 work.

Report to the user, in two or three sentences, what came back: GA4 property IDs found, whether a CMP
exists, and anything already obviously broken.

### Phase 3 — Login handoff

**⚠️ MANDATORY STOPPING POINT.** The browser is already open on the target site. Tell the user:

```
The browser is open on <site>. Please sign in there yourself — I won't ask for or handle
your credentials. Land on any page once you're in, then tell me to continue.
```

Wait for explicit confirmation that they are signed in. Do not attempt to log in, do not
guess at credentials, and do not proceed on a timer.

If the user said "No" in Phase 1, skip to Phase 4 and mark the identity findings as unassessed.

### Phase 4 — Signed-in audit

Delegate to the `browser` subagent using the **Signed-in audit prompt** in
`references/audit-prompts.md`. Load `references/identity-join-reference.md` first — it defines the
identity checks and the hash-comparison logic that prompt depends on.

The signed-in pass carries the cart test. Policy: **add an item, test the event, then remove it and
verify `remove_from_cart` fired.** This tests two events instead of one and leaves the user's cart as
it was found. Report exactly what was added and whether removal succeeded.

### Phase 5 — GTM container inspection

Run `scripts/gtm_container_audit.py` for every GTM container ID found in Phases 2 and 4. Containers
are fetched over plain HTTP from Google's public endpoint — no browser needed, and cheaper.

```bash
python3 <SKILL_DIR>/scripts/gtm_container_audit.py GTM-XXXXXXX [GTM-YYYYYYY ...] \
    --observed-id G-XXXXXXXXXX
```

Pass every measurement ID the browser audit saw via `--observed-id`. The container describes how the
ID is chosen but not which branch is live, so without this hint environment lookups often cannot be
resolved — and unresolved IDs make the double-fire analysis provisional, since tags targeting
different properties may be grouped together.

Read `references/gtm-inspection-reference.md` for how to interpret the output — particularly
duplicate event tags, blocking rules, dynamic event-name macros that silently drop unmapped events,
and why measurement IDs must be resolved before any duplicate is reported.

**The container is the only place some identity wiring is visible.** GA4 user properties and
parameters appear only on hits for the events their tag fires on, and `user_id` is normally set once
on the config tag rather than per event. So a browser session that never completes a purchase cannot
see identity wired onto the purchase tag. Run this step before concluding anything about the join
key — on one real audit the container showed `emailHash` and a CRM ID configured as user properties
that the wire pass had not observed at all, which changed the conclusion.

**Set expectations here.** A first-party server-side GTM endpoint (`sgtm.*`, `a.*`, `metrics.*` on
the site's own domain) serves a *proxy* of the client-side container. The **server container's own
configuration is not publicly fetchable for any site.** If identity appears to be injected
server-side, say so as an unresolved question and recommend requesting a container export — do not
present absence of client-side evidence as proof of absence.

### Phase 6 — Hash recipe identification (only if identity hashes were found)

If the signed-in pass found one or more hashed identifiers, identify what they are hashes *of*:

```bash
python3 <SKILL_DIR>/scripts/hash_probe.py --hash <observed_hash> [--hash <another>] \
    --email "<the account email>" [--extra-value <crm_id>]
```

Run this **locally via bash**, not in the browser and not by pasting the email into the transcript —
the script keeps the plaintext email out of conversation context and prints only which normalisation
matched.

Multiple hashes that disagree is a major finding, not a curiosity: it means downstream systems
joining on "the email hash" will split one person into several identities, and any ad platform
receiving the wrong one is failing advanced matching silently.

### Phase 7 — Write the report

Ask for the output path (text input, default `./ga4-audit-<domain>-<YYYY-MM-DD>.md`).

**⚠️ STOP**: Confirm the path before writing.

Build the document from `references/report-template.md`. Produce **both**:
1. The full severity-ranked remediation document at the confirmed path.
2. A short executive brief — append it as a final section, and also print the top findings in chat
   so the user can paste them into email or Slack without opening the file.

---

## Severity rubric

Assign severity by consequence, not by how unusual the defect is.

| | Criteria |
|---|---|
| **P0** | Blocks an attribution or measurement build · corrupts revenue or conversion data · is a privacy/compliance exposure |
| **P1** | Materially distorts attribution or channel reporting |
| **P2** | Degrades data quality, or wastes media spend |
| **P3** | Hygiene, redundancy, inconsistency |

Two calibration notes worth applying deliberately:

- **Inflated data outranks missing data.** A missing event reads as an obvious zero. A duplicated
  `purchase` tag doubles revenue, looks entirely plausible, and silently corrupts every ROAS and AOV
  figure downstream. Rank duplication as P0.
- **Separate the findings that pay for themselves.** Broken advanced matching on ad platforms, or
  PII flowing to a processor, are live problems independent of any analytics project. Flag them for
  parallel escalation so they do not queue behind a modelling roadmap.

---

## Reporting standards

**Distinguish observed from inferred, every time.** GA4 switches from GET to POST for large
payloads, which moves the event name (`en`) and all event parameters into the request body — and
those bodies cannot be read from the page, because `gtag.js` captures its `fetch` reference at load
and any hook installed afterwards never sees its sends. So some conclusions necessarily rest on
hit-count reconciliation, `_s` sequence ordering and container logic. Label those as inferred, state
the supporting evidence, and name what would settle it (GA4 DebugView or GTM Preview, both of which
need container access). An audit that overstates its certainty is worse than one with gaps.

Never assert a `purchase` event is absent without either a completed transaction or container
evidence. Inference from an incomplete funnel is not sufficient — a tag can exist, be correctly
triggered, and still not have been observed.

**Lead with what the client can act on.** State what is working correctly as well: it is honest, it
establishes that the audit was even-handed, and it prevents a competent implementation with specific
faults being read as broadly broken.

**Tag an owner per finding.** Route by the team that can actually fix it — web/storefront
engineering, legacy/checkout engineering, the GTM owner, the server-container owner, legal/privacy.
Findings with no owner do not get fixed.

---

## Tools

### `scripts/gtm_container_audit.py`

Fetches public GTM containers and resolves tags against triggers.

**Usage:** `python3 <SKILL_DIR>/scripts/gtm_container_audit.py GTM-XXXXXXX [more ...]`

**Options:** `--json` for machine-readable output · `--all-tags` to include non-GA4 tags ·
`--raw-dir DIR` to save fetched containers for manual inspection · `--observed-id G-XXXXXXX`
(repeatable) to pass measurement IDs seen on the wire.

**Pass `--observed-id` whenever the browser audit found the measurement IDs.** Containers route the
ID through environment lookups whose conventions differ — one site keys production explicitly and
defaults to UAT, another lists only its non-production hostnames and lets production fall through
to the default. Neither can be assumed, so where the container is ambiguous the script reports it
rather than guessing, and one observed ID collapses the ambiguity. Without it you may get every tag
unresolved, which makes the double-fire grouping provisional.

**Reports:** container version and tag counts · the GA4 properties targeted, with resolved
measurement IDs · every GA4 event tag with its event name, target property, triggering dataLayer
events, firing triggers and blocking rules · **double-fire risk** (the double-count check) ·
dynamic event-name macros and the mappings they contain · which standard ecommerce events have no
tag · plus the targeted checks below.

**Targeted checks**, each automating something first found by hand on a real audit:

| Check | What it catches |
|---|---|
| **Identity wiring** | Whether GA4 `user_id` is configured — in the **config tag** (where it is normally set once) as well as event tags. Plus every configured user property and identity-bearing parameter. **This is the single most valuable check**: user properties only appear on hits for the events their tag fires on, so identity wired onto a `purchase` tag is invisible to any browser session that does not complete a purchase. |
| **PII parameters** | Parameters whose *names* suggest personal data (email, phone, address, CRM/contact ID), excluding names that say hashed. Grouped by parameter set, because a shared settings variable is one decision, not one finding per tag. |
| **Property inventory** | Every GA4 property reachable, and how each is selected. Flags locale/region splits, where a single "production" property is legitimately several. |
| **App suppression** | GA4 events blocked for in-app/WebView traffic. Separates a container-wide exclusion from per-event suppression. Reveals that the app renders these pages, and which conversions cannot come from analytics for the app. |
| **Ecommerce data path** | Ecommerce tags sending neither the ecommerce object nor the ecommerce parameters individually — these fire but produce events with no items or revenue. |
| **Legacy analytics** | Universal Analytics tags *and* UA property IDs referenced anywhere. If UA IDs appear but no UA tag exists, the tag is hardcoded in the page template, so editing GTM will not remove it. |
| **Event naming** | Whether the container listens for GA4-native ecommerce names, namespaced/legacy ones, or both — the silent failure mode when two stacks each push only one convention. |
| **Consent configuration** | Any CMP or Consent Mode tag. Absence is a strong hint, not proof — a CMP can load outside GTM. |

With two or more containers it also runs cross-container checks: ecommerce coverage per container
(distinguishing a genuine gap from a stack-boundary artefact), event-vocabulary mismatch between
stacks, and which properties each container feeds.

**Read the reasoning in the output, not just the flags.** Several checks deliberately report
"possible" rather than "confirmed", and say what would settle it. Two showed up as false positives
during development — `sendEcommerceData: false` is legitimate when parameters are mapped by hand,
and a dozen tags sharing an event name is normal design — so the script now distinguishes both
cases. Treat anything it labels provisional as a lead to verify, not a finding to report.

### `scripts/hash_probe.py`

Identifies which normalisation of a known value produces an observed hash.

**Usage:** `python3 <SKILL_DIR>/scripts/hash_probe.py --hash <hex> --email "<addr>"`

**Options:** `--extra-value V` to test additional candidate pre-images (CRM IDs, user IDs) ·
`--list-recipes` to show what it tests.

**Tests:** SHA-256/SHA-1/MD5/SHA-512-truncated across ~20 normalisations — as-is, lowercased,
uppercased, trimmed, whitespace variants, Gmail dot-stripping, local part only, domain only,
double-hashing, and concatenations with any `--extra-value`. Prints the matching recipe, or reports
no match — which is itself a finding, implying a salt or a different pre-image and requiring
server-side code access to resolve.

---

## Stopping Points

- ✋ **Phase 1** — URL and signed-in intent confirmed before any browser opens
- ✋ **Phase 3** — user confirms they have signed in (mandatory; never self-authenticate)
- ✋ **Phase 7** — output path confirmed before writing

**Resume rule:** on confirmation, proceed to the next phase without re-asking.

---

## Output

1. **Remediation document** at the user's chosen path — scope and method, executive summary,
   severity-ranked findings with evidence/impact/fix/owner, identity-layer reference table, what is
   working correctly, tagging architecture, third-party inventory, pending verification, recommended
   sequence.
2. **Executive brief** — top findings, as a closing section and printed in chat.
3. **Session state disclosure** — anything left behind (cart items, events fired by the audit
   itself), so the client can distinguish audit artefacts from real user behaviour in their data.
