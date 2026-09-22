# Identity and join-key reference

What "correct" looks like for GA4 identity, and how to assess whether the data can join to a CRM.
This is the part of a GA4 audit that determines whether attribution is possible at all.

---

## The claim to test

Clients commonly report a high match rate between GA4 and their CRM — "we pass hashed email as
`user_id` and see 99% match". Two things can be true at once: the match rate within matched rows is
genuinely high, *and* the data is unusable for attribution.

Test three separate propositions. They fail independently.

| Proposition | How it fails | How to test |
|---|---|---|
| A `user_id` is actually being set | Absent from the wire entirely | Look for `uid` on `/g/collect` |
| It is the *right* value | Hashed with a different recipe than CRM stores | Compare hashes byte-for-byte |
| It covers enough traffic | Only set for a small share of sessions | Fill rate over **all** sessions |

**The denominator matters more than the rate.** "99% of GA4 rows with a `user_id` match CRM" says
nothing about coverage. The number that caps deterministic attribution reach is the share of *all*
sessions carrying a `user_id`. Always ask which denominator a quoted figure uses.

---

## Where `user_id` can be set

GA4 `user_id` reaches Google by one of three paths, and they are not equally observable:

| Path | Observable in browser? |
|---|---|
| Client-side via `gtag`/GTM — appears as `uid` on the outbound hit | **Yes** |
| Injected by a server-side GTM container before forwarding | **No** |
| Sent server-to-server via Measurement Protocol, never touching the browser | **No** |

So **absence of `uid` in the browser does not prove `user_id` is absent from the data.** Report it as
an unresolved question, not as a refutation of the client's claim. Two supporting signals suggest
server-side injection is happening:

- A plaintext email or phone is being sent to the tagging endpoint (the server needs the raw value in
  order to hash it).
- `ep.user_data._tag_metadata.*.mode=m` — manual user-provided-data mode, meaning hashing is
  delegated rather than done in-page.

When you suspect server-side injection, recommend requesting a **container export or read access to
the server container**. It is a routine ask and it settles the question outright.

### Confirming from the tag's own data model

More reliable than reading URLs, because it is immune to POST-body invisibility:

```js
// Substitute the measurement ID
google_tag_manager['G-XXXXXXXXXX'].dataLayer.get('user_id')
google_tag_manager['G-XXXXXXXXXX'].dataLayer.get('user_properties')
```

`undefined` for both means nothing is configured client-side, regardless of what any individual hit
shows.

---

## Verifying a hashed-email join key

The industry-standard recipe is **lowercase, trim, then SHA-256, hex-encoded**. Anything else will
fail to join against a CRM using the convention, and the failure is silent.

Sanity-check the hashing implementation before trusting any comparison:

```
SHA-256("test") = 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
```

Then compare the observed value against the hash of the known account email. Use
`scripts/hash_probe.py` for the search rather than doing it in-page — it tests ~20 normalisations
across four algorithms and keeps the plaintext email out of the conversation transcript.

### Multiple conflicting hashes

Finding two or three different hashes of the same user's email in one session is common on sites with
more than one stack, and it is a serious finding with two distinct consequences:

1. **Attribution:** any downstream join on "the email hash" splits one customer into several
   identities depending on which stack the row came from. If the server container builds `user_id`
   from the wrong one, the export will not join to CRM at all.
2. **Media:** ad platforms receiving an incorrect hash report low or zero advanced-matching rates
   with no error surfaced. This is live, quantifiable waste, independent of any analytics project —
   worth escalating on its own track, since fixing it has immediate value and does not need to wait
   for a modelling roadmap.

When a hash matches no candidate recipe, say so plainly. It implies a salt, a different pre-image, or
a stale value on the account — and resolving it requires server-side code access. An unidentified
hash is a legitimate audit outcome; guessing is not.

### Shape is not identity

A 64-character lowercase hex string looks like SHA-256 of *something*, but it is not necessarily a
hashed person. Device and visitor IDs are frequently hashed to the same shape. Before treating any
64-hex value as the join key, confirm what it is a hash of — and if it cannot be confirmed, flag it
as a live risk of being mistaken for the join key during modelling. Check whether the value persists
across a logged-out and logged-in session: a value that is stable while the user is anonymous is a
device identifier, not a person identifier.

---

## Identifiers worth cataloguing

Build a single table of every person-level identifier observable in the browser. It is the raw
material for any join design, and having it in one place prevents the wrong column being picked
later.

| Identifier | What to record | Why it matters |
|---|---|---|
| GA4 `uid` | Present/absent, value, shape, which pages | The nominal join key |
| GA4 user properties (`up.*`) | All keys and values, or that none exist | No `up.*` means no user-scoped audiences are possible |
| Hashed email fields | Every variant, with its location and verified recipe | The likely join key; conflicts are a P0 |
| Unhashed CRM / contact ID | Value, and where it is transmitted | Often the strongest authoritative key — but a privacy exposure if sent to third parties |
| Hashed CRM ID | Value and algorithm shape (32 hex = MD5, 40 = SHA-1, 64 = SHA-256) | Algorithm inconsistency across fields is itself a defect |
| Loyalty / membership number | Present or absent | If absent, loyalty data can only be joined via another key — worth knowing early |
| Client ID (`_ga`, `cid`) | Value | Device-scoped fallback for unauthenticated attribution |
| Visitor ID | Value, format, whether stable across auth state | Frequently confused with a person hash |
| First-party server cookie (`FPID`) | Present or absent | Absence means a server-side tagging deployment is not delivering its cookie-durability benefit |

---

## Privacy findings to look for

The identity phase surfaces privacy issues as a by-product. Treat them as findings in their own
right, not as footnotes — they usually need a different owner and a faster timeline than the
analytics work.

- **Plaintext email or phone in the `dataLayer`.** Readable by every tag in the container and by any
  script on the page, including session replay. List the other tags in that container to show the
  exposure surface concretely.
- **Plaintext PII transmitted as an analytics parameter** (e.g. `ep.user_data.email`). Materially
  different from sending a hash. Note whether a consent mechanism exists — if not, the two findings
  compound.
- **Unhashed CRM primary key sent to analytics or ad platforms.** Lands in the export and in a third
  party's systems. Ask whether there is a documented, reviewed reason.
- **Session replay not masking identity fields.** If replay runs in a container that can read a
  plaintext PII field, flag it for verification.

Where a plaintext transmission appears deliberate — for instance, sending a raw email so the server
container can hash it — say so. That makes it a decision requiring documentation and legal review
rather than a bug, which is a different conversation and a fairer framing.
