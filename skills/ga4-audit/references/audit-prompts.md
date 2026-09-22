# Browser subagent prompts

Two verbatim prompts for the `browser` subagent. Substitute `<SITE_URL>` and, in the signed-in
prompt, the findings carried forward from the anonymous pass.

Both prompts front-load the safety rules, because a subagent only sees what it is given.

---

## Anonymous audit prompt

```
Perform a read-only analytics tag audit of <SITE_URL>. Do NOT log in, do NOT submit forms, do NOT
enter any personal data, do NOT place an order. Only navigate and click links/buttons that are part
of normal browsing, and read network requests, console output and page JS variables.

GOAL: enumerate exactly which Google Analytics 4 (GA4) hits fire, on which events, with which
parameters — plus every other marketing pixel present.

SAFETY RULES:
- Do not load or inject any third-party JavaScript. Use only built-in browser APIs. If a task seems
  to need an external library, stop and report instead.
- Do not interact with any cross-origin iframe. If an action would target a frame on a domain other
  than the site's own, stop and report which domain.
- Do not read or exfiltrate cookie values beyond noting cookie NAMES and domains.
- Do not proceed past any point that would submit payment or personal details.

STEPS:

1. Navigate to <SITE_URL>. Accept or dismiss any cookie consent banner (choose "accept all" if
   offered). Record whether a CMP exists and name the vendor (OneTrust, Cookiebot, Sourcepoint,
   Quantcast, Didomi, Usercentrics, Osano, TrustArc, Ketch, CookieYes, Iubenda...).

2. Inspect network requests for GA4 endpoints: any URL containing `/g/collect`,
   `google-analytics.com/g/collect`, `analytics.google.com/g/collect`, `/gtm.js`, `/gtag/js`. Look
   specifically for a FIRST-PARTY server-side GTM endpoint — a subdomain of the site itself (e.g.
   `sgtm.*`, `ss.*`, `a.*`, `metrics.*`, `analytics.*`) serving `/g/collect`, `/gtm.js` or
   `/_/set_cookie`. Report exact hostnames. Also note Google-hosted server-side markers:
   `analytics.google.com/g/s/collect`, `stats.g.doubleclick.net`, `/ccm/collect`, `ads/ga-audiences`.

3. For each distinct GA4 hit, extract: `tid` (measurement ID), `en` (event name), `cid`
   (present/absent only), `uid` (presence; if present report the FULL value, its length, and whether
   it matches ^[0-9a-f]{64}$), `sid`, `_p`, `_s`, `gcd`/`gcs`/`npa`/`dma`/`ec_mode` (Consent Mode v2
   signals), `dl`/`dr` (page and referrer — note explicitly if `dr` is never sent), `_et`, any
   `sst.*` params (server-side tagging markers), and all `ep.*`/`epn.*` (event params) and
   `up.*`/`upn.*` (user properties). List the ep./up. keys with example values, redacting anything
   resembling a real person's name, email or phone.

   IMPORTANT: GA4 sends large payloads as POST with `en` and all params in the BODY, leaving only a
   base URL. If you cannot read a hit's `en`, say so explicitly rather than assuming the event is
   absent. Note how many such unnamed hits occurred per page and their `_s` sequence values.

4. Read `window.dataLayer` via JS evaluation and dump the full array — event names plus payload keys.
   Note especially any identity event (user_data, login, user_id, hashed email/phone), any
   `ecommerce` object, and the GA4 enhanced-ecommerce event set. Also check for other data layers
   (e.g. `window.impactDataLayer`, vendor-specific ones).

5. Browse a realistic journey, capturing the GA4 `en` and the ecommerce/`ep.` payload at each step:
   a. A category/listing page → expect `view_item_list`, and `select_item` on tile click.
   b. A product detail page → expect `view_item`. Capture item_id, item_name, price, currency,
      item_brand, item_category*, item_variant. Are they correctly populated, or empty/undefined?
      Note if `view_item` fires more than once per product view.
   c. Add to cart → expect `add_to_cart`. If NO GA4 hit is generated, verify this carefully at the
      CDP/network level and record which OTHER pixels did fire from the same dataLayer push. A
      dataLayer push that ad pixels consume but GA4 ignores is a high-value finding.
   d. Cart/bag page → expect `view_cart`.
   e. Begin checkout, ONLY as far as it goes without entering personal details or payment info →
      expect `begin_checkout`. STOP there. If checkout requires login, report that and stop.
   f. Site search with a generic term → expect `view_search_results` or `search` with `search_term`.

   At each step note whether the page is the same application or a DIFFERENT stack (different
   framework, different GTM container ID, a redirect to a different URL pattern). Sites frequently
   run a modern storefront and a legacy checkout with entirely separate tagging — if so, report the
   boundary, because it is usually where the defects cluster.

6. Report presence of: `session_id`/`sid`, `engagement_time_msec`/`_et`, `debug_mode`/`_dbg` (should
   be OFF in production), the cross-domain linker (is a `_gl` param appended to links to other
   domains? check any off-domain checkout, gift-card or blog destinations), and whether hits are
   batched as POST.

7. Inventory every OTHER third-party marketing/analytics tag, with vendor and ID: Google Ads (AW-),
   Floodlight (DC-), Meta/Facebook (pixel id), TikTok, Snapchat, Pinterest, Reddit, Microsoft/Bing
   UET, LinkedIn, Criteo, The Trade Desk, affiliate networks (Impact, Awin, Rakuten, Commission
   Factory), review platforms (Bazaarvoice, Yotpo), session replay / experience analytics (Hotjar,
   Clarity, Contentsquare, Quantum Metric, FullStory), RUM (Datadog, New Relic, Dynatrace), CDPs
   (Segment, Tealium, mParticle, Salesforce Interactions, Adobe), personalisation/AB (Optimizely,
   VWO, Dynamic Yield, Monetate, AB Tasty, Insider), site search (Attraqt, Algolia, Bloomreach),
   fraud/bot (Riskified, Kasada, Imperva). For any pixel that appears to send hashed email or phone
   (Meta `ud[em]`, Snapchat `u_hem`, TikTok advanced matching, Pinterest `em`, UET `em`), note it
   explicitly — but do not log real values.

   Also list cookies present whose vendor SDK you could NOT find on the page. These often indicate
   third-party collection via embedded widgets, outside the GTM inventory.

8. Note app-store links and any Firebase/AppsFlyer/Branch/Adjust/Kochava/Singular SDK or deep-link
   references in page source, plus any `al:ios`/`al:android` App Links meta tags — these hint at how
   a mobile app is measured. Also look for any parameter suggesting the container detects in-app
   WebViews (e.g. a `webview_platform`-style param, or user-agent checks for an app).

FINAL REPORT — structured markdown:
(1) GA4 property and tagging architecture — client-side vs server-side, hostnames, GTM container IDs
    (report ALL of them, and which pages each covers)
(2) Consent Mode / CMP — include `google_tag_data.ics` state (`active`, `usedDefault`, the four
    entries and whether each is `implicit`), and `__tcfapi`/`__gpp`/`__uspapi` presence
(3) Table of GA4 events observed → event name → key params → data-quality notes
(4) Identity findings — is any `uid` or hashed identifier present while anonymous? What identity
    fields exist in the dataLayer even if empty (these reveal the intended design)?
(5) dataLayer inventory
(6) Full third-party tag inventory
(7) Gaps and anomalies — missing standard ecommerce events, missing item fields, duplicate hits,
    double-tagging, PII in URLs or params, missing consent signals, deprecated tags (UA-)
(8) App measurement hints
(9) What you could NOT verify and why

Be precise. Quote exact parameter names and values, redacting PII.
```

---

## Signed-in audit prompt

Carry forward the anonymous findings — especially the measurement IDs, container IDs, the anonymous
`dataLayer` identity object, and any event that failed to fire. The diff is where the value is.

```
Continue the read-only analytics audit of <SITE_URL>. The user has now SIGNED IN in this browser
session. This is the authenticated-state pass.

ABSOLUTE SAFETY RULES — READ FIRST:
- DO NOT PLACE AN ORDER. DO NOT SUBMIT PAYMENT. Do not click any final "Pay", "Place order",
  "Complete purchase" or equivalent button. Walk checkout forward only as far as it goes WITHOUT
  submitting payment, and stop before any irreversible confirmation.
- Do NOT sign the user out.
- Do NOT change account settings, addresses, saved cards or any profile data. If a checkout step
  requires entering an address to continue, STOP there — do not fill it in.
- Do NOT interact with any cross-origin iframe (payment frames especially). If an action would
  target a frame on another domain, stop and report.
- Do NOT load or inject third-party JavaScript. Built-in browser APIs only.
- The signed-in user is a real person. Do NOT output their plaintext email, phone, full name, street
  address or any card data. For plaintext PII fields report ONLY: field name, that it is populated,
  and its shape (e.g. "populated, plaintext email format, 25 chars"). HASH VALUES ARE NEEDED — report
  those in full, they are the point of this pass.

PRIMARY OBJECTIVE — the identity join. Most important part:

1. Read `window.dataLayer` fully on the current page and on the cart/account pages. Compare against
   the anonymous state, which was:
   <PASTE ANONYMOUS dataLayer IDENTITY OBJECT HERE>
   For each identity field, report whether it is now populated and its exact value — EXCEPT any
   plaintext PII field, for which report only populated-or-not and its shape. Flag explicitly if a
   PLAINTEXT email (as opposed to a hash) is present in the dataLayer, and list which other tags run
   in the same container and could therefore read it.

2. For every GA4 `/g/collect` hit, report: `tid` (which property), `en`, and critically:
   - `uid` — is it NOW present? Full value, length, does it match ^[0-9a-f]{64}$?
   - all `up.*`/`upn.*` user properties — full values. If NONE exist, state that explicitly; it means
     the property has no user-scoped dimensions at all, which blocks user-scoped audiences.
   - all `ep.user_data.*` params. Note the `_tag_mode` value and whether a plaintext email or a hash
     is being transmitted. A plaintext email in a GA4 parameter is a significant finding.
   - any `ep.*` carrying a CRM/customer ID.

3. THE CRITICAL COMPARISON: is the GA4 `uid` byte-identical to the dataLayer's hashed-email field?
   Do a literal `===` comparison in JS and report the boolean plus both values. Also compare
   case-insensitively. If they differ, report both in full and describe how (length? case? apparently
   a different algorithm or pre-image?).

4. Verify what the hashes actually ARE, using `crypto.subtle` in-page. First sanity-check your
   implementation against the known SHA-256 of "test"
   (9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08). Then test each observed hash
   against candidate pre-images of the account email: as-is, lowercased, uppercased, trimmed, and
   any CRM/contact ID present. Report which hash matches which recipe, and which match nothing.
   Multiple DIFFERENT hashes of the same user is a major finding — report each with its location.

5. Determine WHERE `uid` appears. If the site runs multiple stacks, check EACH: navigate to the
   modern storefront (home, category, PDP) AND the cart/checkout pages, and report `uid` presence
   per page. Identity is often wired on one stack and not the other.

6. Re-test every event that failed to fire anonymously — these were:
   <LIST THE FAILING EVENTS FROM THE ANONYMOUS PASS>
   Confirm whether authentication changes the outcome. For each, clear the resource-timing buffer
   first so the measurement is clean.

7. CART TEST — add, verify, then remove:
   a. On a product detail page, add an item to the cart. Record whether a GA4 `add_to_cart` hit is
      generated, and which other pixels fired from the same dataLayer push.
   b. Open the cart page. Record whether `view_cart` fires.
   c. Enter checkout as far as possible WITHOUT entering any personal details — record
      `begin_checkout`, and `add_shipping_info` if a delivery step is reachable.
   d. Return to the cart and REMOVE the item you added. Record whether `remove_from_cart` fires in
      GA4 — this is an extra event under test, not just cleanup.
   e. Confirm the cart is back to the state you found it in, and report the final state. If anything
      you added could not be removed, say so prominently.

8. Do the ad pixels now send hashed PII? Report presence and full hash values for Meta `ud[em]`/
   `ud[ph]`, Pinterest `em`, Microsoft UET `em`/`ph`, Floodlight `em`, TikTok advanced matching,
   Criteo. Cross-reference against step 4: if a platform is receiving a hash that is NOT the correct
   hash of the account email, its advanced matching is failing silently — call that out. Also watch
   for any pixel injected by ANOTHER vendor (e.g. a second Meta pixel loaded by a retargeting
   partner) carrying identifiers.

9. Re-check Consent Mode signed in: `google_tag_data.ics` state, `gcd`/`gcs`/`npa` on hits, whether a
   CMP has appeared.

10. Report loyalty/membership/account-state signals now visible, and assess which identifiers could
    serve as a CRM join key. Note explicitly whether any UNHASHED customer/CRM ID is exposed
    client-side or transmitted to any third party.

11. Cookies: is a server-set first-party identity cookie present (`FPID`)? What is `FPLC`/`_fplc`?
    Report cookie names and whether identity cookies are set client-side or server-side.

FINAL REPORT — structured markdown:
(A) Identity join verdict — LEAD WITH THIS. Is `uid` present, is it SHA-256 hex, does it byte-match
    the dataLayer hash, and on WHICH stacks/pages does it appear?
(B) Authenticated dataLayer contents, per stack, with the plaintext-PII question answered definitively
(C) GA4 event table for this pass, with explicit status for add_to_cart, view_cart, begin_checkout,
    add_shipping_info, add_payment_info, remove_from_cart
(D) Hash analysis — every hash observed, its location, and what it is a hash OF (or "unidentified")
(E) Ad-pixel PII transmission, and which platforms are receiving a wrong or correct hash
(F) Consent mode + identity cookie status
(G) Anything that changed versus anonymous that I did not ask about
(H) Cart test outcome and final cart state
(I) Explicit list of what you could NOT verify and why

Be precise. Quote exact parameter names and full hash values. Redact only plaintext PII.
```
