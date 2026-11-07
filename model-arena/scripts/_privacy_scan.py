# -*- coding: utf-8 -*-
"""_privacy_scan.py - temporary scan of the NEW T3 rows for personal data.

Checks the four appended datasets for:
  email / CN mobile / 18-digit id / real absolute path / platform account id
The absolute-path rule only flags a drive letter or POSIX root that is NOT part of a
well-known example path (the D9 tasks legitimately use /etc/passwd and /var/www/html
as test fixtures, which is exactly what those questions are about).

Usage: python scripts/_privacy_scan.py
"""
import json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, "datasets", "full")
PREFIX = {"D4": "d4m_", "D7": "d7m_", "D8": "d8m_", "D9": "d9m_"}

# documented example paths / RFC 2606 reserved domains are allowed by design
ALLOW = ["/etc/passwd", "/var/www/html", "example.test", "example.local",
         "example.com", "registry.local", "127.0.0.1", "10.0.0.2", "10.1.2.3",
         "api.internal", "company.example.com"]

PATTERNS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone_cn", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    # a drive letter followed by a real path segment; the negative lookahead skips
    # escape sequences such as "e:\n" that come from JSON-encoded source code
    ("win_abs", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?![nrtbfv\"\\\\])[A-Za-z0-9_.\-]")),
    ("posix_abs", re.compile(r"(?<![\w.])/(?:home|Users|root|opt)/[A-Za-z0-9_.\-]")),
    ("platform_id", re.compile(r"\b(?:UID|QQ|WeChat|wxid|telegram)[_ :]*[A-Za-z0-9]{6,}", re.I)),
]


def main():
    grand = 0
    for dim, pre in PREFIX.items():
        rows = [json.loads(l) for l in open(os.path.join(FULL, "%s_full.jsonl" % dim),
                                            encoding="utf-8") if l.strip()]
        new = [r for r in rows if r["id"].startswith(pre)]
        blob = chr(10).join(json.dumps(r, ensure_ascii=False) for r in new)
        for a in ALLOW:
            blob = blob.replace(a, "")
        print("%s: %d new rows, %d chars scanned" % (dim, len(new), len(blob)))
        for name, pat in PATTERNS:
            hits = sorted(set(pat.findall(blob)))
            grand += len(hits)
            print("   %-12s %d  %s" % (name, len(hits), hits[:5] if hits else ""))
    print()
    print("TOTAL PERSONAL-DATA HITS IN NEW ROWS: %d" % grand)
    return 0 if grand == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
