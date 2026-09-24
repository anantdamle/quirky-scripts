#!/usr/bin/env python3
"""
gtm_container_audit.py - Fetch and audit public Google Tag Manager containers.

Resolves tags against triggers to answer the question network observation cannot:
not "what fired" but "what was supposed to fire, and why didn't it".

Usage:
    python3 gtm_container_audit.py GTM-XXXXXXX [GTM-YYYYYYY ...]
    python3 gtm_container_audit.py GTM-XXXXXXX --json
    python3 gtm_container_audit.py GTM-XXXXXXX --all-tags --raw-dir ./containers

Standard library only - no dependencies.

Two limits worth knowing before relying on the output:

1. Server-side GTM container configuration is NOT publicly fetchable for any
   site. A first-party endpoint serving /gtm.js is proxying the *client*
   container.
2. Paused tags are omitted from published containers entirely. A tag absent here
   may be paused rather than never built - you cannot distinguish the two from
   outside, so report it as "no tag in the published container" rather than
   asserting it was never created.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

GTM_URL = "https://www.googletagmanager.com/gtm.js?id={}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

TAG_TYPES = {
    "__gaawe": "GA4 event",
    "__googtag": "Google tag (config)",
    "__gaawc": "GA4 config (legacy)",
    "__ua": "Universal Analytics (DEPRECATED)",
    "__fls": "Floodlight sales",
    "__flc": "Floodlight counter",
    "__sp": "Google Ads remarketing",
    "__awct": "Google Ads conversion",
    "__gclidw": "Conversion linker",
    "__cl": "Click listener",
    "__html": "Custom HTML",
    "__img": "Custom image pixel",
}

ECOMMERCE_EVENTS = [
    "view_item_list", "select_item", "view_item", "add_to_cart",
    "remove_from_cart", "view_cart", "begin_checkout", "add_shipping_info",
    "add_payment_info", "purchase", "refund",
]

OPS = {
    "_eq": "equals", "_sw": "starts with", "_ew": "ends with", "_cn": "contains",
    "_re": "matches RegExp", "_lt": "<", "_le": "<=", "_gt": ">", "_ge": ">=",
    "_css": "matches CSS", "_um": "URL matches",
}

MAP_RE = re.compile(r"""["']([\w.\-]+)["']\s*:\s*["']([\w.\-]+)["']""")
BRANCH_RE = re.compile(r"""==\s*["']([^"']+)["'][^{;]*?return\s*["']([\w_]+)["']""")


# ---------------------------------------------------------------- fetch / parse

def fetch_container(container_id, raw_dir=None):
    url = GTM_URL.format(container_id)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} fetching {container_id} (does it exist / is it published?)"
    except Exception as e:
        return None, f"Failed to fetch {container_id}: {e}"

    if raw_dir:
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, f"{container_id}.js"), "w", encoding="utf-8") as f:
            f.write(body)

    data = _extract_json(body)
    if data is None:
        return None, (f"Could not locate the container data object in {container_id}. "
                      "Google may have changed the payload shape - use --raw-dir "
                      "and inspect by hand.")
    return data, None


def _extract_json(body):
    """
    The container ships as `var data = { "resource": {...}, ... };`. Locate the
    opening brace and walk forward tracking depth, capturing the whole object
    without needing a JS parser.
    """
    start = None
    for marker in ('var data = {', 'var data={'):
        idx = body.find(marker)
        if idx != -1:
            start = body.index('{', idx)
            break
    if start is None:
        m = re.search(r'"resource"\s*:\s*\{', body)
        if not m:
            return None
        start = body.rindex('{', 0, m.start())

    depth, in_str, esc = 0, False, False
    for i in range(start, len(body)):
        ch = body[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(body[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ------------------------------------------------------------ macros and names

def _is_event_macro(macros, i):
    """__e is GTM's built-in {{Event}} variable - the dataLayer event name."""
    return 0 <= i < len(macros) and macros[i].get("function") == "__e"


def _metadata_name(obj):
    """Published containers keep names in metadata as ["map","name","..."]."""
    md = obj.get("metadata")
    if isinstance(md, list):
        for j in range(1, len(md) - 1):
            if md[j] == "name":
                return md[j + 1]
    return None


def _macro_label(macros, i):
    if not (0 <= i < len(macros)):
        return f"{{{{macro {i}}}}}"
    m = macros[i]
    if m.get("function") == "__e":
        return "Event"
    return f"{{{{{_metadata_name(m) or f'macro {i}'}}}}}"


def _resolve_ref(val, macros):
    if isinstance(val, list) and len(val) == 2 and val[0] == "macro":
        return _macro_label(macros, val[1])
    return str(val) if val is not None else None


def _lookup_entries(m):
    """Yield (key, value_ref) for a GTM lookup/regex-map macro."""
    raw = m.get("vtp_map")
    if not isinstance(raw, list):
        return
    for entry in raw:
        if isinstance(entry, list) and "key" in entry and "value" in entry:
            yield entry[entry.index("key") + 1], entry[entry.index("value") + 1]


def _reachable_ids(val, macros, depth=0, seen=None):
    """Every G-XXXXXXX reachable through this reference, following indirection."""
    out = set()
    if depth > 8 or val is None:
        return out
    if isinstance(val, str):
        if val.startswith("G-"):
            out.add(val)
        return out
    if isinstance(val, list) and len(val) == 2 and val[0] == "macro":
        i = val[1]
        if not (isinstance(i, int) and 0 <= i < len(macros)):
            return out
        seen = seen or set()
        if i in seen:
            return out
        seen = seen | {i}
        m = macros[i]
        fn = m.get("function")
        if fn == "__c":
            return _reachable_ids(m.get("vtp_value"), macros, depth + 1, seen)
        if fn in ("__remm", "__smm"):
            for _k, v in _lookup_entries(m):
                out |= _reachable_ids(v, macros, depth + 1, seen)
            out |= _reachable_ids(m.get("vtp_defaultValue"), macros, depth + 1, seen)
    return out


# Lookup keys that indicate the live/production branch. Bot-flag tables keyed on
# "human" are as common as environment tables keyed on "prod".
LIVE_KEYS = ("prod", "production", "live", "human", "real")

# Keys that denote a NON-production environment. When every key in a lookup is one
# of these, the default value is the production branch. This is the inverse of the
# pattern LIVE_KEYS handles, and real containers use both: one site routes
# prod via an explicit key with UAT as the default, another lists only its
# non-prod hostnames and lets production fall through to the default. Neither
# convention can be assumed, which is why unresolved cases are reported rather
# than guessed.
NONPROD_KEYS = ("sit", "uat", "qa", "preview", "next", "dev", "stag", "test",
                "local", "bot", "nonprod", "non-prod", "preprod", "pre-prod",
                "sandbox", "demo")


def _is_nonprod_key(k):
    return isinstance(k, str) and any(t in k.strip().lower() for t in NONPROD_KEYS)


def _preferred_id(val, macros, depth=0, seen=None):
    """
    Follow the branch a real user on production would take.

    Containers routinely nest lookups - hostname -> bot flag -> ID, or hostname ->
    locale -> ID - so this recurses rather than trusting vtp_defaultValue, which
    may be either the production value or the UAT fallback depending on the
    container's convention. Silently taking the default is how you end up
    confidently naming the wrong property.
    """
    if depth > 8 or val is None:
        return None
    if isinstance(val, str):
        return val if val.startswith("G-") else None
    if not (isinstance(val, list) and len(val) == 2 and val[0] == "macro"):
        return None
    i = val[1]
    if not (isinstance(i, int) and 0 <= i < len(macros)):
        return None
    seen = seen or set()
    if i in seen:
        return None
    seen = seen | {i}

    m = macros[i]
    fn = m.get("function")
    if fn == "__c":
        return _preferred_id(m.get("vtp_value"), macros, depth + 1, seen)
    if fn not in ("__remm", "__smm"):
        return None

    entries = list(_lookup_entries(m))

    # A key naming the live branch wins outright.
    for k, v in entries:
        if isinstance(k, str) and any(t in k.strip().lower() for t in LIVE_KEYS):
            ids = _reachable_ids(v, macros, depth + 1, seen)
            return _preferred_id(v, macros, depth + 1, seen) or (
                next(iter(ids)) if len(ids) == 1 else None)

    # Every mapped key is a non-production environment, so production falls
    # through to the default.
    if entries and all(_is_nonprod_key(k) for k, _v in entries):
        dflt = m.get("vtp_defaultValue")
        branch_ids = set()
        for _k, v in entries:
            branch_ids |= _reachable_ids(v, macros, depth + 1, seen)
        dflt_ids = _reachable_ids(dflt, macros, depth + 1, seen)
        # Only trust this when the default is genuinely distinct from the
        # non-prod branches - otherwise the inference tells us nothing.
        if dflt_ids and not (dflt_ids & branch_ids):
            return _preferred_id(dflt, macros, depth + 1, seen) or (
                next(iter(dflt_ids)) if len(dflt_ids) == 1 else None)

    # Otherwise recurse into the branches - the live marker is often one level down.
    found = {r for r in (_preferred_id(v, macros, depth + 1, seen) for _k, v in entries) if r}
    if len(found) == 1:
        return next(iter(found))
    # Unambiguous single-valued branch (excluding the fallback default).
    branch_ids = set()
    for _k, v in entries:
        branch_ids |= _reachable_ids(v, macros, depth + 1, seen)
    return next(iter(branch_ids)) if len(branch_ids) == 1 else None


def resolve_measurement_id(val, macros, observed=None):
    """
    Resolve a measurement-ID reference to an actual G-XXXXXXX.

    This matters more than it looks. Two tags emitting the same event on the same
    trigger only double-count if they target the SAME property; sending one event
    to two different properties is ordinary dual-tagging. Grouping without
    resolving the ID produces confident false positives.

    `observed` is the set of measurement IDs actually seen on the wire during the
    browser audit. The wire says which property is live; the container says how
    routing works. Neither alone is sufficient, so where the container is
    ambiguous and exactly one candidate was observed live, that one is chosen.

    Returns (id_or_None, description). Remaining ambiguity is reported, not
    guessed.
    """
    if val is None:
        return None, "not set (inherits from the config tag)"
    ids = _reachable_ids(val, macros)
    chosen = _preferred_id(val, macros)
    if chosen:
        extra = sorted(ids - {chosen})
        desc = chosen + (f" (lookup; other branches: {'|'.join(extra)})" if extra else "")
        return chosen, desc
    if len(ids) == 1:
        only = next(iter(ids))
        return only, only
    if ids and observed:
        hits = sorted(ids & set(observed))
        if len(hits) == 1:
            others = sorted(ids - {hits[0]})
            return hits[0], (f"{hits[0]} (matched an observed wire ID; "
                             f"other branches: {'|'.join(others)})")
    if ids:
        return None, "AMBIGUOUS lookup: " + "|".join(sorted(ids))
    return None, _resolve_ref(val, macros) or "unresolved"


def _js_template_to_string(val):
    """
    Custom JS is stored as a GTM template array:
    ["template", "code...", ["escape", ["macro", N], ...], "more code..."].
    Join the literal parts so embedded mapping tables become greppable.
    """
    if isinstance(val, str):
        return val
    if not isinstance(val, list):
        return ""
    return "".join(el for el in val if isinstance(el, str) and el != "template")


# ----------------------------------------------------------------- predicates

def describe_predicate(pred, macros):
    fn = pred.get("function", "?")
    negate = fn.startswith("!")
    op = OPS.get(fn.lstrip("!"), fn)
    left = _resolve_ref(pred.get("arg0"), macros)
    return f"{left} {'not ' if negate else ''}{op} {pred.get('arg1', '')!r}"


def _event_predicate(pred, macros):
    """
    If this predicate constrains the dataLayer event name, return (op, value).
    Negated predicates return None - they exclude rather than select.
    """
    fn = pred.get("function", "")
    if fn.startswith("!"):
        return None
    a0 = pred.get("arg0")
    if not (isinstance(a0, list) and len(a0) == 2 and a0[0] == "macro"
            and _is_event_macro(macros, a0[1])):
        return None
    return (fn, str(pred.get("arg1", "")))


def _matches(op, pattern, event):
    try:
        if op == "_eq":
            return event == pattern
        if op == "_sw":
            return event.startswith(pattern)
        if op == "_ew":
            return event.endswith(pattern)
        if op == "_cn":
            return pattern in event
        if op == "_re":
            return re.search(pattern, event) is not None
    except re.error:
        return False
    return False


# --------------------------------------------------------------------- triggers

def build_trigger_index(rules, predicates, macros):
    """
    A rule is a list of clauses: ["if", i, ...], ["unless", i, ...],
    ["add", tagIdx, ...], ["block", tagIdx, ...]. add/block indices are positions
    in the tags array.

    Per firing rule we also record whether its positive conditions are composed
    ONLY of event-name constraints. That distinction drives double-fire analysis:
    a tag that fires unconditionally on an event will always fire alongside
    another such tag, whereas a tag with extra discriminating conditions (click
    text, page path) usually will not.
    """
    index = {}
    for rule in rules:
        if_ids, unless_ids, add_ids, block_ids = [], [], [], []
        for clause in rule:
            if not isinstance(clause, list) or not clause:
                continue
            verb, ids = clause[0], [x for x in clause[1:] if isinstance(x, int)]
            {"if": if_ids, "unless": unless_ids,
             "add": add_ids, "block": block_ids}.get(verb, []).extend(ids)

        if_preds = [predicates[i] for i in if_ids if 0 <= i < len(predicates)]
        unless_preds = [predicates[i] for i in unless_ids if 0 <= i < len(predicates)]

        conds = [describe_predicate(p, macros) for p in if_preds]
        conds += [f"UNLESS {describe_predicate(p, macros)}" for p in unless_preds]
        desc = " AND ".join(conds) if conds else "(always)"

        ev_constraints = [c for c in (_event_predicate(p, macros) for p in if_preds) if c]
        # Unconditional if every positive condition is an event constraint.
        unconditional = bool(ev_constraints) and len(ev_constraints) == len(if_preds)

        for ti in add_ids:
            index.setdefault(ti, {"fire": [], "block": [], "fire_rules": []})
            index[ti]["fire"].append(desc)
            index[ti]["fire_rules"].append(
                {"event_constraints": ev_constraints, "unconditional": unconditional})
        for ti in block_ids:
            index.setdefault(ti, {"fire": [], "block": [], "fire_rules": []})
            index[ti]["block"].append(desc)
    return index


def collect_event_universe(predicates, macros):
    """Every literal dataLayer event name referenced anywhere in the container."""
    out = set()
    for p in predicates:
        c = _event_predicate(p, macros)
        if c and c[0] == "_eq" and c[1]:
            out.add(c[1])
    return out


# ------------------------------------------------------- dynamic event mappings

def extract_js_mappings(macros):
    """
    Custom JavaScript macros mapping internal event names to GA4 event names.

    Two kinds:
      - direct:      dataLayer event -> GA4 event. Usable for double-fire
                     analysis, because the key IS the event.
      - conditional: some other variable's value -> GA4 event (e.g. a checkout
                     step name). Reported as the spec, but not used for
                     double-fire analysis since the key is not the event.
    """
    out = []
    for i, m in enumerate(macros):
        if m.get("function") != "__jsm":
            continue
        js = _js_template_to_string(m.get("vtp_javascript"))
        if not js:
            continue

        direct = {k: v for k, v in MAP_RE.findall(js)
                  if v in ECOMMERCE_EVENTS or k in ECOMMERCE_EVENTS}
        conditional = {c: r for c, r in BRANCH_RE.findall(js)
                       if r in ECOMMERCE_EVENTS and c not in direct}

        if direct or conditional:
            out.append({
                "macro_index": i,
                "name": _metadata_name(m) or f"macro {i}",
                "mappings": sorted(direct.items()),
                "conditional_mappings": sorted(conditional.items()),
                "drops_unmapped": "undefined" in js,
            })
    return out


def _mapping_lookup(js_mappings, dl_event):
    for m in js_mappings:
        for k, v in m["mappings"]:
            if k == dl_event:
                return v
    return None


# ------------------------------------------------------------- targeted checks
#
# Each check below automates something that was first found by hand during a real
# audit. They are static checks over the container, so they complement rather than
# replace the browser pass: the container says what is configured, the wire says
# what actually happens.

LOCALE_KEYS = ("au", "nz", "uk", "gb", "us", "ca", "ie", "sg", "hk", "my",
               "en-au", "en-nz", "en-gb", "en-us")

PII_PARAM_PAT = re.compile(
    r"(e_?mail|phone|mobile|first_?name|last_?name|full_?name|surname|"
    r"street|address|postcode|zip|dob|birth|crm_?id|contact_?id|customer_?id|"
    r"member_?id|loyalty_?id|user_?data)", re.I)

# Params that are hashed or explicitly non-identifying are not the concern here.
PII_SAFE_PAT = re.compile(r"(hash|hashed|sha256|sha1|md5|_sha|digest)", re.I)

APP_MARKER_PAT = re.compile(r"(webview|app-?android|app-?ios|djapp|-app\b|_app\b|"
                            r"android|ios|mobile_?app)", re.I)

UA_ID_PAT = re.compile(r"\bUA-\d{4,}-\d+\b")

CONSENT_HINT_PAT = re.compile(
    r"(onetrust|optanon|cookiebot|sourcepoint|quantcast|didomi|usercentrics|"
    r"osano|trustarc|ketch|cookieyes|iubenda|consent)", re.I)


def _settings_sources(tag, macros):
    """
    A tag's own config plus any Google Tag Event Settings (__gtes) variables it
    references.

    Containers frequently centralise parameters and user properties in a shared
    settings variable rather than repeating them per tag. Reading only the tag
    misses all of it - which is easy to do, and makes a container look as though it
    sends no parameters or user properties at all.
    """
    sources = [tag]
    for key in ("vtp_eventSettingsVariable", "vtp_userDataVariable",
                "vtp_configSettingsVariable"):
        ref = tag.get(key)
        if isinstance(ref, list) and len(ref) == 2 and ref[0] == "macro":
            i = ref[1]
            if isinstance(i, int) and 0 <= i < len(macros):
                sources.append(macros[i])
    return sources


def _tag_param_names(tag, macros=None):
    """
    Extract configured parameter / user-property names from a GA4 tag.

    GTM stores these as ["list", ["map","parameter",NAME,"parameterValue",REF], ...].
    Only names are collected - values are macro references, and the point is to
    flag what kind of data a tag is configured to send.
    """
    names = []
    for src in (_settings_sources(tag, macros) if macros is not None else [tag]):
        for key in ("vtp_eventSettingsTable", "vtp_userProperties",
                    "vtp_eventParameters", "vtp_userDataTable",
                    "vtp_configSettingsTable"):
            raw = src.get(key)
            if not isinstance(raw, list):
                continue
            for entry in raw:
                if not isinstance(entry, list):
                    continue
                for j in range(len(entry) - 1):
                    if entry[j] in ("parameter", "name", "key") and isinstance(entry[j + 1], str):
                        names.append(entry[j + 1])
    return names


def check_property_inventory(macros):
    """
    Every GA4 property reachable in this container, and how it is selected.

    Containers commonly hold production, UAT, bot and per-locale properties. If the
    data export is wired to the wrong one it will look like a modelling problem
    when it is a plumbing one, so the full inventory is worth stating explicitly.
    """
    ids, locale_split = {}, {}
    for i, m in enumerate(macros):
        if m.get("function") == "__c":
            v = m.get("vtp_value")
            if isinstance(v, str) and v.startswith("G-"):
                ids.setdefault(v, []).append(f"constant macro {i}")
    for i, m in enumerate(macros):
        if m.get("function") not in ("__remm", "__smm"):
            continue
        for key, val in _lookup_entries(m):
            if not isinstance(key, str):
                continue
            for rid in _reachable_ids(val, macros):
                ids.setdefault(rid, []).append(f"macro {i} key {key!r}")
                if key.strip().lower() in LOCALE_KEYS:
                    locale_split.setdefault(key.strip().lower(), set()).add(rid)
    return {
        "properties": {k: sorted(set(v))[:4] for k, v in sorted(ids.items())},
        "locale_split": {k: sorted(v) for k, v in sorted(locale_split.items())},
    }


def check_legacy_analytics(res, tags):
    """
    Universal Analytics traces. Checked two ways because they diverge: a UA tag in
    the container, and UA property IDs referenced anywhere (often only in a lookup
    macro). Where UA hits appear on the wire but no UA tag exists here, the tag is
    hardcoded in the page template - so editing GTM will not remove it, which is
    the fix people reach for first.
    """
    ua_tags = [i for i, t in enumerate(tags) if t.get("function") == "__ua"]
    ids = sorted(set(UA_ID_PAT.findall(json.dumps(res))))
    return {"ua_tags": ua_tags, "ua_ids_referenced": ids}


def check_app_suppression(ga4_tags):
    """
    GA4 events suppressed for in-app / WebView traffic via blocking rules.

    Separates a blanket container-wide exclusion - one decision affecting everything,
    which should be reported once - from per-event suppression, which is a
    deliberate choice about specific events and usually the more interesting finding.

    Either way: app suppression means the app's events are reported by a native SDK
    elsewhere or not measured at all, which decides whether analytics can be the
    source for app conversions. It also reveals that the app renders these web
    pages, without needing to inspect the app.
    """
    blanket_rules, blanket_tags, specific = {}, set(), {}
    for t in ga4_tags:
        hits = [b for b in t["blocked_by"] if APP_MARKER_PAT.search(b)]
        if not hits:
            continue
        ev = t["event_name"] if isinstance(t["event_name"], str) else f"tag {t['index']}"
        for rule in hits:
            # A rule matching every event is a container-wide exclusion.
            if "Event matches RegExp '.*'" in rule:
                blanket_rules[rule] = blanket_rules.get(rule, 0) + 1
                blanket_tags.add(t["index"])
            else:
                specific.setdefault(ev, {"tags": set(), "rules": set()})
                specific[ev]["tags"].add(t["index"])
                specific[ev]["rules"].add(rule)
    return {
        "blanket_rules": [{"rule": k, "tag_count": v} for k, v in
                          sorted(blanket_rules.items(), key=lambda kv: -kv[1])],
        "blanket_tag_count": len(blanket_tags),
        "per_event": {k: {"tags": sorted(v["tags"]),
                          "example_rules": sorted(v["rules"])[:2]}
                      for k, v in sorted(specific.items())},
    }


def check_pii_parameters(tags, macros):
    """
    Tags configured to send parameters whose NAMES suggest personal data.

    Names only - values are macro references. A parameter called `email` may carry
    a hash, but it may equally carry the raw address, and that distinction is worth
    confirming on the wire. Names that already say hashed are excluded.

    Results are grouped by parameter set: a shared event-settings variable applies
    the same parameters to every tag that references it, which is ONE configuration
    decision, not one finding per tag.
    """
    groups = {}
    for i, t in enumerate(tags):
        if t.get("function") != "__gaawe":
            continue
        flagged = tuple(sorted({n for n in _tag_param_names(t, macros)
                                if PII_PARAM_PAT.search(n) and not PII_SAFE_PAT.search(n)}))
        if flagged:
            ev = t.get("vtp_eventName")
            g = groups.setdefault(flagged, {"tags": [], "events": set()})
            g["tags"].append(i)
            g["events"].add(ev if isinstance(ev, str) else "(dynamic)")
    return [{"parameters": list(k), "tags": v["tags"],
             "events": sorted(v["events"])}
            for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]["tags"]))]


def _tag_user_properties(tag, macros=None):
    """Configured GA4 user-property names, from ['list', ['map','name',N,'value',REF]]."""
    names = []
    for src in (_settings_sources(tag, macros) if macros is not None else [tag]):
        raw = src.get("vtp_userProperties")
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if not isinstance(entry, list):
                continue
            for j in range(len(entry) - 1):
                if entry[j] == "name" and isinstance(entry[j + 1], str):
                    names.append(entry[j + 1])
    return names


ECOM_PARAM_NAMES = {"items", "value", "currency", "transaction_id", "tax", "shipping"}


def check_ecommerce_data_path(ga4_tags, tags, macros):
    """
    Ecommerce-event tags that send neither the ecommerce object NOR the ecommerce
    parameters individually.

    `sendEcommerceData: false` alone is not a defect - mapping `items`, `value` and
    `currency` by hand is a legitimate alternative, and flagging it would be a false
    positive. Only a tag doing neither will produce an event with no items or
    revenue, which reads as an upstream dataLayer problem but is a tag-config one.
    """
    out = []
    for t in ga4_tags:
        ev = t["event_name"]
        if not (isinstance(ev, str) and ev in ECOMMERCE_EVENTS):
            continue
        if t["send_ecommerce_data"]:
            continue
        manual = set(_tag_param_names(tags[t["index"]], macros)) & ECOM_PARAM_NAMES
        if not manual:
            out.append({"index": t["index"], "event": ev, "name": t["name"]})
    return out


IDENTITY_NAME_PAT = re.compile(
    r"(user_?id|customer_?id|crm_?id|contact_?id|member_?id|loyalty|"
    r"e_?mail|phone|hash)", re.I)


def check_identity_wiring(tags, ga4_tags, macros):
    """
    Identity configured at the container level, which the wire may not reveal.

    User properties and parameters only appear on hits for the events their tag
    fires on. Identity wired onto a `purchase` tag will never be seen in a browser
    session that does not complete a purchase - so the container is the only
    practical way to find it, and it may be exactly where the join key lives.

    Crucially, GA4 `user_id` is normally set once on the CONFIG tag (__googtag) via
    its config settings variable, NOT on individual event tags. Scanning only event
    tags will report no user_id on a site that plainly sets one, so both are checked
    here. Absence across both is strong corroboration that user_id is not set
    client-side, and therefore that anything in the export arrives via server-side
    tagging or the Measurement Protocol.
    """
    per_tag, all_props, user_id_sources = {}, {}, []

    # GA4 config tags: where user_id is usually set, once, for everything.
    for i, tag in enumerate(tags):
        if tag.get("function") not in ("__googtag", "__gaawc", "__gaawe"):
            continue
        names = _tag_param_names(tag, macros)
        if any(n.lower() in ("user_id", "userid") for n in names):
            kind = ("config tag" if tag.get("function") in ("__googtag", "__gaawc")
                    else "event tag")
            user_id_sources.append({"index": i, "kind": kind})
        # A real GA4 user_id can also be its own config field.
        for k, v in tag.items():
            if k.lower().replace("_", "") in ("vtpuserid", "vtpuseridvalue") and v:
                user_id_sources.append({"index": i, "kind": "userId field"})

    for t in ga4_tags:
        tag = tags[t["index"]]
        props = _tag_user_properties(tag, macros)
        ident_params = sorted({n for n in _tag_param_names(tag, macros)
                               if IDENTITY_NAME_PAT.search(n)})
        if props or ident_params:
            ev = t["event_name"] if isinstance(t["event_name"], str) else "(dynamic)"
            per_tag[t["index"]] = {"event": ev, "user_properties": sorted(set(props)),
                                   "identity_parameters": ident_params}
        for p in props:
            all_props.setdefault(p, []).append(t["index"])

    # De-duplicate while preserving order.
    seen, uniq = set(), []
    for s in user_id_sources:
        key = (s["index"], s["kind"])
        if key not in seen:
            seen.add(key)
            uniq.append(s)

    return {
        "user_id_sources": uniq,
        "user_properties": {k: sorted(v) for k, v in sorted(all_props.items())},
        "per_tag": per_tag,
    }


def check_consent_configuration(tags, macros):
    """
    Any consent-management or Consent Mode signal in the container.

    Absence here is not proof of absence on the site - a CMP can load outside GTM -
    but it is a strong hint, and it tells you where to look during the browser pass.
    """
    hits = []
    for i, t in enumerate(tags):
        name = _metadata_name(t) or ""
        fn = t.get("function", "")
        if CONSENT_HINT_PAT.search(name) or CONSENT_HINT_PAT.search(fn):
            hits.append({"kind": "tag", "index": i, "name": name or fn})
    for i, m in enumerate(macros):
        name = _metadata_name(m) or ""
        if CONSENT_HINT_PAT.search(name):
            hits.append({"kind": "variable", "index": i, "name": name})
    return hits


def check_event_naming_conventions(ga4_tags, universe):
    """
    Whether this container listens for GA4-native ecommerce event names, legacy
    namespaced ones, or both.

    A container listening for legacy names while another stack pushes GA4-native
    ones is why two stacks cannot share tagging - and the mismatch is silent.
    """
    native = sorted({e for e in universe if e in ECOMMERCE_EVENTS})
    legacy = sorted({e for e in universe
                     if ("." in e and not e.startswith("gtm."))})
    return {"native_ecommerce_events": native, "namespaced_events": legacy[:25]}


# ------------------------------------------------------------------- the audit

def audit(container_id, data, include_all=False, observed_ids=None):
    res = data.get("resource", data)
    tags = res.get("tags") or []
    predicates = res.get("predicates") or []
    rules = res.get("rules") or []
    macros = res.get("macros") or []

    triggers = build_trigger_index(rules, predicates, macros)
    universe = collect_event_universe(predicates, macros)
    js_mappings = extract_js_mappings(macros)

    result = {
        "container_id": container_id,
        "version": str(res.get("version", "unknown")),
        "counts": {"tags": len(tags), "predicates": len(predicates),
                   "rules": len(rules), "macros": len(macros)},
        "ga4_event_tags": [],
        "other_tags": [],
        "double_fire_confirmed": [],
        "double_fire_possible": [],
        "measurement_ids": {},
        "unresolved_property_tags": [],
        "missing_ecommerce_events": [],
        "deprecated": [],
        "js_event_mappings": js_mappings,
    }

    # (dl_event, ga4_event, resolved_measurement_id) -> [(tag_index, unconditional)]
    emissions = {}
    measurement_ids = {}

    for i, tag in enumerate(tags):
        fn = tag.get("function", "")
        name = _metadata_name(tag) or f"tag_id {tag.get('tag_id', '?')}"
        trig = triggers.get(i, {"fire": [], "block": [], "fire_rules": []})

        if fn == "__ua":
            result["deprecated"].append({"index": i, "name": name, "type": TAG_TYPES[fn]})

        if fn != "__gaawe":
            if include_all:
                result["other_tags"].append(
                    {"index": i, "name": name, "type": TAG_TYPES.get(fn, fn),
                     "fires_on": trig["fire"], "blocked_by": trig["block"]})
            continue

        raw_ev = tag.get("vtp_eventName")
        dynamic = isinstance(raw_ev, list)
        event_name = _resolve_ref(raw_ev, macros) if dynamic else raw_ev
        mid, mid_desc = resolve_measurement_id(
            tag.get("vtp_measurementIdOverride"), macros, observed_ids)
        if mid:
            measurement_ids[mid] = measurement_ids.get(mid, 0) + 1
        elif "AMBIGUOUS" in (mid_desc or ""):
            result["unresolved_property_tags"].append(i)

        # Which dataLayer events reach this tag, and via an unconditional rule?
        dl_events = {}
        for fr in trig["fire_rules"]:
            if not fr["event_constraints"]:
                continue
            for ev in universe:
                if all(_matches(op, pat, ev) for op, pat in fr["event_constraints"]):
                    dl_events[ev] = dl_events.get(ev, False) or fr["unconditional"]

        for dl, uncond in dl_events.items():
            emitted = event_name
            if dynamic:
                emitted = _mapping_lookup(js_mappings, dl) or event_name
            if isinstance(emitted, str) and not emitted.startswith("{{"):
                # Key on measurement ID so dual-property tagging is not mistaken
                # for double-counting. Unresolved IDs group under a sentinel and
                # are flagged as needing manual confirmation.
                emissions.setdefault(
                    (dl, emitted, mid or "?unresolved"), []).append(
                        (i, uncond, tuple(trig["fire"])))

        result["ga4_event_tags"].append({
            "index": i,
            "name": name,
            "event_name": event_name,
            "dynamic_event_name": dynamic,
            "triggering_datalayer_events": sorted(dl_events),
            "measurement_id": mid,
            "measurement_id_desc": mid_desc,
            "send_ecommerce_data": tag.get("vtp_sendEcommerceData"),
            "ecommerce_data_source": tag.get("vtp_getEcommerceDataFrom"),
            "fires_on": trig["fire"],
            "blocked_by": trig["block"],
        })

    for (dl, ga4, mid), entries in sorted(emissions.items()):
        if len(entries) < 2:
            continue
        uncond = [t for t, u, _f in entries if u]

        # Two tags sharing a byte-identical firing rule will always fire together,
        # however many conditions that rule has. That is a stronger signal than an
        # unconditional trigger and catches duplicates the unconditional test misses.
        rule_owners = {}
        for t, _u, fires in entries:
            for desc in fires:
                rule_owners.setdefault(desc, set()).add(t)
        shared = {desc: sorted(ts) for desc, ts in rule_owners.items() if len(ts) > 1}

        record = {"datalayer_event": dl, "ga4_event": ga4, "measurement_id": mid,
                  "tags": [t for t, _u, _f in entries],
                  "unconditional_tags": uncond,
                  "shared_rules": shared}
        if len(uncond) >= 2 or shared:
            result["double_fire_confirmed"].append(record)
        else:
            result["double_fire_possible"].append(record)

    result["measurement_ids"] = dict(
        sorted(measurement_ids.items(), key=lambda kv: -kv[1]))

    covered = {ga4 for _, ga4, _ in emissions}
    covered |= {t["event_name"] for t in result["ga4_event_tags"]
                if isinstance(t["event_name"], str)}
    covered |= {v for m in js_mappings for _, v in m["mappings"]}
    covered |= {v for m in js_mappings for _, v in m["conditional_mappings"]}
    result["missing_ecommerce_events"] = [e for e in ECOMMERCE_EVENTS if e not in covered]

    result["checks"] = {
        "property_inventory": check_property_inventory(macros),
        "legacy_analytics": check_legacy_analytics(res, tags),
        "app_suppression": check_app_suppression(result["ga4_event_tags"]),
        "pii_parameters": check_pii_parameters(tags, macros),
        "ecommerce_data_path": check_ecommerce_data_path(
            result["ga4_event_tags"], tags, macros),
        "identity_wiring": check_identity_wiring(
            tags, result["ga4_event_tags"], macros),
        "consent_configuration": check_consent_configuration(tags, macros),
        "event_naming": check_event_naming_conventions(result["ga4_event_tags"], universe),
    }
    return result


# -------------------------------------------------------------------- reporting

def print_checks(r):
    """Findings from the targeted checks, ordered roughly by severity."""
    ch = r.get("checks") or {}
    print("\n" + "-" * 78)
    print("TARGETED CHECKS")
    print("-" * 78)

    pii = ch.get("pii_parameters") or []
    if pii:
        print("\n[PRIVACY] GA4 tags configured to send parameters whose names suggest")
        print("personal data. Names only - values are macro references, so confirm on")
        print("the wire whether each carries a hash or a raw value. A raw email or an")
        print("unhashed CRM key reaching analytics is a materially different compliance")
        print("posture from a hash, and it also lands in the data export.")
        for g in pii:
            n = len(g["tags"])
            evs = ", ".join(g["events"][:6]) + (" ..." if len(g["events"]) > 6 else "")
            print(f"  {', '.join(g['parameters'])}")
            print(f"      on {n} tag(s): {g['tags'][:12]}"
                  + (" ..." if n > 12 else ""))
            print(f"      events: {evs}")
        if any(len(g["tags"]) > 5 for g in pii):
            print("  A set applied across many tags points to a shared event-settings")
            print("  variable - one configuration decision, not many. Fix it once.")

    eco = ch.get("ecommerce_data_path") or []
    if eco:
        print("\n[FUNNEL] Ecommerce-event tags NOT configured to send ecommerce data.")
        print("These fire but produce events with no items, revenue or currency - which")
        print("reads as an upstream dataLayer problem but is a tag-config problem.")
        for d in eco:
            print(f"  tag {d['index']}: {d['event']}  ({d['name']})")

    ident = ch.get("identity_wiring") or {}
    print("\n[IDENTITY] ", end="")
    srcs = ident.get("user_id_sources") or []
    if srcs:
        print("GA4 user_id IS configured in this container:")
        cfg = [s for s in srcs if s["kind"] != "event tag"]
        ev = [s["index"] for s in srcs if s["kind"] == "event tag"]
        for s in cfg:
            print(f"    {s['kind']} at tag index {s['index']}"
                  f"  <-- set once, applies to all events")
        if ev:
            print(f"    also on {len(ev)} event tag(s): {ev[:10]}"
                  + (" ..." if len(ev) > 10 else ""))
        print("  Confirm on the wire that it is populated, and verify the hashing")
        print("  recipe matches what the CRM stores - a user_id that is set but")
        print("  hashed differently will not join, and fails silently.")
    else:
        print("NO user_id is configured anywhere in this container.")
        print("  Checked both the GA4 config tag (where user_id is normally set once)")
        print("  and every event tag. Strong corroboration that user_id is not set")
        print("  client-side. If the data export nevertheless contains user_id, it is")
        print("  being injected by the server-side container or the Measurement")
        print("  Protocol - neither inspectable from outside, so request a container")
        print("  export rather than concluding the field is absent.")
    if ident.get("user_properties"):
        print("\n  GA4 user properties configured (name -> tags):")
        for name, idxs in ident["user_properties"].items():
            flag = "  <-- identity" if IDENTITY_NAME_PAT.search(name) else ""
            shown = idxs[:8]
            more = f" (+{len(idxs) - 8} more)" if len(idxs) > 8 else ""
            print(f"    {name:<24} tags {shown}{more}{flag}")
        print("  These appear ONLY on hits for the events their tag fires on. Identity")
        print("  wired onto a `purchase` tag will never show in a browser session that")
        print("  does not complete a purchase - so check the export for these as")
        print("  user-scoped fields even if the wire pass saw no up.* parameters.")
    for idx, d in sorted((ident.get("per_tag") or {}).items())[:4]:
        if d["identity_parameters"]:
            print(f"    e.g. tag {idx} ({d['event']}) identity params: "
                  f"{', '.join(d['identity_parameters'])}")

    app = ch.get("app_suppression") or {}
    if app.get("blanket_rules") or app.get("per_event"):
        print("\n[APP] GA4 events suppressed for in-app / WebView traffic.")
        print("Someone built app-specific exclusions here, which implies the app renders")
        print("these pages. Two consequences: app-originated sessions land in the same")
        print("property and must be segmented out to avoid double-counting, and any")
        print("suppressed conversion event cannot come from analytics for the app - it")
        print("must come from the order system.")
        for b in app["blanket_rules"]:
            print(f"  CONTAINER-WIDE exclusion on {b['tag_count']} tag(s): {b['rule']}")
        if app.get("per_event"):
            print(f"  Per-event suppression ({len(app['per_event'])} event(s)):")
            for ev, d in app["per_event"].items():
                print(f"    {ev}: tags {d['tags']}")
                for rule in d["example_rules"]:
                    print(f"        e.g. {rule}")

    legacy = ch.get("legacy_analytics") or {}
    if legacy.get("ua_tags") or legacy.get("ua_ids_referenced"):
        print("\n[DEPRECATED] Universal Analytics traces.")
        if legacy.get("ua_tags"):
            print(f"  UA tags in container: {legacy['ua_tags']}")
        if legacy.get("ua_ids_referenced"):
            print(f"  UA property IDs referenced: {', '.join(legacy['ua_ids_referenced'])}")
        if legacy.get("ua_ids_referenced") and not legacy.get("ua_tags"):
            print("  IDs are referenced but there is no UA tag here. If UA hits appear")
            print("  on the wire, the tag is hardcoded in the page template - editing")
            print("  GTM will NOT remove it, which is the fix people try first.")

    inv = ch.get("property_inventory") or {}
    props = inv.get("properties") or {}
    if len(props) > 1:
        print("\n[PROPERTIES] All GA4 properties reachable in this container:")
        for pid, how in props.items():
            print(f"  {pid}  via {'; '.join(how)}")
        print("  Confirm which one the data export is wired to. Getting this wrong")
        print("  makes a plumbing problem look like a modelling problem.")
    if inv.get("locale_split"):
        print("\n  Locale/region split detected - a single 'production' property may")
        print("  legitimately be several, and cross-market reporting needs union logic:")
        for k, ids in inv["locale_split"].items():
            print(f"    {k}: {', '.join(ids)}")

    naming = ch.get("event_naming") or {}
    if naming.get("native_ecommerce_events") and naming.get("namespaced_events"):
        print("\n[NAMING] This container listens for BOTH GA4-native ecommerce event")
        print("names and namespaced/legacy ones. Where two stacks each push only one")
        print("convention, the mismatch is silent - the container simply never matches.")
        print(f"  native:     {', '.join(naming['native_ecommerce_events'])}")
        print(f"  namespaced: {', '.join(naming['namespaced_events'][:10])}"
              + (" ..." if len(naming["namespaced_events"]) > 10 else ""))

    consent = ch.get("consent_configuration") or []
    print("\n[CONSENT] ", end="")
    if consent:
        print(f"{len(consent)} consent-related tag(s)/variable(s) found:")
        for c in consent[:8]:
            print(f"  {c['kind']} {c['index']}: {c['name']}")
    else:
        print("No consent-management or Consent Mode tag found in this container.")
        print("  Not proof of absence - a CMP can load outside GTM - but verify in the")
        print("  browser pass. Check google_tag_data.ics: active:false with all entries")
        print("  implicit:true means Consent Mode v2 is not implemented.")


def print_report(r):
    print(f"\n{'=' * 78}\n{r['container_id']}  (container version {r['version']})\n{'=' * 78}")
    c = r["counts"]
    print(f"{c['tags']} tags · {c['rules']} triggers · {c['macros']} variables · "
          f"{len(r['ga4_event_tags'])} GA4 event tags")
    print("Paused tags are omitted from published containers, so anything absent")
    print("here may be paused rather than never built.")

    if r["measurement_ids"]:
        print("\n### GA4 PROPERTIES TARGETED (resolved, tag count each)")
        for mid, n in r["measurement_ids"].items():
            print(f"  {mid}  ({n} tag{'s' if n != 1 else ''})")
        if len(r["measurement_ids"]) > 1:
            print("  More than one property receives events from this container.")
            print("  Confirm which one the data export comes from before analysing")
            print("  anything - coverage frequently differs sharply between them.")

    if r["unresolved_property_tags"]:
        n = len(r["unresolved_property_tags"])
        print(f"\n### {n} GA4 TAG(S) WITH AN UNRESOLVED PROPERTY")
        print("Their measurement ID comes from an environment lookup with several")
        print("possible values and no key this tool can confidently identify as the")
        print("live branch. Conventions genuinely differ between containers - some")
        print("route production via an explicit key and default to UAT, others list")
        print("only non-production hostnames and let production fall through to the")
        print("default - so guessing would be worse than reporting it.")
        print("  Re-run with --observed-id G-XXXXXXX using the measurement IDs seen")
        print("  on the wire during the browser audit to resolve these.")
        print("  Until then, treat the double-fire grouping below as provisional:")
        print("  tags targeting DIFFERENT properties may be grouped together.")
        print(f"  tags: {r['unresolved_property_tags']}")

    if r["double_fire_confirmed"]:
        print("\n### DOUBLE-FIRE - CONFIRMED. Investigate first.")
        print("Two or more tags emit the same GA4 event, to the SAME property, from the")
        print("same dataLayer push, with nothing separating them - either their triggers")
        print("are conditioned only on the event name, or two tags share a byte-identical")
        print("firing rule. For `purchase` this doubles transaction counts AND revenue.")
        print("Rank P0: unlike a missing event, which reads as an obvious zero, inflated")
        print("data looks plausible and goes unnoticed. Cheapest confirmation: compare")
        print("GA4 transactions against the order system - roughly 2x settles it without")
        print("a test purchase.")
        for d in r["double_fire_confirmed"]:
            tags = d["unconditional_tags"] or d["tags"]
            print(f"  ! {d['datalayer_event']!r} -> GA4 {d['ga4_event']!r} "
                  f"-> {d['measurement_id']}  tags {tags}")
            for desc, ts in d.get("shared_rules", {}).items():
                print(f"      tags {ts} share an identical rule: {desc}")

    if r["double_fire_possible"]:
        print("\n### DOUBLE-FIRE - POSSIBLE. Verify triggers by hand.")
        print("Same GA4 event from multiple tags on the same dataLayer event, but at")
        print("most one fires unconditionally - the others carry extra conditions")
        print("(click text, page path, custom variables) that probably separate them.")
        print("Usually benign: many tags legitimately emit one event from different")
        print("interactions. Read FIRES ON below before reporting any of these.")
        for d in r["double_fire_possible"]:
            print(f"  ? {d['datalayer_event']!r} -> GA4 {d['ga4_event']!r} "
                  f"-> {d['measurement_id']}  tags {d['tags']}")

    if r["deprecated"]:
        print("\n### DEPRECATED TAGS")
        for t in r["deprecated"]:
            print(f"  - tag {t['index']}: {t['name']} ({t['type']})")

    if r["missing_ecommerce_events"]:
        print("\n### ECOMMERCE EVENTS WITH NO TAG IN THIS CONTAINER")
        print("  " + ", ".join(r["missing_ecommerce_events"]))
        print("  Not necessarily a defect - check the other containers first. A")
        print("  storefront container legitimately has no `purchase` tag if checkout")
        print("  runs on a different stack.")

    if r["js_event_mappings"]:
        print("\n### DYNAMIC EVENT-NAME MAPPINGS (Custom JavaScript)")
        print("The definitive spec of what this container intends to send - often more")
        print("informative than what it currently does.")
        for m in r["js_event_mappings"]:
            print(f"\n  {m['name']} (macro {m['macro_index']})")
            for k, v in m["mappings"]:
                print(f"    {k:<44} -> {v}")
            for k, v in m["conditional_mappings"]:
                print(f"    [conditional on another variable] {k!r} -> {v}")
            if m["drops_unmapped"]:
                print("    ! References 'undefined' - events absent from this table are")
                print("      likely DROPPED SILENTLY with no error. Audit the table")
                print("      against every event the site actually pushes.")

    print("\n### GA4 EVENT TAGS")
    if not r["ga4_event_tags"]:
        print("  (none)")
    for t in r["ga4_event_tags"]:
        flags = []
        if t["dynamic_event_name"]:
            flags.append("dynamic name")
        if t["blocked_by"]:
            flags.append(f"{len(t['blocked_by'])} blocking rule(s)")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"\n  tag {t['index']}: {t['event_name']}{suffix}")
        print(f"    name: {t['name']}")
        print(f"    property: {t['measurement_id_desc']}")
        if t["triggering_datalayer_events"]:
            print(f"    dataLayer events: {', '.join(t['triggering_datalayer_events'])}")
        if t["send_ecommerce_data"]:
            print(f"    ecommerce: sendEcommerceData={t['send_ecommerce_data']} "
                  f"from={t['ecommerce_data_source']}")
        for f in t["fires_on"]:
            print(f"    FIRES ON:  {f}")
        for b in t["blocked_by"]:
            print(f"    BLOCKED:   {b}")

    if r["other_tags"]:
        print("\n### OTHER TAGS")
        for t in r["other_tags"]:
            print(f"  tag {t['index']}: {t['name']} ({t['type']})")

    print("\n### INTERPRETING A TAG THAT EXISTS BUT NEVER FIRES")
    print("If the network audit saw the event in dataLayer and other pixels consumed")
    print("it, but GA4 got nothing, and the tag above has a matching trigger with no")
    print("applicable blocking rule - the fault is INSIDE the tag, usually the")
    print("ecommerce-data path. Strengthen this by diffing against a tag that DOES")
    print("fire on the same page load: near-identical config except the ecommerce")
    print("settings isolates the fault. That is the most actionable finding you can")
    print("hand an engineer, and it is invisible from the network alone.")
    print("\nCaveat: 'dataLayer events' is derived from conditions on the {{Event}}")
    print("variable only, over the set of event names referenced anywhere in this")
    print("container. Other conditions may further restrict firing, and an event the")
    print("site pushes but never references in a trigger will not appear. Read")
    print("FIRES ON before concluding a tag fires on a given push.")
    print("\nOn properties: two tags sending one event to two DIFFERENT measurement")
    print("IDs is ordinary dual-tagging, not double-counting - they are grouped")
    print("separately above. Where a property shows as an env lookup that could not")
    print("be resolved to a single ID, confirm by hand before drawing conclusions.")

    print_checks(r)


def print_cross_container(results):
    """
    Checks that only make sense across containers.

    Multi-stack sites are where defects concentrate, and the specific failures are
    predictable: each stack has its own event vocabulary, coverage is uneven, and
    the properties they feed diverge.
    """
    print("\n--- CROSS-CONTAINER CHECKS ---")

    # Which container covers which ecommerce events. An event missing everywhere is
    # a genuine gap; missing in one container is usually just the stack boundary.
    per = {}
    for r in results:
        have = {t["event_name"] for t in r["ga4_event_tags"] if isinstance(t["event_name"], str)}
        have |= {v for m in r["js_event_mappings"] for _, v in m["mappings"]}
        have |= {v for m in r["js_event_mappings"] for _, v in m["conditional_mappings"]}
        per[r["container_id"]] = have & set(ECOMMERCE_EVENTS)

    everywhere_missing = [e for e in ECOMMERCE_EVENTS
                          if not any(e in v for v in per.values())]
    print("\nEcommerce event coverage by container:")
    for cid, have in per.items():
        ordered = [e for e in ECOMMERCE_EVENTS if e in have]
        print(f"  {cid}: {', '.join(ordered) if ordered else '(none)'}")
    if everywhere_missing:
        print(f"\n  NOT PRESENT IN ANY CONTAINER: {', '.join(everywhere_missing)}")
        print("  These are genuine gaps rather than a stack-boundary artefact.")

    # Event vocabulary mismatch: one container listening for namespaced names while
    # another pushes GA4-native ones is the classic silent two-stack failure.
    conv = {}
    for r in results:
        n = r["checks"]["event_naming"]
        conv[r["container_id"]] = (bool(n["native_ecommerce_events"]),
                                  bool(n["namespaced_events"]))
    natives = [c for c, (nat, _leg) in conv.items() if nat]
    legacies = [c for c, (_nat, leg) in conv.items() if leg]
    if natives and legacies and set(natives) != set(legacies):
        print("\nEvent-vocabulary mismatch between stacks:")
        print(f"  GA4-native ecommerce names: {', '.join(natives)}")
        print(f"  namespaced/legacy names:    {', '.join(legacies)}")
        print("  If a stack pushes one convention into a container listening for the")
        print("  other, nothing matches and nothing errors. Verify the event names")
        print("  each stack actually pushes against what each container listens for.")

    # Property divergence across containers.
    prop_map = {}
    for r in results:
        for pid in r["measurement_ids"]:
            prop_map.setdefault(pid, []).append(r["container_id"])
    if len(prop_map) > 1:
        print("\nProperties fed, by container:")
        for pid, cids in sorted(prop_map.items()):
            print(f"  {pid}: {', '.join(cids)}")
        partial = [p for p, c in prop_map.items() if len(c) < len(results)]
        if partial:
            print("  Properties fed by only SOME containers will have partial coverage")
            print(f"  by construction: {', '.join(partial)}")
            print("  Confirm which property the export uses before analysing it.")


def main():
    ap = argparse.ArgumentParser(
        description="Audit public GTM containers: GA4 event tags, triggers, double-fire risks.")
    ap.add_argument("container_ids", nargs="+", help="e.g. GTM-XXXXXXX")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--all-tags", action="store_true", help="include non-GA4 tags")
    ap.add_argument("--raw-dir", help="save fetched containers for manual inspection")
    ap.add_argument("--observed-id", action="append", dest="observed_ids", default=[],
                    metavar="G-XXXXXXX",
                    help="measurement ID(s) actually seen on the wire during the browser "
                         "audit (repeatable). Used to disambiguate environment lookups "
                         "the container alone cannot resolve.")
    args = ap.parse_args()

    bad = [x for x in args.observed_ids if not x.startswith("G-")]
    if bad:
        ap.error(f"--observed-id values must look like G-XXXXXXX: {bad}")

    results, failed = [], False
    for cid in (c.strip() for c in args.container_ids):
        if not re.match(r"^GTM-[A-Z0-9]+$", cid, re.I):
            print(f"Skipping {cid!r}: not a GTM container ID (expected GTM-XXXXXXX)",
                  file=sys.stderr)
            failed = True
            continue
        data, err = fetch_container(cid, args.raw_dir)
        if err:
            print(f"ERROR: {err}", file=sys.stderr)
            failed = True
            continue
        results.append(audit(cid, data, include_all=args.all_tags,
                             observed_ids=args.observed_ids))

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            print_report(r)
        if len(results) > 1:
            print(f"\n{'=' * 78}\nCROSS-CONTAINER NOTE\n{'=' * 78}")
            print("Attribute each finding to a stack. 'The site has no view_cart' is")
            print("wrong if one container has it; 'view_cart exists in the storefront")
            print("container but the cart page runs on the legacy stack, which has no")
            print("equivalent' is correct and actionable. Stack boundaries are where")
            print("defects cluster - separate dataLayer schemas, identity populated on")
            print("one side only, and drifting field conventions.")
            print_cross_container(results)

    return 1 if failed and not results else 0


if __name__ == "__main__":
    sys.exit(main())
