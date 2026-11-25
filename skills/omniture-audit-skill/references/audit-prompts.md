# Browser subagent prompts

Two verbatim prompts for the `browser` subagent. Substitute `<SITE_URL>` and, in
the signed-in prompt, the findings carried forward from the anonymous pass. Both
front-load the safety rules, because a subagent only sees what it is given.

---

## Anonymous audit prompt

```text
Perform a read-only Adobe Analytics / Adobe Experience Cloud tag audit of <SITE_URL>. Do NOT log
in, do NOT submit forms, do NOT enter personal data, do NOT place an order. Only navigate and
click normal browsing links/buttons, and read network requests, console output and page JS.

GOAL: enumerate exactly which Adobe hits fire (AppMeasurement, Web SDK, Target, Audience
Manager), on which interactions, with which variables — plus the tag manager(s) and every other
marketing pixel present.

SAFETY RULES:
- Do not load or inject third-party JavaScript (no debugger extensions or bookmarklets). Built-in
  browser APIs only. Reading window._satellite, window.s, window.s_c_il, window.alloy,
  window.__alloyNS, window.utag, window.adobe, window.Visitor is fine.
- Do not change Launch environment / debug overrides in localStorage or cookies.
- Do not interact with cross-origin iframes. Stop and report the domain.
- Note cookie NAMES and domains only, except AMCV_*, s_ecid, kndctr_*_identity, s_vi, s_fid,
  where you may read the value to extract the ECID / visitor ID.
- Do not proceed past any point that would submit payment or personal details.

CAPTURE: for every Adobe request, save "URL<TAB>POST_BODY" (one per line) so it can be decoded
offline. Adobe requests match: /b/ss/, *.omtrdc.net, *.2o7.net, edge.adobedc.net, /ee/ (first-
party edge CNAME), *.demdex.net, *.tt.omtrdc.net, /rest/v1/delivery, /m2/. Web SDK bodies are
JSON and essential; AppMeasurement bodies matter whenever the URL has no variables.

STEPS:

1. Navigate to <SITE_URL>. Record the CMP vendor (OneTrust, Cookiebot, TrustArc, Didomi,
   Usercentrics, Sourcepoint...) and whether Adobe requests fire BEFORE consent. Accept all, then
   record what changes. Check: adobe.optIn (Opt-In service) state, alloy consent
   (`alloy("getIdentity")` is acceptable; do NOT call setConsent), __tcfapi/__gpp presence.

2. Tag manager detection — report every one found, with IDs and the library URL:
   - Adobe Launch / Tags: script from assets.adobedtm.com (launch-EN*.min.js), or self-hosted.
     Read _satellite.buildInfo (buildDate, environment, turbineVersion),
     _satellite.property.name, _satellite.company.orgId, and whether environment != production.
   - Legacy DTM: satelliteLib-*.js, _satellite.dtm.
   - Tealium iQ: tags.tiqcdn.com/utag/<account>/<profile>/<env>/utag.js, utag.cfg.v, utag.cfg.utid,
     window.utag_data keys (names + example values, PII redacted).
   - GTM: GTM-XXXX container IDs and whether Adobe code is loaded through it.
   - Any other (Ensighten, Signal, Segment) — name only.

3. AppMeasurement: for each /b/ss/ hit report report suite(s) from the path, library version,
   tracking server (first-party CNAME vs *.sc.omtrdc.net), call type (page view vs pe=lnk_o/
   lnk_d/lnk_e with pev2 link name), pageName, g, r, ch, events, products, purchaseID, v0, every
   vN (eVar) and cN (prop) with values, list vars, context data (c.=...&.c=), mid (ECID), aid,
   vid, fid, sdid. Read window.s (or s_c_il) config: account, trackingServer, linkInternalFilters,
   trackDownloadLinks, trackExternalLinks, usePlugins, visitor, and AppMeasurement.version.
   Note any D= dynamic variables.

4. Web SDK: for each edge request report datastream (configId), edge domain (first-party CNAME
   or edge.adobedc.net), and from the JSON body: events[].xdm.eventType, web.webPageDetails.name/
   URL, commerce.*, productListItems, identityMap, data.__adobe.analytics (AA vars sent outside
   XDM), meta.consent, query.personalization.decisionScopes. Read window.__alloyNS and the alloy
   configure settings if exposed (orgId, edgeConfigId, defaultConsent, clickCollectionEnabled,
   idMigrationEnabled, thirdPartyCookiesEnabled).

5. If BOTH AppMeasurement and Web SDK fire, report whether they appear to feed the SAME report
   suite (compare rsid vs datastream's Analytics mapping if inferable) and count page views per
   page from each. Two page views per page view is a P0 lead.

6. ECID / Visitor API: Visitor.version, Visitor.getInstance org IDs, AMCV_ cookie (ECID value,
   MCAAMLH, MCAAMB), s_ecid, kndctr_<org>_identity, legacy s_vi / s_fid, demdex.net /id calls and
   their d_mid. Is ECID consistent across AppMeasurement mid, Web SDK identity and Target?

7. Target: at.js version (adobe.target.VERSION) or Web SDK personalization; mbox names / decision
   scopes; whether a flicker-mitigation (prehiding) snippet exists and its timeout; whether
   supplemental data ID (sdid / mboxMCSDID) is present on BOTH the Target and Analytics hit (A4T).

8. Audience Manager: DIL presence, demdex.net requests (/event, /ibs, /id), d_orgid, signals.

9. Browse a realistic journey, decoding hits at each step:
   a. Category/listing page — what is tracked (prodView? list eVar? commerce.productListViews?)
   b. Product detail — prodView / commerce.productViews with products string or productListItems
      (SKU, price, category). Note double prodView.
   c. Add to cart — scAdd (and scOpen on first add) / commerce.productListAdds. If NO Adobe hit,
      record which other pixels fired.
   d. Cart page — scView / commerce.productListViews.
   e. Begin checkout ONLY as far as possible without personal details — scCheckout /
      commerce.checkouts. STOP there.
   f. Site search — internal search eVar/event, search term variable.
   g. Click one internal link, one outbound link and (if present) one file download — did
      s.tl fire (lnk_o/lnk_e/lnk_d) or Web SDK web.webinteraction.linkClicks? Is ActivityMap data
      (c.a.activitymap.*) present?
   For each step note whether eVars/events from the PREVIOUS hit reappear on the next s.tl hit
   (variable leakage) and whether the page is a different stack (different TMS/library/report
   suite — the boundary is where defects cluster). For SPAs, check whether virtual page views
   fire on route change.

10. Inventory every OTHER marketing/analytics tag with vendor and ID (GA4, Google Ads, Floodlight,
    Meta, TikTok, Snap, Pinterest, Reddit, UET, LinkedIn, Criteo, TTD, affiliates, session replay,
    RUM, CDPs, personalisation, search, fraud). Note any that send hashed email/phone — presence
    only. List cookies whose vendor SDK you could not find.

11. Note app-store links, Branch/AppsFlyer/Adjust references, App Links meta tags, and any
    WebView detection in the TMS (user-agent conditions, app query params).

FINAL REPORT — structured markdown:
(1) Tagging architecture — TMS(s) with IDs/build dates, AppMeasurement vs Web SDK vs both,
    collection hosts (first-party CNAME?), report suites, datastreams, org ID
(2) Consent — CMP, pre-consent firing, Opt-In / Web SDK consent state
(3) Beacon table: page/step → call type → pageName → events → eVars/props → products → notes
(4) Variable map — every eVar/prop/event/context-data key observed with example value
(5) Identity — ECID, legacy IDs, any customer ID or hash while anonymous, data-layer identity
    fields present even if empty
(6) Target / AAM / A4T findings
(7) Third-party tag inventory
(8) Gaps and anomalies — double page views, leakage, missing commerce events, missing
    purchaseID, PII in variables or URLs, pre-consent firing, deprecated libs (H-code s_code.js,
    DTM, old AppMeasurement/at.js), non-production Launch environment on prod
(9) App measurement hints
(10) What you could NOT verify and why (e.g. POST bodies not captured, processing rules)

Be precise. Quote exact variable names and values, redacting PII.
```

---

## Signed-in audit prompt

Carry forward the anonymous findings — report suites, datastreams, library IDs,
the anonymous data-layer identity object, ECID value, and any event that failed
to fire. The diff is the value.

```text
Continue the read-only Adobe tag audit of <SITE_URL>. The user has now SIGNED IN in this browser.

ABSOLUTE SAFETY RULES — READ FIRST:
- DO NOT PLACE AN ORDER OR SUBMIT PAYMENT. Stop before any irreversible confirmation.
- Do NOT sign the user out. Do NOT change settings, addresses, saved cards or profile data.
  If a checkout step requires entering an address, STOP.
- Do NOT interact with cross-origin iframes (payment frames especially).
- Do NOT load or inject third-party JavaScript. Do NOT change Launch environment overrides.
- The user is a real person. Never output their plaintext email, phone, name, address or card
  data — for plaintext PII report field name, populated-or-not, and shape only. HASH VALUES AND
  ECIDs ARE NEEDED — report those in full.

Keep capturing every Adobe request as "URL<TAB>POST_BODY".

PRIMARY OBJECTIVE — the identity join:

1. Data layer: read the site's data layer (adobeDataLayer, digitalData, utag_data, dataLayer,
   _satellite.getVar on identity data elements) on home, account and cart. Compare against the
   anonymous state:
   <PASTE ANONYMOUS IDENTITY OBJECT HERE>
   Report each identity field: populated or not, exact value (hashes/IDs) or shape (plaintext PII).
   Flag any PLAINTEXT email in the data layer and which tags can read it.

2. Customer ID sync:
   - Visitor API: was setCustomerIDs called? Read Visitor.getInstance(<org>).getCustomerIDs() —
     report each integration code, id, and authState (0 UNKNOWN / 1 AUTHENTICATED / 2 LOGGED_OUT).
     Look for demdex.net requests carrying d_cid / d_cid_ic (format: code%01id%01authState).
   - Web SDK: identityMap in the XDM of each event — namespaces, ids, authenticatedState,
     primary. Is the CRM namespace authenticated and on every event, or only some?
   - Target: mbox3rdPartyId / customerIds on Target requests.
   - AAM: dpuuid.

3. Analytics variables carrying identity: which eVars/props/context-data keys now hold a customer
   ID, hash, login state, loyalty tier? Full values for IDs and hashes. Is a PLAINTEXT email or
   phone in ANY eVar, prop, context data, pageName, or URL query string? That is a P0.

4. THE CRITICAL COMPARISON: is the customer ID in Visitor/identityMap byte-identical to the
   hashed-email (or CRM ID) in the data layer and in any eVar? Literal === and case-insensitive.
   If different, report both in full and how they differ.

5. Verify what each hash IS with crypto.subtle in-page. First sanity-check: SHA-256("test") =
   9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08. Test candidate pre-images:
   email as-is, lowercased, uppercased, trimmed, and any CRM ID present. Report matches / no match.

6. ECID stability: is the ECID identical to the anonymous pass? (It should be — a change means
   cookies were reset or a different org/CNAME is in play.) If Web SDK and Visitor API coexist,
   does idMigrationEnabled keep them aligned?

7. Per-stack coverage: check customer-ID presence on EACH stack (storefront, account, checkout).

8. Re-test events that failed anonymously:
   <LIST THE FAILING EVENTS FROM THE ANONYMOUS PASS>

9. CART TEST — add, verify, remove:
   a. Add an item on a PDP — scAdd/scOpen or commerce.productListAdds? Products string correct?
   b. Cart page — scView / productListViews.
   c. Checkout as far as possible without personal details — scCheckout / commerce.checkouts and
      any shipping step event.
   d. Return to cart and REMOVE the item — scRemove / commerce.productListRemovals?
   e. Confirm cart is back to its original state. If anything could not be removed, say so prominently.

10. Ad pixels: hashed PII presence and full values (Meta ud[em], Pinterest em, UET em, Floodlight,
    TikTok, Criteo). Cross-reference step 5 — a wrong hash means matching fails silently.

11. Consent re-check signed in. Login event: did a login success event/eVar fire? Report it so
    the client can filter the audit's own login.

FINAL REPORT — structured markdown:
(A) Identity join verdict — LEAD WITH THIS. Customer ID set? Which integration code / namespace,
    authState, hashed or not, matches data layer? On which stacks?
(B) Authenticated data layer per stack, plaintext-PII question answered definitively
(C) Beacon table for this pass with explicit status of scAdd, scView, scCheckout, scRemove (or XDM
    equivalents)
(D) Hash analysis — every hash, location, and what it is a hash OF (or "unidentified")
(E) PII in Adobe variables / context data / URLs
(F) Ad-pixel PII transmission
(G) ECID stability and consent
(H) Anything else that changed versus anonymous
(I) Cart test outcome and final state
(J) What you could NOT verify and why
```
