"""Receipts: check that every quoted sentence really is on the page it cites, and build a link that
opens that page scrolled to the sentence (a URL text fragment, `#:~:text=start,end`).

claims.json (written by the agent's researchers):
[
  {"id": "C1", "sub": "Q1", "claim": "Electric car sales passed 17 million in 2024.",
   "evidence": [{"source": "S03", "quote": "exact sentence copied from raw/S03.md"}]}
]

`verify` adds to each piece of evidence: match = "exact" | "close" | "missing", the matched page text,
and `link`; and to each claim: receipts (matched evidence), independent (distinct sites), status:
"verified" (2+ independent sites), "single source" (1), "no receipt" (0). A checker may later set
status to "conflicting" and add a note; verify never overwrites that.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .quality import norm_text, registrable, domain_of

WORD = re.compile(r"\S+")


MARKS = set("*_`#>|")


@lru_cache(maxsize=64)
def _index(raw: str) -> Tuple[str, List[int]]:
    """Normalized text plus, for every normalized char, its position in the raw text (approximate but monotonic)."""
    out_chars: List[str] = []
    pos: List[int] = []
    prev_space = True
    for i, ch in enumerate(raw):
        if ch.isspace() or ch in MARKS:
            n = " "
        elif ord(ch) < 128:
            n = ch.lower()
        else:
            n = norm_text(ch) or " "
        if n == " ":
            if prev_space:
                continue
            prev_space = True
        else:
            prev_space = False
        for c in n:
            out_chars.append(c)
            pos.append(i)
    return "".join(out_chars), pos


def find_quote(quote: str, raw: str) -> Tuple[str, str]:
    """Returns (match, page_text): exact normalized match, a close match (>= 0.88 similar), or missing."""
    q = norm_text(quote)
    if len(q) < 12:
        return "missing", ""
    text, pos = _index(raw)
    i = text.find(q)
    if i >= 0:
        return "exact", raw[pos[i]:pos[i + len(q) - 1] + 1]
    # close: anchor on the rarest-looking long word, compare windows around it
    words = sorted(set(w for w in q.split() if len(w) >= 6), key=lambda w: (text.count(w), -len(w)))
    best, best_span = 0.0, None
    for w in words[:4]:
        start = 0
        hits = 0
        while hits < 40:
            j = text.find(w, start)
            if j < 0:
                break
            hits += 1
            off = q.find(w)
            lo = max(0, j - off - 20)
            hi = min(len(text), lo + len(q) + 40)
            sm = SequenceMatcher(None, q, text[lo:hi], autojunk=False)
            blocks = sm.get_matching_blocks()
            ratio = sm.ratio() * (len(q) + (hi - lo)) / (2 * len(q))  # share of the quote that is matched
            if ratio > best:
                b0 = next((b for b in blocks if b.size > 0), None)
                b1 = [b for b in blocks if b.size > 0][-1] if b0 else None
                best = ratio
                best_span = (lo + b0.b, lo + b1.b + b1.size) if b0 and b1 else None
            start = j + 1
    if best >= 0.88 and best_span:
        a, b = best_span
        return "close", raw[pos[a]:pos[max(a, b - 1)] + 1]
    return "missing", ""


def text_fragment(url: str, page_text: str) -> str:
    """https://site/page#:~:text=first%20words,last%20words  (opens the page scrolled to the sentence)."""
    words = WORD.findall(" ".join(page_text.split()))
    if not words or not url:
        return url
    enc = lambda s: urllib.parse.quote(s, safe="").replace("-", "%2D").replace(",", "%2C").replace("&", "%26")
    if len(words) <= 9:
        frag = enc(" ".join(words))
    else:
        frag = enc(" ".join(words[:5])) + "," + enc(" ".join(words[-5:]))
    base = url.split("#", 1)[0]
    return base + "#:~:text=" + frag


def verify(run: Path) -> Dict[str, Any]:
    run = Path(run)
    claims = json.loads((run / "claims.json").read_text(encoding="utf-8"))
    sources = {s["id"]: s for s in json.loads((run / "sources.json").read_text(encoding="utf-8"))["sources"]}
    raw_cache: Dict[str, Optional[str]] = {}

    def raw_of(sid: str) -> Optional[str]:
        if sid not in raw_cache:
            p = run / "raw" / (sid + ".md")
            raw_cache[sid] = p.read_text(encoding="utf-8") if p.is_file() else None
        return raw_cache[sid]

    totals = {"claims": 0, "evidence": 0, "exact": 0, "close": 0, "missing": 0, "verified": 0, "single source": 0, "no receipt": 0}
    for c in claims:
        totals["claims"] += 1
        sites = set()
        n_ok = 0
        for ev in c.get("evidence", []):
            totals["evidence"] += 1
            src = sources.get(ev.get("source", ""))
            raw = raw_of(ev.get("source", ""))
            if not src:
                ev.update(match="missing", note="unknown source id")
            elif raw is None:
                # not fetched: the quote can only come from the search summary/snippet
                blob = " ".join([src.get("summary", ""), src.get("snippet", "")])
                m, page = find_quote(ev.get("quote", ""), blob)
                ev.update(match=m, page_text=page, from_summary=True)
            else:
                body = raw.split("\n---\n", 1)[-1]
                m, page = find_quote(ev.get("quote", ""), body)
                ev.update(match=m, page_text=page)
            totals[ev["match"]] += 1
            if ev["match"] != "missing" and src:
                n_ok += 1
                sites.add(registrable(domain_of(src["url"])))
                ev["link"] = text_fragment(src["url"], ev.get("page_text") or ev.get("quote", ""))
                ev["url"] = src["url"]
        c["receipts"] = n_ok
        c["independent"] = len(sites)
        if c.get("status") != "conflicting":
            c["status"] = "verified" if len(sites) >= 2 else "single source" if n_ok else "no receipt"
        if c["status"] in totals:
            totals[c["status"]] += 1
    (run / "claims.json").write_text(json.dumps(claims, ensure_ascii=False, indent=1), encoding="utf-8")
    return totals
