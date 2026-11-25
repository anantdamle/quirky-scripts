# Tag-manager library inspection reference

How to interpret `tms_library_audit.py` (Launch, DTM, Tealium) and `gtm_container_audit.py` output
for an Adobe implementation. All three libraries are public because the browser must download them.

---

## Adobe Launch / Tags

**Find the library:** `script[src*="assets.adobedtm.com"]` (`launch-EN<hash>.min.js` = production;
`-development` / `-staging` suffixes are other environments) or a self-hosted copy. Read
`_satellite.buildInfo` for build date and environment.

| Output | How to read it |
| --- | --- |
| `build.environment` ≠ production on the live site | Dev/staging library on prod — P1; unreviewed changes are live |
| Extensions list | Which Adobe stacks are installed: `adobe-analytics` (AppMeasurement), `adobe-alloy` (Web SDK), `adobe-mcid` (Visitor API), `adobe-target`/`-v2`, `adobe-audience-manager`. Third-party extensions show governed vendors. |
| `report_suites` | Per-environment report suites from the AA extension. `%Data Element%` values mean the suite is chosen at runtime — reconcile against the beacon path. |
| Rules → `aa_call_type` | `s.t` = page view beacon, `s.tl` = link beacon. |
| **>1 rule sending `s.t`** | Verify their events/conditions are mutually exclusive (e.g. Library Loaded vs Direct Call `pageView` on an SPA). If two can fire on one page → double page views (P0). Confirm on the wire with the decoder. |
| No **Clear Variables** action anywhere | eVars/events persist on the `s` object between beacons; `s.tl` calls re-send previous events → inflated conversions. Very common on SPAs. |
| AppMeasurement **and** Web SDK rules both sending | Migration in progress. Fine if they feed different suites/datastreams; double counting if the datastream forwards to the same report suite. |
| Web SDK `eventType` | Check page views use `web.webpagedetails.pageViews` and links `web.webinteraction.linkClicks`; commerce uses `commerce.*`. A page-view-only event type on clicks inflates page views. |
| `data_elements[].pii_name` | Names suggesting plaintext PII — check whether they are mapped to eVars/XDM or pixels. |
| Custom-code rules | Vendor tags inside custom code bypass extension governance and consent conditions. `--fetch-custom` pulls external code files and scans them for vendors and eVars. |
| `data_elements_used` per rule | Which `%…%` values feed each rule — trace identity elements (`%Customer ID%`, `%Hashed Email%`) to the rules and actions that send them. |

**Consent in Launch:** look for rule conditions on a CMP data element / OneTrust groups, the
privacy setting (`_satellite.getVar` / Core "default privacy"), Visitor API `optInOptions`, and
Web SDK `defaultConsent: pending` + a `setConsent` rule. Absent all of these on an opt-in market =
P0 lead; confirm pre-consent firing on the wire.

**Event Forwarding** (Launch server-side) is a separate property and **not public**.

## Legacy DTM

`satelliteLib-*.js` / `_satellite.dtm`. DTM is sunset — P2 hygiene unless it is also the reason
for other defects (old AppMeasurement, no ECID). Page-load rules, event-based rules and direct-call
rules map to the same overlap and leakage checks.

---

## Tealium iQ

**Find the profile:** `tags.tiqcdn.com/utag/<account>/<profile>/<env>/utag.js`. `utag.cfg.v`
encodes the publish timestamp. `utag.sync.js` loads synchronously in `<head>` — check what it carries.

| Output | How to read it |
| --- | --- |
| Tags with `load_rule_text` | Each tag's load/send expressions resolved to `utag.cond[N]` load rules. |
| `all_pages` | No load rule — tag loads everywhere, including pages where it may not have consent or purpose. |
| `DISABLED` (`load:0`) | Present but not loading — candidate for removal. |
| >1 tag containing AppMeasurement | Check load rules for overlap; two AA tags on one page = double page views. |
| AA tag `report_suites` | `s.account` from the tag file. Compare with beacon path. |
| `mapped_vars` | Data-layer variable → eVar/prop/events mappings found in the tag file. |
| `data_layer_vars` PII names | `utag_data` keys suggesting plaintext PII; check which tags map them. |
| Non-Adobe vendors | Third-party inventory from the profile — reconcile with the wire. |

Tealium **EventStream / AudienceStream** (server-side) is not public. Tealium consent: look for
the Consent Manager / Consent Integrations (`utag.gdpr`) and tag categories (`tcat`).

---

## GTM hosting Adobe

Run `gtm_container_audit.py GTM-XXX --all-tags --raw-dir ./gtm`. It is GA4-centric; Adobe usually
lives in Custom HTML (`__html`) or community templates (`cvt_*`). Grep the raw container for
`AppMeasurement`, `s_gi(`, `alloy(`, `setCustomerIDs`, `mboxCreate`, `demdex` and read the firing
triggers of those tags.

---

## Multiple TMSs on one site

Launch + Tealium + GTM together is common after migrations or acquisitions. Report which pages
each covers and whether the same vendor (especially AppMeasurement or Meta) loads from more than
one — duplicate loading across TMSs is invisible to each TMS's own governance.

---

## What libraries cannot tell you

- Processing rules, VISTA rules, marketing-channel processing rules, classifications (Adobe-side).
- Event Forwarding / EventStream / sGTM configuration.
- Datastream mapping (which report suites a Web SDK datastream forwards to) — ask for the
  datastream config or infer from Analytics reports.
- Unpublished or paused rules — absence in the published library ≠ never built.
