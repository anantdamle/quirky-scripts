# GTM container inspection reference

How to read the output of `scripts/gtm_container_audit.py`, and what it can and cannot tell you.

Network observation answers "what fired". Container inspection answers "what was supposed to fire,
and why didn't it" — which is where most actionable findings live.

---

## What is fetchable

| Artefact | Public? | Endpoint |
|---|---|---|
| Client-side (web) GTM container | **Yes** | `googletagmanager.com/gtm.js?id=GTM-XXXXXXX` |
| Container proxied through a first-party domain | Yes — but it is the *same web container* | `sgtm.example.com/gtm.js?id=GTM-XXXXXXX` |
| **Server-side GTM container config** | **No — for any site** | none exists |

This distinction is repeatedly misunderstood, so state it explicitly in reports. A first-party
endpoint serving `/gtm.js` is proxying the **client** container. The **server** container's clients,
tags, transformations and variables live only in the sGTM instance and the GTM UI. There is no public
endpoint, no debug URL without auth, and no way to infer the configuration from outside.

Consequence: if identity or any transformation appears to happen server-side, that is a genuine blind
spot. Recommend requesting a container export. Do not present client-side absence as proof.

---

## Container structure

`gtm.js` wraps a JSON-ish data object. The parts that matter:

| Key | Contains |
|---|---|
| `tags` | Tag definitions. `function` is the type — `__gaawe` is a GA4 event tag, `__googtag` the config tag, `__fls`/`__flc` Floodlight sales/counter, `__ua` legacy Universal Analytics |
| `predicates` | Individual conditions, e.g. "Event equals add_to_cart" |
| `rules` | Triggers: combinations of predicates mapped to tags they fire (`add`) or block (`block`) |
| `macros` | Variables, including Custom JavaScript |

Per-tag fields worth reading:

- `vtp_eventName` — the GA4 event name. If it references a macro instead of a literal string, the
  event name is computed at runtime; see **dynamic event names** below.
- `vtp_sendEcommerceData` / `vtp_getEcommerceDataFrom` — the ecommerce payload path. A tag that
  triggers correctly but sends nothing usually fails here.
- `vtp_measurementIdOverride` — which property receives the hit, often via an environment macro.
- Paused state — a paused tag never fires regardless of triggers.

---

## What to look for

### Duplicate event names — resolve the measurement ID first

**The highest-value check, and the easiest one to get wrong.**

Two unpaused tags emitting the same event on the same trigger **to the same property** means every
one of those events is sent twice. For `purchase` this doubles transaction counts and revenue. Rank
it P0, above missing events: a missing event reads as an obvious zero and gets noticed, whereas
doubled revenue looks entirely plausible and silently corrupts every ROAS, AOV and attribution
figure downstream — potentially for as long as it has been live.

The cheapest confirmation is comparing GA4 transaction counts against the order system. Roughly 2×
settles it without needing to place a test order.

**But two tags emitting one event to two *different* properties is ordinary dual-tagging, and
entirely benign.** Deciding this from tag configuration alone produces confident false positives,
which is worse than missing the finding — so always resolve `vtp_measurementIdOverride` to an actual
`G-XXXXXXX` before concluding anything. Three traps:

1. **The ID is usually behind a macro chain.** A common pattern is hostname → bot-flag → ID, so the
   reference resolves through two or three lookup tables before reaching a constant.
2. **`vtp_defaultValue` is usually the UAT or bot fallback, not production.** Taking it because the
   lookup has no key literally named `prod` will name the wrong property with full confidence. Look
   for the live branch — keys like `prod`, `live`, or `human` on a bot-flag table — and recurse,
   because the live marker is often one level below the table you are reading.
3. **When it cannot be resolved to one ID, say so.** Cross-reference against the measurement IDs
   actually observed on the wire during the browser audit: the wire tells you which property is
   live, the container tells you the routing logic. Neither alone is sufficient. Pass them via
   `--observed-id G-XXXXXXX` — where the container is ambiguous and exactly one candidate was seen
   live, that resolves it.

Real containers use opposite conventions, which is why this cannot be guessed:

| Pattern | Production branch | Default value |
|---|---|---|
| Explicit environment key (`prod`, or a bot-flag table keyed `human`) | the named key | UAT or bot fallback |
| Only non-production hostnames listed (`sit`, `uat`, `qa`, `preview`) | **the default** | production |

Take the default in the first case and you name UAT as production with full confidence. The script
handles both patterns and reports anything else as ambiguous rather than picking.

Also distinguish genuine duplication from tags that share an event name but are separated by other
conditions. A container may legitimately have a dozen tags emitting one `interaction` event from a
dozen different clicks — correct design, not a defect. Treat as confirmed in two cases:

- **Triggers conditioned only on the event name**, so nothing separates the tags.
- **Two tags sharing a byte-identical firing rule**, however many conditions it has. This is the
  stronger signal and catches duplicates the first test misses — for example two `page_view` tags
  both gated on the same page-path-plus-webview condition, which fires twice on exactly the pages
  matching it.

### Asymmetric properties are often deliberate

Where a second property receives a curated subset of events, check the tag names before calling it
broken — names like `GA4 - Alt - …` indicate intent. A deliberate minimal property is not a defect.

It is still worth reporting, for a specific reason: if the data export is wired to the secondary,
it may contain conversions with almost no path data. That looks like a modelling problem when it is
actually a plumbing one, and it can cost a lot of time. Always confirm which property the export
comes from before analysing anything.

### A tag that exists, triggers, and still does not fire

When the network audit shows an event pushed to `dataLayer` and consumed by other pixels, but no GA4
hit, the container tells you which of these it is:

| Container shows | Diagnosis |
|---|---|
| No tag for that event | Genuinely missing — needs building |
| Tag exists but paused | One-click fix |
| Tag exists, trigger does not match the pushed event name | Trigger fix |
| Tag exists, trigger matches, blocking rule applies | Read the blocking rule — often a deliberate exclusion |
| Tag exists, trigger matches, nothing blocks it | **The fault is inside the tag** — usually the ecommerce-data path |

The last case is the most useful finding you can hand an engineer, and it is invisible from the
network alone. Strengthen it by diffing against a tag that *does* fire successfully on the same page
load: if the configurations are near-identical except for the ecommerce settings, you have isolated
the fault to those settings.

### Dynamic event names

A Custom JavaScript macro that maps a site's internal event names to GA4 event names is a good
pattern — it centralises the translation. Extract and quote the mapping table in the report: it is
the definitive specification of what the site *intends* to send, which is often more informative than
what it currently does.

Two failure modes to check:

- **Silent drops.** If the tag's trigger includes a condition like `unless {{macro}} equals
  "undefined"`, any event absent from the mapping table is discarded with no error and no diagnostic.
  Audit the table against every event the site actually pushes.
- **Stale names.** A mapping table listing legacy internal event names will not match a newer stack
  pushing GA4-native names. This is common where a modern storefront coexists with a legacy checkout,
  and it is why the two cannot share tagging.

### Blocking rules as evidence

Blocking rules reveal intent. A rule excluding tags when the user agent indicates a mobile app
implies the app renders these same pages in a WebView. If such a rule sits on the `purchase` tag,
app purchases are being deliberately suppressed in GA4 — which means GA4 cannot be the source for app
conversions, and you have learned something about the app without inspecting it.

### Deprecated and orphaned tags

- **Universal Analytics (`UA-`).** Check whether a `__ua` tag exists in the container. If UA hits are
  observed on the wire but no UA tag exists, the tag is hardcoded in the page template — so editing
  GTM will not remove it. Worth saying, because the obvious fix will not work.
- **Tags with cookies but no requests.** A vendor cookie present with no corresponding SDK or network
  call usually means a paused or orphaned tag, or an embedded partner widget setting it.

### Coverage gaps

Compare the GA4 event tags present against the standard ecommerce set: `view_item_list`,
`select_item`, `view_item`, `add_to_cart`, `remove_from_cart`, `view_cart`, `begin_checkout`,
`add_shipping_info`, `add_payment_info`, `purchase`, `refund`. Report which are absent.

Absence is not automatically a defect — if checkout lives on a different stack, the storefront
container legitimately has no `purchase` tag. Check the other container before calling it a gap.

---

## Multiple containers

Sites often run more than one container across different stacks. Always audit **all** of them, and
report which pages each covers. The boundary between stacks is reliably where defects cluster, for
predictable reasons:

- Each stack has its own `dataLayer` schema, so events do not match across containers.
- Identity fields populate on one stack and not the other.
- One stack may still use a Universal-Analytics-era ecommerce schema.
- Field values drift — different casing, different hash algorithms, different category derivations.

When reporting, attribute each finding to a stack. "The site has no `view_cart` event" is wrong if
one container has it and the other does not; "`view_cart` exists in the storefront container but the
cart page runs on the legacy stack, which has no equivalent" is correct and actionable.
