#!/usr/bin/env python3
"""
tms_library_audit.py - Fetch and audit public Adobe Launch (Tags / DTM) and
Tealium iQ libraries.

The GTM equivalent for the Adobe ecosystem: network capture shows what fired,
the published library shows what was *supposed* to fire, on which conditions,
setting which variables. Both libraries are public by necessity - the browser
has to download them.

Usage:
    # Adobe Launch / Tags - pass the embed URL seen on the page
    python3 tms_library_audit.py launch https://assets.adobedtm.com/<org>/<prop>/launch-EN<hash>.min.js
    # Legacy DTM
    python3 tms_library_audit.py launch https://assets.adobedtm.com/<hash>/satelliteLib-<hash>.js
    # Tealium iQ - account/profile/env, or the utag.js URL
    python3 tms_library_audit.py tealium <account>/<profile>/prod
    python3 tms_library_audit.py tealium https://tags.tiqcdn.com/utag/<acct>/<prof>/prod/utag.js

Options: --json, --raw-dir DIR, --no-fetch-tags (Tealium: skip per-tag files).

Standard library only - no dependencies.

Parsing is structural, not a JS engine: object literals are brace-matched and
read by pattern. Treat anything labelled "heuristic" as a lead to verify in the
Launch UI / Tealium iQ, not as a finding.

Limits worth knowing:
1. Launch custom-code actions whose code is "external" are served as separate
   files (RC<hash>-source.min.js). Their URLs are listed; pass --fetch-custom to
   pull and scan them.
2. Only the PUBLISHED environment you pass is inspected. Dev/staging libraries
   have different URLs and may differ materially.
3. Tealium "server-side" (EventStream / AudienceStream) config is not public.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Vendor signatures for identifying what a rule action or Tealium tag does.
SIGNATURES = [
    # Keep these specific: generic patterns like ".t()" match unrelated vendor code.
    ("Adobe Analytics (AppMeasurement)", r"new AppMeasurement|s_gi\(|/b/ss/"),
    ("Adobe Web SDK (alloy)", r"alloy\(\s*[\"']sendEvent|adobe-alloy|edge\.adobedc\.net"),
    ("Adobe Target (at.js)", r"adobe\.target\.(?:getOffer|applyOffer|trackEvent)|mboxCreate\("),
    ("Adobe Audience Manager", r"DIL\.create|demdex\.net"),
    ("ECID / Visitor API", r"Visitor\.getInstance|setCustomerIDs|AMCV_"),
    ("Google Analytics 4 / gtag", r"gtag\(|googletagmanager\.com/gtag|/g/collect"),
    ("Google Tag Manager", r"googletagmanager\.com/gtm\.js"),
    ("Meta Pixel", r"fbq\(|connect\.facebook\.net"),
    ("TikTok", r"ttq\.|analytics\.tiktok\.com"),
    ("Pinterest", r"pintrk\("),
    ("Microsoft UET", r"bat\.bing\.com|uetq"),
    ("Google Ads / Floodlight", r"googleadservices|fls\.doubleclick|AW-\d+|DC-\d+"),
    ("Hotjar", r"hotjar"), ("Contentsquare", r"contentsquare"),
    ("Quantum Metric", r"quantummetric"), ("FullStory", r"fullstory"),
]
PII_NAME_RE = re.compile(r"e-?mail|phone|mobile|first_?name|last_?name|surname|"
                         r"address|postcode|zip|dob|birth", re.I)
HASHED_NAME_RE = re.compile(r"hash|sha|md5|hashed", re.I)
EVAR_RE = re.compile(r"\b(eVar\d+|prop\d+|list\d|hier\d)\b")
EVENTS_RE = re.compile(r"\b(event\d+|purchase|prodView|scAdd|scRemove|scOpen|scView|scCheckout)\b")


def fetch(url, raw_dir=None, name=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} fetching {url}"
    except Exception as e:  # noqa: BLE001 - report any fetch failure
        return None, f"Failed to fetch {url}: {e}"
    if raw_dir:
        os.makedirs(raw_dir, exist_ok=True)
        fname = name or re.sub(r"[^\w.\-]", "_", url.split("//", 1)[-1])[-120:]
        with open(os.path.join(raw_dir, fname), "w", encoding="utf-8") as f:
            f.write(body)
    return body, None


def match_brackets(text, start):
    """Return index just past the bracket that closes text[start]. String-aware."""
    open_c = text[start]
    close_c = {"{": "}", "[": "]", "(": ")"}[open_c]
    depth, i, n = 0, start, len(text)
    quote = None
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def split_top_level(arr_text):
    """Split the body of a JS array literal into its top-level {...} elements."""
    out, i = [], 0
    while True:
        j = arr_text.find("{", i)
        if j < 0:
            return out
        k = match_brackets(arr_text, j)
        out.append(arr_text[j:k])
        i = k


def find_section(text, key):
    """Locate `key:` or `"key":` followed by an object/array; return its text."""
    m = re.search(r"""["']?%s["']?\s*:\s*([\[{])""" % re.escape(key), text)
    if not m:
        return None
    s = m.start(1)
    return text[s:match_brackets(text, s)]


def str_field(obj, key):
    m = re.search(r"""["']?%s["']?\s*:\s*["']((?:[^"'\\]|\\.)*)["']""" % re.escape(key), obj)
    return m.group(1) if m else None


def vendors_in(text):
    return [name for name, pat in SIGNATURES if re.search(pat, text)]


# ---------------------------------------------------------------- Launch

def audit_launch(url, raw_dir=None, fetch_custom=False):
    body, err = fetch(url, raw_dir)
    if err:
        return {"error": err}
    legacy = "satelliteLib" in url or "_satellite.dtm" in body
    res = {"library_url": url, "legacy_dtm": legacy, "build": {}, "property": {},
           "extensions": [], "data_elements": [], "rules": [], "flags": [],
           "custom_code_files": []}
    cont = find_section(body, "container") or body
    bi = find_section(cont, "buildInfo") or ""
    for k in ("turbineVersion", "turbineBuildDate", "buildDate", "environment"):
        v = str_field(bi, k)
        if v:
            res["build"][k] = v
    prop = find_section(cont, "property") or ""
    res["property"]["name"] = str_field(prop, "name")
    res["company_org_id"] = str_field(find_section(cont, "company") or "", "orgId")

    ext = find_section(cont, "extensions") or ""
    for m in re.finditer(r"""["']?([\w\-]+)["']?\s*:\s*\{\s*["']?displayName["']?\s*:\s*["']([^"']+)["']""", ext):
        start = m.start()
        block = ext[start:match_brackets(ext, ext.index("{", start))]
        # Read settings from the extension's own settings object only; minified
        # module code can defeat brace matching and bleed into the next extension.
        settings = find_section(block, "settings") or ""
        res["extensions"].append({
            "id": m.group(1), "displayName": m.group(2),
            "settings_hint": {k: str_field(settings, k) for k in
                              ("orgId", "edgeConfigId", "clientCode", "trackingServer",
                               "trackingServerSecure", "productionReportSuites",
                               "stagingReportSuites", "developmentReportSuites")
                              if str_field(settings, k)},
            # AA extension: libraryCode.accounts:{production:["rsid" | "%DataElement%"],...}
            "report_suites": {env: re.findall(r"""["']([^"']+)["']""", lst)
                              for env, lst in re.findall(
                                  r"""["']?(production|staging|development)["']?\s*:\s*\[(.*?)\]""",
                                  find_section(settings, "accounts") or "")},
            "vendors_in_code": vendors_in(block)[:5],
        })

    de = find_section(cont, "dataElements") or ""
    for m in re.finditer(r"""["']([^"']+)["']\s*:\s*\{\s*["']?(?:defaultValue|modulePath|storageDuration|forceLowerCase|cleanText)""", de):
        block = de[m.start():match_brackets(de, de.index("{", m.start()))]
        name = m.group(1)
        mp = str_field(block, "modulePath") or ""
        path = str_field(block, "path")
        res["data_elements"].append({
            "name": name, "type": mp.split("/")[-1].replace(".js", "") if mp else None,
            "path": path, "storage": str_field(block, "storageDuration"),
            "pii_name": bool(PII_NAME_RE.search(name + " " + (path or "")))
            and not HASHED_NAME_RE.search(name + " " + (path or "")),
        })

    rules_text = find_section(cont, "rules") or ""
    for r in split_top_level(rules_text[1:-1] if rules_text else ""):
        rule = {
            "id": str_field(r, "id"), "name": str_field(r, "name"),
            "events": sorted(set(re.findall(r"""modulePath["']?\s*:\s*["']([^"']*/events/[^"']+)["']""", r))),
            "conditions": sorted(set(re.findall(r"""modulePath["']?\s*:\s*["']([^"']*/conditions/[^"']+)["']""", r))),
            "actions": re.findall(r"""modulePath["']?\s*:\s*["']([^"']*/actions/[^"']+)["']""", r),
            "negated_conditions": len(re.findall(r"""negate["']?\s*:\s*(?:!0|true)""", r)),
            "evars_props": sorted(set(EVAR_RE.findall(r))),
            "aa_events": sorted(set(EVENTS_RE.findall(r))),
            "data_elements_used": sorted(set(re.findall(r"%([^%\s\"']{1,80})%", r))),
            "vendors_in_code": vendors_in(r),
            "custom_code_sources": re.findall(r"""["']?source["']?\s*:\s*["'](https?://[^"']+|//[^"']+)["']""", r),
        }
        acts = " ".join(rule["actions"])
        # Module paths are camelCase in Tags (sendBeacon.js), kebab-case in some older builds.
        has = lambda pat: bool(re.search(pat, acts, re.I))  # noqa: E731
        rule["sends_aa_beacon"] = has(r"send-?beacon") or bool(re.search(r"\bs\.t\(\)|\bs\.tl\(", r))
        rule["aa_call_type"] = ("s.tl" if re.search(r"""type["']?\s*:\s*["']link["']|\.tl\(""", r)
                                else "s.t" if rule["sends_aa_beacon"] else None)
        rule["sends_websdk_event"] = has(r"adobe-alloy.*send-?event")
        rule["websdk_event_type"] = (re.search(r"""["']?type["']?\s*:\s*["']([\w.]+\.[\w.]+)["']""", r) or [None, None])[1]
        rule["clears_vars"] = has(r"clear-?variables")
        rule["custom_code"] = has(r"custom-?code")
        res["rules"].append(rule)
        res["custom_code_files"].extend(rule["custom_code_sources"])

    if fetch_custom:
        res["custom_code_scan"] = []
        for src in res["custom_code_files"]:
            full = "https:" + src if src.startswith("//") else src
            code, e = fetch(full, raw_dir)
            res["custom_code_scan"].append({"url": full, "error": e,
                                            "vendors": vendors_in(code or ""),
                                            "evars_props": sorted(set(EVAR_RE.findall(code or "")))})
    launch_flags(res)
    return res


def launch_flags(res):
    f = res["flags"]
    rules = res["rules"]
    if res["legacy_dtm"]:
        f.append("Legacy DTM library (sunset by Adobe) - migrate to Tags")
    beacon_rules = [r for r in rules if r["sends_aa_beacon"]]
    pv = [r for r in beacon_rules if r["aa_call_type"] == "s.t"]
    if len(pv) > 1:
        f.append(f"{len(pv)} rules send an s.t() page-view beacon - verify their events/conditions are "
                 "mutually exclusive or page views will double count: "
                 + ", ".join(r["name"] or r["id"] for r in pv[:10]))
    no_clear = [r for r in beacon_rules if r["aa_call_type"] == "s.tl" and not r["clears_vars"]]
    if no_clear and not any(r["clears_vars"] for r in rules):
        f.append("No rule uses 'Clear Variables' - eVars/events set for one beacon can leak onto the next "
                 "(classic SPA / s.tl inflation)")
    websdk = [r for r in rules if r["sends_websdk_event"]]
    if beacon_rules and websdk:
        f.append(f"Both AppMeasurement ({len(beacon_rules)} rules) and Web SDK ({len(websdk)} rules) send - "
                 "check whether both feed the same report suite")
    exts = {e["displayName"] for e in res["extensions"]}
    if "Experience Cloud ID Service" not in exts and beacon_rules and not websdk:
        f.append("AppMeasurement without ECID extension - visitor IDs fall back to legacy s_vi/s_fid "
                 "(heuristic: Visitor API may be loaded outside Launch)")
    pii = [d["name"] for d in res["data_elements"] if d["pii_name"]]
    if pii:
        f.append("Data elements whose names suggest plaintext PII (heuristic): " + ", ".join(pii[:15]))
    cc = [r for r in rules if r["custom_code"]]
    if cc:
        f.append(f"{len(cc)} rules run custom code - vendor tags inside them bypass extension governance")
    third = sorted({v for r in rules for v in r["vendors_in_code"] if not v.startswith("Adobe")
                    and not v.startswith("ECID")})
    if third:
        f.append("Third-party vendors loaded via Launch rules: " + ", ".join(third))
    dup_names = {r["name"] for r in rules if r["name"] and sum(1 for x in rules if x["name"] == r["name"]) > 1}
    if dup_names:
        f.append("Duplicate rule names: " + ", ".join(sorted(dup_names)[:10]))


# --------------------------------------------------------------- Tealium

def tealium_url(arg):
    if arg.startswith("http"):
        return arg
    parts = arg.strip("/").split("/")
    if len(parts) == 2:
        parts.append("prod")
    return "https://tags.tiqcdn.com/utag/{}/{}/{}/utag.js".format(*parts)


def audit_tealium(arg, raw_dir=None, fetch_tags=True):
    url = tealium_url(arg)
    body, err = fetch(url, raw_dir, "utag.js")
    if err:
        return {"error": err}
    base = url.rsplit("/", 1)[0]
    res = {"utag_url": url, "flags": [], "tags": [], "load_rules": {}, "extensions": [],
           "version": None, "data_layer_vars": []}
    m = re.search(r"""utag\.cfg\s*=\s*\{.*?v\s*:\s*["']([^"']+)["']""", body, re.S) or \
        re.search(r"""ut\d\.\d+\.\d+\.\d+""", body)
    res["version"] = m.group(1) if m and m.groups() else (m.group(0) if m else None)
    res["publish_timestamp"] = (re.search(r"""utag\.cfg\.v\s*=\s*["']([^"']+)""", body) or
                                re.search(r"""\bv\s*:\s*["'](ut[\d.]+\.\d{12})["']""", body) or [None, None])[1]

    # Load rules are numbered conditions: case'1015':try{c[1015]|=(<expr>)}catch
    lr_m = re.search(r"utag\.loader\.loadrules\s*=\s*function", body)
    if lr_m:
        s = body.index("{", lr_m.end())
        lr = body[s:match_brackets(body, s)]
        for c in re.finditer(r"""c\[(\d+)\]\s*\|=\s*(.*?)\}\s*catch""", lr, re.S):
            res["load_rules"][c.group(1)] = re.sub(r"\s+", " ", c.group(2))[:300]

    # Tag config: "uid":{load:<0|1|4|expr>,send:<1|expr>,tcat:..,v:..,wait:..,tid:..}
    # load/send expressions reference utag.cond[N], i.e. the load rules above.
    cfg_m = re.search(r"utag\.loader\.cfg\s*=\s*\{", body)
    if cfg_m:
        s = cfg_m.end() - 1
        cfg = body[s:match_brackets(body, s)]
        for t in re.finditer(r"""["'](\d+)["']\s*:\s*\{""", cfg):
            blk = cfg[t.end() - 1:match_brackets(cfg, t.end() - 1)]

            def field(k, blk=blk):
                m = re.search(r"[{,]\s*%s\s*:\s*(\(.*?\)|[^,}]+)(?=\s*,\s*\w+\s*:|\s*\}$)" % k, blk)
                return m.group(1).strip() if m else None
            load, send = field("load"), field("send")
            conds = sorted(set(re.findall(r"utag\.cond\[(\d+)\]", f"{load} {send}")), key=int)
            res["tags"].append({
                "uid": t.group(1), "template_id": field("tid"), "version": field("v"),
                "wait": field("wait"), "load": load, "send": send, "load_rule_ids": conds,
                "all_pages": not conds and load not in ("0", None),
                "disabled": load == "0",
                "load_rule_text": " AND ".join(res["load_rules"].get(c, f"cond[{c}]")[:120] for c in conds),
            })

    for m2 in re.finditer(r"""function\s*\(\s*a\s*,\s*b\s*\)\s*\{(.*?)\}\s*(?:,|\])""", body, re.S):
        code = m2.group(1)
        if len(code) < 20:
            continue
        res["extensions"].append({"vendors": vendors_in(code), "b_keys":
                                  sorted(set(re.findall(r"""b\[['"]([^'"]+)['"]\]""", code)))[:25]})
    res["extensions"] = res["extensions"][:60]

    res["data_layer_vars"] = sorted(set(re.findall(r"""b\[['"]([\w.:\-]+)['"]\]""", body)))[:200]

    if fetch_tags:
        for t in res["tags"]:
            code, e = fetch(f"{base}/utag.{t['uid']}.js", raw_dir, f"utag.{t['uid']}.js")
            if e:
                t["fetch_error"] = e
                continue
            t["vendors"] = vendors_in(code)
            t["evars_props"] = sorted(set(EVAR_RE.findall(code)))[:40]
            t["mapped_vars"] = sorted(set(re.findall(r"""['"]([\w.:\-]+)['"]\s*:\s*['"](eVar\d+|prop\d+|events?|products)['"]""", code)))[:40]
            rsid = re.search(r"""(?:account|s_account)\s*[:=]\s*["']([\w,\-]+)["']""", code)
            t["report_suites"] = rsid.group(1) if rsid else None
    tealium_flags(res)
    return res


def tealium_flags(res):
    f = res["flags"]
    tags = res["tags"]
    aa = [t for t in tags if any(v.startswith("Adobe Analytics") for v in t.get("vendors", []))]
    if len(aa) > 1:
        f.append(f"{len(aa)} Tealium tags contain AppMeasurement (uids {', '.join(t['uid'] for t in aa)}) - "
                 "check load rules are exclusive or page views double count")
    if aa and any(any(v.startswith("Adobe Web SDK") for v in t.get("vendors", [])) for t in tags):
        f.append("Both AppMeasurement and Web SDK tags present in Tealium")
    always = [t["uid"] for t in tags if t["all_pages"]]
    if always:
        f.append(f"{len(always)} tags have no load rule (load on all pages): " + ", ".join(always[:20]))
    pii = [v for v in res["data_layer_vars"] if PII_NAME_RE.search(v) and not HASHED_NAME_RE.search(v)]
    if pii:
        f.append("utag_data / extension vars whose names suggest plaintext PII (heuristic): " + ", ".join(pii[:15]))
    third = sorted({v for t in tags for v in t.get("vendors", []) if not v.startswith("Adobe")
                    and not v.startswith("ECID")})
    if third:
        f.append("Non-Adobe vendors in Tealium tags: " + ", ".join(third))


# ------------------------------------------------------------------ output

def print_md(kind, res):
    if "error" in res:
        print(f"ERROR: {res['error']}")
        return
    print(f"# {kind} library audit\n")
    for k, v in res.items():
        if k in ("rules", "tags", "data_elements", "extensions", "flags", "load_rules"):
            continue
        if v:
            print(f"- **{k}**: {v if not isinstance(v, (list, dict)) else json.dumps(v)}")
    if res.get("flags"):
        print("\n## Flags\n" + "\n".join(f"- {x}" for x in res["flags"]))
    if kind == "Launch":
        print("\n## Extensions\n")
        for e in res["extensions"]:
            print(f"- {e['displayName']} (`{e['id']}`) {json.dumps(e['settings_hint']) if e['settings_hint'] else ''}")
        print("\n## Rules\n\n| Rule | Events | AA call | Web SDK | eVars/props | AA events | Vendors |\n|---|---|---|---|---|---|---|")
        for r in res["rules"]:
            ev = ", ".join(e.split("/")[-1].replace(".js", "") for e in r["events"])
            print(f"| {r['name'] or r['id']} | {ev} | {r['aa_call_type'] or ''} | "
                  f"{r['websdk_event_type'] or ('yes' if r['sends_websdk_event'] else '')} | "
                  f"{' '.join(r['evars_props'][:12])} | {' '.join(r['aa_events'][:8])} | "
                  f"{', '.join(r['vendors_in_code'])} |")
        print(f"\n## Data elements ({len(res['data_elements'])})\n")
        for d in res["data_elements"]:
            print(f"- {d['name']} - {d['type']}{' path=' + d['path'] if d['path'] else ''}"
                  f"{' [PII-NAME]' if d['pii_name'] else ''}")
    else:
        print("\n## Tags\n\n| uid | template | load rule | vendors | report suite | eVars/props |\n|---|---|---|---|---|---|")
        for t in res["tags"]:
            rule = ("DISABLED" if t["disabled"] else "all pages" if t["all_pages"]
                    else t["load_rule_text"][:120].replace("|", "\\|"))
            print(f"| {t['uid']} | {t['template_id']} | {rule} | "
                  f"{', '.join(t.get('vendors', [])) or t.get('fetch_error', '')} | {t.get('report_suites') or ''} | "
                  f"{' '.join(t.get('evars_props', [])[:12])} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("kind", choices=["launch", "tealium"])
    ap.add_argument("targets", nargs="+", help="Launch embed URL(s) or Tealium account/profile[/env] / utag.js URL")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--raw-dir", help="save fetched libraries for manual inspection")
    ap.add_argument("--fetch-custom", action="store_true", help="Launch: fetch external custom-code files")
    ap.add_argument("--no-fetch-tags", action="store_true", help="Tealium: skip fetching utag.<uid>.js")
    args = ap.parse_args()
    out = []
    for t in args.targets:
        res = (audit_launch(t, args.raw_dir, args.fetch_custom) if args.kind == "launch"
               else audit_tealium(t, args.raw_dir, not args.no_fetch_tags))
        out.append(res)
        if not args.json:
            print_md("Launch" if args.kind == "launch" else "Tealium", res)
            print()
    if args.json:
        print(json.dumps(out if len(out) > 1 else out[0], indent=2))
    sys.exit(1 if all("error" in r for r in out) else 0)


if __name__ == "__main__":
    main()
