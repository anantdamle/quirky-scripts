# Identity and join-key reference

What "correct" looks like for Adobe identity, and how to assess whether the data
can join to a CRM.

---

## The claim to test

"We pass the customer ID to Adobe, so every visitor joins to CRM." Test three
propositions; they fail independently.

| Proposition                         | How it fails                                                   | How to test                                                             |
| ----------------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------- |
| A customer ID is actually being set | No `setCustomerIDs` / no authenticated `identityMap` entry     | Visitor API `getCustomerIDs()`, `d_cid_ic` on demdex, XDM `identityMap` |
| It is the _right_ value             | Different hash recipe, or a device ID mistaken for a person ID | Byte-compare, `hash_probe.py`                                           |
| It covers enough traffic            | Only set on login page / checkout stack                        | Coverage per stack and per event type                                   |

**The denominator matters.** "95% of authenticated visits carry a customer ID"
says nothing about the share of _all_ visits, which is what caps deterministic
attribution.

---

## Adobe identity primitives

| Identifier                              | Where it lives                                                                                                      | Scope                  | Notes                                                                                                             |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------- |
| **ECID** (MID)                          | `AMCV_<org>` cookie (`MCMID\|…`), `s_ecid`, Web SDK `kndctr_<org>_identity`; `mid=` on `/b/ss/`, `identityMap.ECID` | Device/browser         | Primary Analytics visitor ID since Visitor API. Not a person.                                                     |
| Legacy `s_vi` / `aid`                   | Third-party/first-party cookie on tracking server                                                                   | Device                 | Grace period / pre-ECID visitors. `aid` + `mid` together = migration in progress.                                 |
| `s_fid` / `fid`                         | First-party fallback                                                                                                | Device                 | Used when `s_vi` blocked. Spikes in `fid` = cookie issues.                                                        |
| `vid` (s.visitorID)                     | Custom                                                                                                              | Whatever the site sets | Overrides ECID for visitor counting — if set to a CRM ID, Analytics visitor ≈ person (with privacy implications). |
| Customer IDs (Visitor API)              | `visitor.setCustomerIDs({code:{id, authState}})` → demdex `d_cid_ic=code%01id%01state`                              | Person                 | Integration code must match a Data Source in Audience Manager/People core service.                                |
| `identityMap` (Web SDK)                 | `xdm.identityMap.<Namespace>[]` with `authenticatedState`, `primary`                                                | Person/device          | Namespace must exist in AEP Identity Service. Must be on every event to stitch reliably.                          |
| Target `mbox3rdPartyId` / `customerIds` | Target requests                                                                                                     | Person                 | Enables cross-device Target profiles.                                                                             |
| AAM `dpuuid`                            | demdex calls                                                                                                        | Person                 | CRM onboarding key.                                                                                               |
| eVar/prop/context data                  | Analytics hit                                                                                                       | Whatever               | Common for login state, hashed email, loyalty tier. The usual Data Feed join key.                                 |

### Where customer IDs can be set — and observability

| Path                                                                  | Observable in browser?                  |
| --------------------------------------------------------------------- | --------------------------------------- |
| `setCustomerIDs` / `identityMap` / eVar on the client                 | **Yes**                                 |
| Launch **Event Forwarding**, Tealium **EventStream**, sGTM enrichment | **No**                                  |
| Processing rules copying context data → eVar                          | **No** (beacon shows context data only) |
| Customer Attributes upload / Bulk Data Insertion / Data Insertion API | **No**                                  |

So absence in the browser does not prove absence in the data. Report as
unresolved and request the processing-rule export, Event Forwarding property, or
a Data Feed sample.

### Confirm from the client's own objects

```js
Visitor.getInstance("<ORG>@AdobeOrg").getCustomerIDs(); // {} = none set
Visitor.getInstance("<ORG>@AdobeOrg").getMarketingCloudVisitorID();
alloy("getIdentity").then((r) => r.identity); // ECID via Web SDK
_satellite.getVar("<identity data element>");
```

### Then confirm from the library

Run `tms_library_audit.py` — `setCustomerIDs` or an `identityMap` update often
lives in a rule that fires only on login success or order confirmation, which a
no-purchase audit never sees.

| Evidence                                                                 | Conclusion                                                                                                           |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| No customer ID on the wire, none in the library                          | Not set client-side; may be server-side (Event Forwarding / processing rules / Customer Attributes) — request config |
| None on the wire, present in a login/purchase rule                       | Configured but not triggered on pages visited                                                                        |
| Customer ID set with `authState` 0 (UNKNOWN)                             | Person-level stitching (CDA, People) may ignore it — should be 1 when logged in, 2 on logout                         |
| `identityMap` entry with `authenticatedState: ambiguous` while logged in | Same defect in Web SDK                                                                                               |
| CRM ID only in an eVar                                                   | Join key is the eVar via Data Feed, not the Experience Cloud identity graph                                          |

Beware a variable named like a person ID whose value is the ECID or a device
hash — confirm what it contains.

---

## Verifying a hashed-email join key

Industry convention: **lowercase, trim, SHA-256, hex**. Sanity check:

```text
SHA-256("test") = 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
```

Use `scripts/hash_probe.py` for the search (keeps the email out of the
transcript).

**Multiple conflicting hashes** of one user across stacks (data layer vs eVar vs
`setCustomerIDs` vs ad pixels) split one person into several identities and
silently fail ad-platform matching. Major finding; escalate media impact
separately.

**Shape is not identity.** 64-hex can be a hashed device ID. A value stable
while anonymous is a device identifier.

---

## Identifiers worth cataloguing

| Identifier                        | Record                                                        | Why                                                   |
| --------------------------------- | ------------------------------------------------------------- | ----------------------------------------------------- |
| ECID                              | Value; consistent across AA/Web SDK/Target? stable anon→auth? | Device key; inconsistency = broken A4T and stitching  |
| Legacy `aid`/`fid`/`vid`          | Present?                                                      | Visitor-count discontinuities, migration state        |
| Customer IDs (Visitor API)        | Integration codes, values, authState                          | Person key in Experience Cloud                        |
| `identityMap` namespaces          | Values, authenticatedState, primary                           | Person key in AEP                                     |
| Identity eVars/props/context data | Variable number, value/shape                                  | Data Feed join key                                    |
| Hashed email variants             | Value, location, recipe                                       | Likely CRM key; conflicts = P0                        |
| Unhashed CRM ID                   | Where transmitted                                             | Strong key; privacy exposure if sent to third parties |
| Loyalty number                    | Present?                                                      | Determines loyalty joins                              |

---

## Privacy findings

- **Plaintext email/phone in eVars, props, context data, pageName or URLs**
  (`g=`, `pev1`) — stored in Adobe and every Data Feed. Adobe's terms prohibit
  sending directly identifying data. Report variable and shape; recommend Data
  Governance labels and data repair/deletion.
- **Plaintext PII in the data layer** — readable by every tag in the TMS; list
  them.
- **Unhashed CRM ID to ad platforms or AAM** — ask for the documented basis.
- **Pre-consent firing** of AppMeasurement / Web SDK / demdex when the CMP is
  opt-in.
- **Session replay** in the same TMS able to read plaintext PII.

Where a plaintext transmission is deliberate (e.g. Event Forwarding hashes it
server-side), frame it as a decision needing documentation and legal review
rather than a bug.
