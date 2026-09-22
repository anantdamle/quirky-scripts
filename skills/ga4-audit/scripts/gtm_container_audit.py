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


def _preferred_id(val, macros, depth=0, seen=None):
    """
    Follow the branch a real user on production would take.

    Containers routinely nest lookups - hostname -> bot flag -> ID - so this
    recurses through map values rather than trusting vtp_defaultValue, which is
    usually the UAT or bot fallback. Silently taking the default is how you end up
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
    # Otherwise recurse into the branches - the live marker is often one level down.
    found = {r for r in (_preferred_id(v, macros, depth + 1, seen) for _k, v in entries) if r}
    if len(found) == 1:
        return next(iter(found))
    # Unambiguous single-valued branch (excluding the fallback default).
    branch_ids = set()
    for _k, v in entries:
        branch_ids |= _reachable_ids(v, macros, depth + 1, seen)
    return next(iter(branch_ids)) if len(branch_ids) == 1 else None


def resolve_measurement_id(val, macros):
    """
    Resolve a measurement-ID reference to an actual G-XXXXXXX.

    This matters more than it looks. Two tags emitting the same event on the same
    trigger only double-count if they target the SAME property; sending one event
    to two different properties is ordinary dual-tagging. Grouping without
    resolving the ID produces confident false positives.

    Returns (id_or_None, description). Ambiguity is reported rather than guessed -
    cross-reference against the measurement IDs actually observed on the wire.
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


# ------------------------------------------------------------------- the audit

def audit(container_id, data, include_all=False):
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
        mid, mid_desc = resolve_measurement_id(tag.get("vtp_measurementIdOverride"), macros)
        if mid:
            measurement_ids[mid] = measurement_ids.get(mid, 0) + 1

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
                    (dl, emitted, mid or "?unresolved"), []).append((i, uncond))

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
        uncond = [t for t, u in entries if u]
        record = {"datalayer_event": dl, "ga4_event": ga4, "measurement_id": mid,
                  "tags": [t for t, _ in entries], "unconditional_tags": uncond}
        if len(uncond) >= 2:
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
    return result


# -------------------------------------------------------------------- reporting

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

    if r["double_fire_confirmed"]:
        print("\n### DOUBLE-FIRE - CONFIRMED. Investigate first.")
        print("Two or more tags emit the same GA4 event, to the SAME property, from the")
        print("same dataLayer push, each via a trigger conditioned only on the event")
        print("name - so nothing separates them and both will fire. For `purchase` this")
        print("doubles transaction counts AND revenue. Rank P0: unlike a missing event,")
        print("which reads as an obvious zero, inflated data looks plausible and goes")
        print("unnoticed. Cheapest confirmation: compare GA4 transactions against the")
        print("order system - roughly 2x settles it without a test purchase.")
        for d in r["double_fire_confirmed"]:
            print(f"  ! {d['datalayer_event']!r} -> GA4 {d['ga4_event']!r} "
                  f"-> {d['measurement_id']}  tags {d['unconditional_tags']}")

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


def main():
    ap = argparse.ArgumentParser(
        description="Audit public GTM containers: GA4 event tags, triggers, double-fire risks.")
    ap.add_argument("container_ids", nargs="+", help="e.g. GTM-XXXXXXX")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--all-tags", action="store_true", help="include non-GA4 tags")
    ap.add_argument("--raw-dir", help="save fetched containers for manual inspection")
    args = ap.parse_args()

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
        results.append(audit(cid, data, include_all=args.all_tags))

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

    return 1 if failed and not results else 0


if __name__ == "__main__":
    sys.exit(main())
