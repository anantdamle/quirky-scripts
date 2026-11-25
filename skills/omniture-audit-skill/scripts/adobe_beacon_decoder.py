#!/usr/bin/env python3
"""
adobe_beacon_decoder.py - Decode Adobe Analytics, Web SDK, Target and Audience
Manager requests into readable, audit-ready rows.

Network capture gives you URLs and (sometimes) bodies full of shortcodes:
v12, c7, pe=lnk_o, mid, D=, products strings. This turns them into named
variables and flags the things an audit cares about.

Usage:
    python3 adobe_beacon_decoder.py beacons.txt            # one URL per line
    python3 adobe_beacon_decoder.py har_export.har         # HAR file
    python3 adobe_beacon_decoder.py beacons.txt --json
    python3 adobe_beacon_decoder.py - < beacons.txt        # stdin

Input lines may be bare URLs, or "URL<TAB>BODY" for POST requests. HAR files
are read including POST bodies, which is the only reliable way to see Web SDK
(alloy) payloads - they are always POSTed JSON.

Standard library only - no dependencies.

Limits:
1. AppMeasurement switches to POST when the URL would exceed ~2047 chars. The
   variables then live in the body; without it the hit looks empty. The
   decoder says so rather than reporting variables as unset.
2. "D=" values are dynamic copies (e.g. v5=D=pageName). They are resolved
   against the same hit where possible, otherwise reported unresolved.
"""

import argparse
import hashlib
import json
import re
import sys
from urllib.parse import parse_qsl, urlsplit

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s\-()]{8,}\d)(?!\d)")
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

AA_NAMED = {
    "pageName": "pageName", "gn": "pageName", "g": "page URL", "r": "referrer",
    "ch": "channel", "server": "server", "events": "events",
    "products": "products", "purchaseID": "purchaseID", "pi": "purchaseID",
    "xact": "transactionID", "cc": "currencyCode", "state": "state",
    "zip": "zip", "c3": "prop3", "v0": "campaign (v0)", "pe": "link type",
    "pev1": "link URL", "pev2": "link name", "pev3": "video/milestone",
    "mid": "ECID (mid)", "aid": "legacy visitor ID (aid / s_vi)",
    "vid": "custom visitorID", "fid": "fallback visitor ID (s_fid)",
    "mcorgid": "Experience Cloud Org ID", "aamb": "AAM blob",
    "aamlh": "AAM location hint", "mcaid": "MCAID", "cdp": "cookie domain periods",
    "ce": "character set", "ns": "namespace", "t": "timestamp", "ts": "timestamp",
    "bh": "browser height", "bw": "browser width", "s": "screen", "c": "color",
    "j": "JS version", "v": "Java enabled", "k": "cookies", "AQB": "",
    "AQE": "", "ndh": "", "pf": "", "sdid": "supplemental data ID (A4T)",
    "lrt": "link response time", "cl": "cookie lifetime",
}
LINK_TYPES = {"lnk_o": "custom link (s.tl 'o')", "lnk_d": "download link",
              "lnk_e": "exit link"}

ADOBE_HOST_HINTS = ("/b/ss/", "adobedc.net", "omtrdc.net", "2o7.net",
                    "demdex.net", "tt.omtrdc.net", "everesttech.net")


# ------------------------------------------------------------------ input

def read_requests(path):
    """Yield (url, body) tuples from a HAR file or a URL-per-line text file."""
    text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8",
                                                      errors="replace").read()
    stripped = text.lstrip()
    if stripped.startswith("{") and '"log"' in stripped[:2000]:
        har = json.loads(text)
        for entry in har.get("log", {}).get("entries", []):
            req = entry.get("request", {})
            body = (req.get("postData") or {}).get("text", "")
            yield req.get("url", ""), body
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, _, body = line.partition("\t")
        yield url, body


def classify(url):
    u = url.lower()
    if "/b/ss/" in u:
        return "appmeasurement"
    if "/ee/" in u and ("interact" in u or "collect" in u) or "edge.adobedc.net" in u:
        return "websdk"
    if "tt.omtrdc.net" in u or "/rest/v1/delivery" in u or "/m2/" in u and "mbox" in u:
        return "target"
    if "demdex.net" in u:
        return "aam"
    return None


# ------------------------------------------------------- AppMeasurement

def parse_context_data(pairs):
    """Context data is nested as c.=, a.=, x=1, .a=, .c= markers."""
    out, stack = {}, []
    in_ctx = False
    for k, v in pairs:
        if k == "c." and not in_ctx:
            in_ctx = True
            continue
        if not in_ctx:
            continue
        if k == ".c":
            in_ctx = False
            continue
        if k.endswith(".") and not k.startswith("."):
            stack.append(k[:-1])
            continue
        if k.startswith(".") and not k.endswith("."):
            if stack:
                stack.pop()
            continue
        out[".".join(stack + [k])] = v
    return out


def parse_products(s):
    rows = []
    for item in s.split(","):
        f = item.split(";")
        f += [""] * (6 - len(f))
        rows.append({"category": f[0], "product": f[1], "units": f[2],
                     "revenue": f[3], "events": f[4], "merch_evars": f[5]})
    return rows


def decode_appmeasurement(url, body):
    parts = urlsplit(url)
    path = parts.path.split("/")
    rsids, lib = "", ""
    if "ss" in path:
        i = path.index("ss")
        rsids = path[i + 1] if len(path) > i + 1 else ""
        lib = path[i + 3] if len(path) > i + 3 else ""
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if body:
        pairs += parse_qsl(body, keep_blank_values=True)
    raw = dict(pairs)
    hit = {"type": "AppMeasurement", "host": parts.hostname,
           "report_suites": rsids.split(","), "library": lib,
           "first_party_collection": not any(h in (parts.hostname or "")
                                             for h in ("omtrdc.net", "2o7.net")),
           "post_body_missing": len(pairs) <= 3 and not body,
           "vars": {}, "evars": {}, "props": {}, "list_vars": {},
           "context_data": parse_context_data(pairs), "flags": []}
    for k, v in pairs:
        if re.fullmatch(r"v\d+", k) and k != "v0":
            hit["evars"]["eVar" + k[1:]] = v
        elif re.fullmatch(r"c\d+", k):
            hit["props"]["prop" + k[1:]] = v
        elif re.fullmatch(r"l\d", k):
            hit["list_vars"]["list" + k[1:]] = v
        elif k in AA_NAMED and AA_NAMED[k]:
            hit["vars"][AA_NAMED[k]] = v
    # Resolve dynamic variables (D=)
    for bucket in ("evars", "props"):
        for name, v in hit[bucket].items():
            if v.startswith("D="):
                src = v[2:]
                resolved = raw.get(src) or raw.get({"pageName": "gn"}.get(src, src))
                hit[bucket][name] = f"{v} -> {resolved!r}" if resolved else f"{v} (unresolved)"
    pe = raw.get("pe")
    hit["call"] = LINK_TYPES.get(pe, "page view (s.t)") if pe else "page view (s.t)"
    if "products" in raw:
        hit["products_parsed"] = parse_products(raw["products"])
    flags = hit["flags"]
    if hit["post_body_missing"]:
        flags.append("No variables in URL - likely POST; body not captured")
    if "mid" not in raw and "aid" not in raw and "vid" not in raw:
        flags.append("No visitor ID (mid/aid/vid) on hit")
    if "aid" in raw and "mid" in raw:
        flags.append("Both legacy aid and ECID mid present (grace-period/migration)")
    if not pe and not raw.get("pageName") and not raw.get("gn"):
        flags.append("Page view without pageName - reports fall back to URL")
    if len(hit["report_suites"]) > 1:
        flags.append(f"Multi-suite tagging to {len(hit['report_suites'])} suites (secondary server calls are billed)")
    if raw.get("events") and "purchase" in raw["events"] and not (raw.get("purchaseID") or raw.get("pi")):
        flags.append("purchase without purchaseID - no de-duplication on reload")
    if raw.get("events") and "purchase" in raw["events"] and "products" not in raw:
        flags.append("purchase without products string - revenue cannot be recorded")
    return hit


# -------------------------------------------------------------- Web SDK

def walk(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def decode_websdk(url, body):
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query))
    hit = {"type": "Web SDK", "host": parts.hostname,
           "first_party_collection": "adobedc.net" not in (parts.hostname or ""),
           "datastream_id": q.get("configId") or q.get("dataStreamId"),
           "request_id": q.get("requestId"), "events": [], "identity_map": {},
           "consent": None, "flags": []}
    if not body:
        hit["flags"].append("Body not captured - Web SDK payloads are POST JSON; export a HAR")
        return hit
    try:
        payload = json.loads(body)
    except ValueError:
        hit["flags"].append("Body is not JSON")
        return hit
    meta = payload.get("meta", {})
    hit["consent"] = meta.get("consent") or payload.get("consent")
    hit["state_entries"] = [e.get("key") for e in meta.get("state", {}).get("entries", [])]
    hit["config_overrides"] = bool(meta.get("configOverrides"))
    for ev in payload.get("events", []):
        xdm = ev.get("xdm", {})
        idm = xdm.get("identityMap", {})
        for ns, ids in idm.items():
            hit["identity_map"].setdefault(ns, []).extend(
                {"id": i.get("id"), "authenticatedState": i.get("authenticatedState"),
                 "primary": i.get("primary")} for i in ids)
        aa = (ev.get("data") or {}).get("__adobe", {}).get("analytics", {})
        hit["events"].append({
            "eventType": xdm.get("eventType"),
            "web.webPageDetails.name": xdm.get("web", {}).get("webPageDetails", {}).get("name"),
            "commerce": list(xdm.get("commerce", {}).keys()),
            "productListItems": len(xdm.get("productListItems", [])),
            "xdm_fields": sorted(p for p, _ in walk(xdm) if not p.startswith("identityMap")),
            "data.__adobe.analytics": aa,
            "personalization.decisionScopes": (ev.get("query") or {}).get("personalization", {}).get("decisionScopes"),
        })
    flags = hit["flags"]
    for ev in hit["events"]:
        if not ev["eventType"]:
            flags.append("Event without xdm.eventType")
        if ev["eventType"] == "web.webpagedetails.pageViews" and not ev["web.webPageDetails.name"]:
            flags.append("Page view without web.webPageDetails.name")
        if ev["data.__adobe.analytics"]:
            flags.append("Uses data.__adobe.analytics (AA vars bypass XDM - not in AEP datasets)")
    if not hit["identity_map"]:
        flags.append("No identityMap on request (ECID comes from cookie/state only)")
    return hit


# ------------------------------------------------------- Target / AAM

def decode_target(url, body):
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    hit = {"type": "Target", "host": parts.hostname, "flags": [],
           "mbox": q.get("mbox"), "mbox3rdPartyId": q.get("mbox3rdPartyId"),
           "mboxMCGVID": q.get("mboxMCGVID"), "sdid": q.get("mboxMCSDID")}
    if body:
        try:
            p = json.loads(body)
            hit["mboxes"] = [m.get("name") for m in (p.get("execute") or {}).get("mboxes", [])]
            hit["customerIds"] = (p.get("id") or {}).get("customerIds")
            hit["thirdPartyId"] = (p.get("id") or {}).get("thirdPartyId")
            hit["sdid"] = hit["sdid"] or (p.get("experienceCloud") or {}).get(
                "analytics", {}).get("supplementalDataId")
        except ValueError:
            pass
    if not hit["sdid"]:
        hit["flags"].append("No supplemental data ID - A4T stitching to Analytics not evidenced")
    return hit


def decode_aam(url, body):
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True)
    d = dict(q)
    return {"type": "Audience Manager / demdex", "host": parts.hostname,
            "path": parts.path, "d_mid": d.get("d_mid"), "d_orgid": d.get("d_orgid"),
            "d_cid": [v for k, v in q if k in ("d_cid", "d_cid_ic")],
            "d_dpuuid": d.get("dpuuid") or d.get("d_dpuuid"),
            "signals": {k: v for k, v in q if k.startswith("c_")},
            "flags": []}


# ------------------------------------------------------------ PII scan

def pii_scan(hit):
    """Flag plaintext email/phone anywhere in decoded values. Values redacted."""
    found = []
    for path, val in walk(hit):
        if not isinstance(val, str) or path.endswith("flags"):
            continue
        if EMAIL_RE.search(val):
            found.append(f"plaintext EMAIL in {path} ({len(val)} chars)")
        elif PHONE_RE.fullmatch(val.strip()) and not HEX_RE.fullmatch(val.strip()):
            found.append(f"possible PHONE in {path}")
        elif HEX_RE.fullmatch(val) and len(val) in (32, 40, 64):
            algo = {32: "MD5", 40: "SHA-1", 64: "SHA-256"}[len(val)]
            found.append(f"{algo}-shaped hash in {path}: {val}")
    return found


def redact(hit):
    def _r(o):
        if isinstance(o, dict):
            return {k: _r(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_r(v) for v in o]
        if isinstance(o, str) and EMAIL_RE.search(o):
            return EMAIL_RE.sub(lambda m: f"<EMAIL sha256:{hashlib.sha256(m.group().strip().lower().encode()).hexdigest()[:12]}..>", o)
        return o
    return _r(hit)


DECODERS = {"appmeasurement": decode_appmeasurement, "websdk": decode_websdk,
            "target": decode_target, "aam": decode_aam}


def print_hit(n, hit):
    print(f"\n### {n}. {hit['type']} @ {hit.get('host')}")
    skip = {"type", "host", "flags", "pii"}
    for k, v in hit.items():
        if k in skip or v in (None, {}, [], ""):
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
            if len(v) > 600:
                v = v[:600] + " ...(truncated, use --json)"
        print(f"- **{k}**: {v}")
    for f in hit["flags"]:
        print(f"- FLAG: {f}")
    for p in hit.get("pii", []):
        print(f"- PII: {p}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", help="HAR file, text file of URLs (URL<TAB>BODY), or - for stdin")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--include-unknown", action="store_true",
                    help="list Adobe-looking hosts the decoder did not classify")
    args = ap.parse_args()

    hits, unknown = [], set()
    for url, body in read_requests(args.input):
        kind = classify(url)
        if not kind:
            if args.include_unknown and any(h in url for h in ADOBE_HOST_HINTS):
                unknown.add(urlsplit(url).hostname)
            continue
        hit = DECODERS[kind](url, body)
        hit["pii"] = pii_scan(hit)
        hits.append(redact(hit))

    summary = {
        "total": len(hits),
        "by_type": {t: sum(1 for h in hits if h["type"] == t) for t in {h["type"] for h in hits}},
        "report_suites": sorted({r for h in hits for r in h.get("report_suites", []) if r}),
        "datastreams": sorted({h["datastream_id"] for h in hits if h.get("datastream_id")}),
        "collection_hosts": sorted({h["host"] for h in hits if h.get("host")}),
        "both_appmeasurement_and_websdk": any(h["type"] == "AppMeasurement" for h in hits)
        and any(h["type"] == "Web SDK" for h in hits),
    }
    if args.json:
        print(json.dumps({"summary": summary, "hits": hits, "unclassified_hosts": sorted(unknown)},
                         indent=2, ensure_ascii=False))
        return
    print("# Adobe beacon decode\n")
    for k, v in summary.items():
        print(f"- **{k}**: {v}")
    if summary["both_appmeasurement_and_websdk"]:
        print("- FLAG: AppMeasurement AND Web SDK both firing - check for double counting "
              "(same report suite fed twice?)")
    for i, h in enumerate(hits, 1):
        print_hit(i, h)
    if unknown:
        print("\n## Unclassified Adobe-looking hosts\n" + "\n".join(f"- {u}" for u in sorted(unknown)))


if __name__ == "__main__":
    main()
