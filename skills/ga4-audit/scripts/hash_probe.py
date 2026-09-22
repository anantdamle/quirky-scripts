#!/usr/bin/env python3
"""
hash_probe.py - Identify which normalisation of a known value produces an observed hash.

Run this LOCALLY rather than hashing in the browser or pasting the email into a
chat transcript: the plaintext stays on your machine and only the matching recipe
name is printed.

Usage:
    python3 hash_probe.py --hash <hex> --email "user@example.com"
    python3 hash_probe.py --hash <a> --hash <b> --email "u@e.com" --extra-value 003XX0000
    python3 hash_probe.py --list-recipes

Standard library only - no dependencies.

Why it matters: the industry-standard join key is SHA-256 of the lowercased,
trimmed email. Anything else fails to join a CRM using that convention, and the
failure is silent - ad platforms report low match rates with no error surfaced.
"""

import argparse
import hashlib
import sys

ALGOS = {
    "sha256": lambda b: hashlib.sha256(b).hexdigest(),
    "sha1": lambda b: hashlib.sha1(b).hexdigest(),
    "md5": lambda b: hashlib.md5(b).hexdigest(),
    "sha512_trunc64": lambda b: hashlib.sha512(b).hexdigest()[:64],
}

# Hex length -> plausible algorithms. Useful even without a match: a 32-char hash
# where you expected SHA-256 is itself a finding (inconsistent algorithms across
# fields is a defect in its own right).
LENGTH_HINTS = {32: "MD5", 40: "SHA-1", 56: "SHA-224", 64: "SHA-256", 128: "SHA-512"}


def email_variants(email):
    """Normalisations seen in real implementations, in rough order of likelihood."""
    e = email
    lower, stripped = e.lower(), e.strip()
    ls = e.strip().lower()
    out = [
        ("lowercase+trim (industry standard)", ls),
        ("as-is", e),
        ("lowercase", lower),
        ("trim only", stripped),
        ("uppercase", e.upper()),
        ("lowercase+trim, trailing newline", ls + "\n"),
        ("lowercase+trim, trailing CRLF", ls + "\r\n"),
        ("lowercase+trim, leading space", " " + ls),
        ("lowercase+trim, trailing space", ls + " "),
    ]
    if "@" in ls:
        local, _, domain = ls.partition("@")
        # Gmail-style canonicalisation: dots ignored, +tag stripped.
        out += [
            ("dots stripped from local part", local.replace(".", "") + "@" + domain),
            ("plus-tag stripped", local.split("+")[0] + "@" + domain),
            ("dots + plus-tag stripped",
             local.split("+")[0].replace(".", "") + "@" + domain),
            ("local part only", local),
            ("domain only", domain),
        ]
    return out


def double_hash_variants(email):
    """Some implementations hash twice, or hash the hex digest as a string."""
    ls = email.strip().lower()
    out = []
    for algo, fn in ALGOS.items():
        once = fn(ls.encode())
        out.append((f"double {algo} (raw bytes)", algo, fn(bytes.fromhex(once))))
        out.append((f"{algo} of {algo} hexdigest", algo, fn(once.encode())))
    return out


def probe(target, email=None, extras=None):
    """Return a list of matching descriptions. Empty means no recipe matched."""
    target = target.strip().lower()
    matches = []

    candidates = []
    if email:
        candidates += [(f"email: {d}", v) for d, v in email_variants(email)]
    for x in extras or []:
        candidates += [
            (f"extra {x!r}: as-is", x),
            (f"extra {x!r}: lowercase", x.lower()),
            (f"extra {x!r}: uppercase", x.upper()),
        ]
        # Salesforce 18-char IDs are the 15-char ID plus a checksum suffix;
        # systems disagree about which form they store.
        if len(x) == 18:
            candidates.append((f"extra {x!r}: first 15 chars (SF 15-char ID)", x[:15]))
    # Concatenations, both orders and with common separators.
    if email and extras:
        ls = email.strip().lower()
        for x in extras:
            for sep in ("", ":", "|", "_"):
                candidates.append((f"email + {sep!r} + {x!r}", f"{ls}{sep}{x}"))
                candidates.append((f"{x!r} + {sep!r} + email", f"{x}{sep}{ls}"))

    for desc, value in candidates:
        for algo, fn in ALGOS.items():
            if fn(value.encode("utf-8")) == target:
                matches.append(f"{algo.upper()} of {desc}")

    if email:
        for desc, _algo, digest in double_hash_variants(email):
            if digest == target:
                matches.append(desc)

    return matches


def self_test():
    """
    Verify the hashing implementation before trusting any comparison. A silently
    broken implementation would produce false negatives, which is the worst
    possible failure mode here - it looks like a real finding.
    """
    expected = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    actual = hashlib.sha256(b"test").hexdigest()
    if actual != expected:
        print(f"FATAL: SHA-256 self-test failed.\n  expected {expected}\n  got      {actual}",
              file=sys.stderr)
        return False
    return True


def main():
    ap = argparse.ArgumentParser(
        description="Identify which normalisation of a value produces an observed hash.")
    ap.add_argument("--hash", action="append", dest="hashes", default=[],
                    help="observed hash (repeatable)")
    ap.add_argument("--email", help="the known account email (stays local)")
    ap.add_argument("--extra-value", action="append", dest="extras", default=[],
                    help="other candidate pre-image, e.g. a CRM or contact ID (repeatable)")
    ap.add_argument("--list-recipes", action="store_true",
                    help="show what gets tested, then exit")
    args = ap.parse_args()

    if not self_test():
        return 2

    if args.list_recipes:
        print("Algorithms: " + ", ".join(sorted(ALGOS)))
        print("\nEmail normalisations:")
        for d, _ in email_variants("Example.User+tag@Example.COM"):
            print(f"  - {d}")
        print("\nAlso tested: double-hashing, hashing the hex digest as a string,")
        print("extra values as-is/lower/upper, Salesforce 18->15 char truncation,")
        print("and email+extra concatenations in both orders with separators.")
        return 0

    if not args.hashes:
        ap.error("at least one --hash is required (or use --list-recipes)")
    if not args.email and not args.extras:
        ap.error("provide --email and/or --extra-value to test against")

    unresolved = []
    for h in args.hashes:
        h = h.strip().lower()
        print(f"\n{'=' * 70}\n{h}\n{'=' * 70}")
        hint = LENGTH_HINTS.get(len(h))
        print(f"Length {len(h)}" + (f" - consistent with {hint}" if hint else
                                    " - NOT a standard hex digest length"))

        matches = probe(h, args.email, args.extras)
        if matches:
            print("\nMATCHED:")
            for m in matches:
                print(f"  + {m}")
            # Only warn about CRM-join convention when this actually resolved to an
            # email. A hash of a CRM ID is not meant to be an email hash, so the
            # warning would be misleading.
            email_matches = [m for m in matches if " of email: " in m]
            standard = any(m.startswith("SHA256 of email: lowercase+trim") for m in matches)
            if email_matches and not standard:
                print("\n  Note: this is NOT SHA-256 of the lowercased+trimmed email.")
                print("  It will not join a CRM using the standard convention, and any")
                print("  ad platform receiving it is failing advanced matching silently.")
            elif not email_matches:
                print("\n  Note: this resolved to a non-email value. Confirm what the")
                print("  consuming system expects - if a field is documented as an email")
                print("  hash but carries this, that mismatch is the finding.")
        else:
            unresolved.append(h)
            print("\nNO MATCH against any tested recipe.")
            print("  This is a legitimate audit finding, not a dead end. It implies a")
            print("  salt, a different pre-image, or a stale value on the account -")
            print("  and resolving it requires server-side code access. Report it as")
            print("  unidentified rather than guessing.")
            print("  Also confirm it is a hash of a PERSON at all: device and visitor")
            print("  IDs are often hashed to the same shape. If the value persists")
            print("  while the user is logged OUT, it is a device ID.")

    if len(args.hashes) > 1:
        print(f"\n{'=' * 70}\nMULTIPLE HASHES\n{'=' * 70}")
        print("Different hashes of the same user is a P0, with two consequences:")
        print("  1. Attribution - a downstream join on 'the email hash' splits one")
        print("     customer into several identities depending on which stack the row")
        print("     came from. If the wrong one becomes user_id, the export will not")
        print("     join to CRM at all.")
        print("  2. Media - platforms receiving the wrong hash report low or zero")
        print("     advanced-matching rates with no error. Live, quantifiable waste,")
        print("     independent of any analytics project - escalate on its own track.")
        print("Record each hash WITH its location (which field, which stack).")

    if unresolved:
        print(f"\n{len(unresolved)} of {len(args.hashes)} hash(es) unidentified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
