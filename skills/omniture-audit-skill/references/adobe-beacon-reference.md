# Adobe beacon reference

Glossary for reading decoded hits. `adobe_beacon_decoder.py` names most of these automatically.

## AppMeasurement (`/b/ss/<rsid[,rsid2]>/<1|0>/<lib>/s<random>`)

| Param | Meaning | Audit note |
|---|---|---|
| path rsids | Report suite(s) | >1 = multi-suite tagging; each extra suite is a billed secondary server call |
| `pageName` / `gn` | Page name | Missing → reports fall back to URL |
| `g`, `r` | Page URL, referrer | Scan for PII in query strings |
| `pe` | Link type: `lnk_o` custom, `lnk_d` download, `lnk_e` exit | Absent = page view (`s.t`) |
| `pev2`, `pev1` | Link name, link URL | |
| `events` | Success events, incl. `purchase`, `prodView`, `scOpen`, `scAdd`, `scRemove`, `scView`, `scCheckout`, `eventN[=value]`, `eventN:serial` | Serialisation prevents double counting |
| `products` | `cat;SKU;units;revenue;events;merch eVars` comma-separated | Revenue only counts with `purchase` |
| `purchaseID` / `pi` | Order de-dup ID | Missing → reloads double revenue |
| `v0` | Campaign (tracking code) | |
| `vN` / `cN` / `lN` / `hN` | eVarN / propN / list var / hierarchy | `D=pageName` = dynamic copy |
| `c.` … `.c` | Context data (nested with `a.` / `.a`) | Processing rules map these server-side — invisible here |
| `c.a.activitymap.*` | Activity Map | |
| `mid`, `aid`, `fid`, `vid` | ECID, legacy s_vi, fallback ID, custom visitor ID | See identity reference |
| `mcorgid`, `aamb`, `aamlh` | Org ID, AAM blob, AAM location hint | |
| `sdid` | Supplemental data ID | Must match the Target hit for A4T |
| `ce`, `cdp`, `j`, `bw`, `bh`, `s` | Charset, cookie domain periods, JS version, browser/screen | `cdp` wrong on multi-part TLDs (`.com.au`) breaks first-party cookies |

## Web SDK (`/ee/[region/]v2/interact|collect?configId=<datastream>`)

POST JSON. Key fields: `events[].xdm.eventType`, `xdm.web.webPageDetails`, `xdm.commerce.*`
(`productViews`, `productListAdds`, `productListRemovals`, `productListViews`, `checkouts`,
`purchases`, `order.purchaseID`), `xdm.productListItems[]`, `xdm.identityMap`,
`data.__adobe.analytics.*` (AA variables sent outside XDM — reach Analytics but not AEP datasets),
`meta.consent`, `meta.state.entries` (cookies), `query.personalization`.

`collect` endpoint = `sendBeacon` (no response, e.g. on unload). `configOverrides` = datastream
override per command (check it is intentional).

## Target

`*.tt.omtrdc.net/rest/v1/delivery` (at.js 2) or `/m2/<client>/mbox/json` (at.js 1). Fields: mbox
names, `mbox3rdPartyId`/`customerIds`, `mboxMCGVID` (ECID), `mboxMCSDID` / `experienceCloud.analytics.supplementalDataId`.

## Audience Manager / demdex

`dpm.demdex.net/id` (ID sync, `d_mid`, `d_cid_ic=<code>%01<id>%01<authState>`), `/ibs` (partner
syncs), `<partner>.demdex.net/event` (signals `c_*`, `d_dpuuid`).

## Server-side behaviour that the browser cannot show

Processing rules, VISTA rules, marketing channel rules, bot filtering, classifications, Datastream
→ report suite mapping, Event Forwarding. Name them as unverifiable and request the config.
