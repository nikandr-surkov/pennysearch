"""Report: one self-contained HTML file (no CDN, no fonts to download, works offline, light and dark).

spec.json (written by the agent's writer, from verified claims only):
{
  "title": "Is the electric car market still growing?",
  "question": "the user's question as asked",
  "answer": "Yes. More than 17 million ... [C1] ... [C2]",        # 2-4 sentences, citations as [C1]
  "takeaways": ["Sales passed 17 million in 2024 [C1]", "..."],
  "numbers": [{"label": "Sold in 2024", "value": "17.3", "unit": "M", "note": "battery + plug-in", "claims": ["C1"]}],
  "charts": [{"type": "bar"|"hbar"|"line", "title": "...", "subtitle": "...", "unit": "M", "labels": [...],
              "values": [...] | "series": [{"name": "...", "values": [...]}], "note": "...", "claims": ["C2"]}],
  "sections": [{"heading": "...", "body": "Paragraphs with **bold** and [C3] citations.\\n\\n- lists work too"}],
  "disagreements": [{"topic": "...", "body": "...", "claims": ["C4", "C5"]}],
  "caveats": ["..."],
  "mode": "deep" | "quick"
}
The run folder's claims.json, sources.json and stats.json are read automatically.
"""
from __future__ import annotations

import html
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__

REPO_URL = "https://github.com/nikandr-surkov/pennysearch"
esc = lambda s: html.escape(str(s if s is not None else ""), quote=True)

LOGO_SVG = ('<svg class="logo" viewBox="0 0 64 64" aria-hidden="true"><defs><radialGradient id="{id}" cx="35%" cy="30%" r="75%">'
        '<stop offset="0" stop-color="#F6C08A"/><stop offset=".55" stop-color="#C8743A"/><stop offset="1" stop-color="#8A4519"/></radialGradient></defs>'
        '<circle cx="32" cy="32" r="30" fill="url(#{id})"/><circle cx="32" cy="32" r="24.5" fill="none" stroke="#FFE3C2" stroke-opacity=".55" stroke-width="1.6"/>'
        '<circle cx="29" cy="29" r="10" fill="none" stroke="#FFF4E6" stroke-width="4.2"/><path d="M36.5 36.5 L45 45" stroke="#FFF4E6" stroke-width="5" stroke-linecap="round"/></svg>')


def logo(uid: str) -> str:
    # a gradient id per copy: a gradient inside a hidden copy would otherwise blank out the others
    return LOGO_SVG.replace("{id}", "cu-" + uid)


# ---------------------------------------------------------------------------
# small text helpers

CITE = re.compile(r"\[((?:C\d+)(?:\s*,\s*C\d+)*)\]")


def inline(text: str, claims: Dict[str, Dict[str, Any]]) -> str:
    """Escaped text with **bold**, *italic*, `code`, [label](https://link) and [C1] citation chips."""
    t = esc(text)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", lambda m: '<a href="%s" rel="noopener">%s</a>' % (m.group(2), m.group(1)), t)

    def chip(m: re.Match) -> str:
        out = []
        for cid in [c.strip() for c in m.group(1).split(",")]:
            c = claims.get(cid)
            if not c:
                continue
            st = c.get("status", "")
            cls = {"verified": "ok", "single source": "one", "conflicting": "bad"}.get(st, "none")
            tip = (c.get("claim", "") + " | " + st)[:180]
            out.append('<a class="cite %s" href="#%s" title="%s">%s</a>' % (cls, esc(cid), esc(tip), esc(cid[1:])))
        return "".join(out)
    return CITE.sub(chip, t)


def blocks(text: str, claims: Dict[str, Dict[str, Any]]) -> str:
    out = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [l for l in para.split("\n") if l.strip()]
        if lines and all(re.match(r"\s*[-*]\s+", l) for l in lines):
            out.append("<ul>" + "".join("<li>%s</li>" % inline(re.sub(r"^\s*[-*]\s+", "", l), claims) for l in lines) + "</ul>")
        elif lines:
            out.append("<p>%s</p>" % inline(" ".join(lines), claims))
    return "\n".join(out)


def money(x: float) -> str:
    return ("$%.4f" % x) if x < 0.1 else ("$%.3f" % x) if x < 1 else ("$%.2f" % x)


def letter_avatar(domain: str) -> str:
    d = (domain or "?").lstrip(".")
    hue = sum(ord(c) for c in d) * 37 % 360
    return '<span class="fav" style="--h:%d" aria-hidden="true">%s</span>' % (hue, esc(d[:1].upper()))


# ---------------------------------------------------------------------------
# charts (inline SVG)

PALETTE = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)"]


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * e >= v:
            return m * e
    return 10 * e


def _fmt(v: float, unit: str = "") -> str:
    if v != int(v) and abs(v) < 10:
        s = ("%.2f" % v).rstrip("0").rstrip(".")
    elif v != int(v) and abs(v) < 100:
        s = ("%.1f" % v).rstrip("0").rstrip(".")
    else:
        s = "{:,}".format(int(round(v)))
    return s + (unit if unit and len(unit) <= 3 else "")


def chart_svg(ch: Dict[str, Any]) -> str:
    kind = ch.get("type", "bar")
    labels = [str(x) for x in ch.get("labels", [])]
    unit = str(ch.get("unit", ""))
    W, H = 640, 300
    if kind == "hbar":
        vals = [float(v) for v in ch.get("values", [])]
        n = len(vals)
        if not n:
            return ""
        rowh = 34
        H = 24 + n * rowh
        mx = _nice_max(max(vals))
        x0 = int(min(300, max(110, 7.4 * max(len(l) for l in labels) + 20))) if labels else 170  # label column fits the longest label
        x1 = W - 70
        rows = []
        for i, (lab, v) in enumerate(zip(labels, vals)):
            y = 14 + i * rowh
            w = max(2, (x1 - x0) * v / mx)
            color = PALETTE[0] if i == ch.get("highlight", vals.index(min(vals)) if ch.get("lower_is_better") else vals.index(max(vals))) else "var(--bar)"
            rows.append('<text x="%d" y="%d" class="lab" text-anchor="end">%s</text>' % (x0 - 10, y + 19, esc(lab[:40])))
            rows.append('<rect x="%d" y="%d" width="%.1f" height="22" rx="5" fill="%s"/>' % (x0, y + 4, w, color))
            rows.append('<text x="%.1f" y="%d" class="val">%s</text>' % (x0 + w + 8, y + 20, esc(_fmt(v, unit))))
        return '<svg viewBox="0 0 %d %d" role="img" aria-label="%s">%s</svg>' % (W, H, esc(ch.get("title", "")), "".join(rows))
    left, right, top, bottom = 48, 16, 18, 34
    pw, ph = W - left - right, H - top - bottom
    if kind == "line":
        series = ch.get("series") or [{"name": ch.get("title", ""), "values": ch.get("values", [])}]
        allv = [float(v) for s in series for v in s.get("values", []) if v is not None]
    else:
        series = [{"name": ch.get("title", ""), "values": ch.get("values", [])}]
        allv = [float(v) for v in ch.get("values", [])]
    if not allv or not labels:
        return ""
    mx = _nice_max(max(allv))
    g = []
    for k in range(5):
        y = top + ph - ph * k / 4
        g.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>' % (left, W - right, y, y))
        g.append('<text x="%d" y="%.1f" class="ax" text-anchor="end">%s</text>' % (left - 8, y + 4, esc(_fmt(mx * k / 4))))
    n = len(labels)
    if kind == "line":
        step = pw / max(1, n - 1)
        for i, lab in enumerate(labels):
            g.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%s</text>' % (left + i * step, H - 10, esc(lab[:10])))
        for si, s in enumerate(series[:5]):
            pts = [(left + i * step, top + ph - ph * float(v) / mx) for i, v in enumerate(s.get("values", [])) if v is not None]
            if not pts:
                continue
            col = PALETTE[si % len(PALETTE)]
            g.append('<polyline fill="none" stroke="%s" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" points="%s"/>'
                     % (col, " ".join("%.1f,%.1f" % p for p in pts)))
            for p in pts:
                g.append('<circle cx="%.1f" cy="%.1f" r="3.6" fill="%s"/>' % (p[0], p[1], col))
            lx, ly = pts[-1]
            g.append('<text x="%.1f" y="%.1f" class="val" fill="%s">%s</text>' % (min(lx + 6, W - 40), ly - 8, col, esc(_fmt(float(s["values"][-1]), unit))))
    else:
        slot = pw / n
        bw = min(56, slot * 0.62)
        vals = [float(v) for v in ch.get("values", [])]
        hi = vals.index(max(vals))
        for i, (lab, v) in enumerate(zip(labels, vals)):
            x = left + i * slot + (slot - bw) / 2
            h = ph * v / mx
            col = PALETTE[0] if i == ch.get("highlight", hi) else "var(--bar)"
            g.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="5" fill="%s"/>' % (x, top + ph - h, bw, h, col))
            g.append('<text x="%.1f" y="%.1f" class="val" text-anchor="middle">%s</text>' % (x + bw / 2, top + ph - h - 7, esc(_fmt(v, unit))))
            g.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%s</text>' % (x + bw / 2, H - 10, esc(lab[:12])))
    legend = ""
    if kind == "line" and len(series) > 1:
        legend = '<div class="legend">' + "".join('<span><i style="background:%s"></i>%s</span>' % (PALETTE[i % 5], esc(s.get("name", ""))) for i, s in enumerate(series[:5])) + "</div>"
    return legend + '<svg viewBox="0 0 %d %d" role="img" aria-label="%s">%s</svg>' % (W, H, esc(ch.get("title", "")), "".join(g))


def chart_card(ch: Dict[str, Any], claims: Dict[str, Dict[str, Any]]) -> str:
    svg = chart_svg(ch)
    if not svg:
        return ""
    cites = inline(" ".join("[%s]" % c for c in ch.get("claims", [])), claims) if ch.get("claims") else ""
    data_rows = ""
    if ch.get("labels"):
        if ch.get("series"):
            heads = "".join("<th>%s</th>" % esc(s.get("name", "")) for s in ch["series"])
            body = "".join("<tr><td>%s</td>%s</tr>" % (esc(l), "".join("<td>%s</td>" % esc(s["values"][i] if i < len(s["values"]) else "") for s in ch["series"]))
                           for i, l in enumerate(ch["labels"]))
        else:
            heads = "<th>%s</th>" % esc(ch.get("unit", "value"))
            body = "".join("<tr><td>%s</td><td>%s</td></tr>" % (esc(l), esc(v)) for l, v in zip(ch["labels"], ch.get("values", [])))
        data_rows = '<details class="data"><summary>Show data</summary><table><tr><th></th>%s</tr>%s</table></details>' % (heads, body)
    return ('<figure class="card chart"><figcaption><b>%s</b><span>%s</span></figcaption>%s<div class="chart-foot"><span>%s %s</span>%s</div></figure>'
            % (esc(ch.get("title", "")), esc(ch.get("subtitle", "")), svg, esc(ch.get("note", "")), cites, data_rows))


# ---------------------------------------------------------------------------
# page

def render(spec: Dict[str, Any], claims_list: List[Dict[str, Any]], sources: List[Dict[str, Any]], stats: Dict[str, Any]) -> str:
    claims = {c["id"]: c for c in claims_list}
    by_src = {s["id"]: s for s in sources}
    shown = [c for c in claims_list if c.get("status") in ("verified", "single source", "conflicting")]
    n_ok = sum(1 for c in shown if c.get("status") == "verified")
    n_one = sum(1 for c in shown if c.get("status") == "single source")
    n_bad = sum(1 for c in shown if c.get("status") == "conflicting")
    n_drop = sum(1 for c in claims_list if c.get("status") not in ("verified", "single source", "conflicting"))
    receipts = sum(1 for c in claims_list for e in c.get("evidence", []) if e.get("match") in ("exact", "close"))
    mode = spec.get("mode", "deep")
    today = spec.get("date") or date.today().isoformat()

    stat_items = [
        (stats.get("queries", 0), "searches"),
        (stats.get("sources", len(sources)), "sources found"),
        (stats.get("read", sum(1 for s in sources if s.get("read"))), "pages read"),
        (receipts, "receipts checked"),
    ]
    statbar = "".join('<div><b>%s</b><span>%s</span></div>' % (esc("{:,}".format(v) if isinstance(v, int) else v), esc(l)) for v, l in stat_items)
    cost = float(stats.get("cost_usd", 0) or 0)
    secs = float(stats.get("seconds", 0) or 0)

    parts: List[str] = []
    parts.append('<header class="top"><a class="brand" href="%s">%s<span>PennySearch</span></a><span class="chip mode">%s</span>'
                 '<span class="meta">%s</span><span class="grow"></span><span class="costpill" title="paid calls x $0.00075">%s &middot; %s</span>'
                 '<button class="theme" type="button" aria-label="Switch light or dark">&#9680;</button></header>'
                 % (REPO_URL, logo("top"), "Deep research" if mode == "deep" else "Quick answer", esc(today), esc(money(cost) + " total"),
                    esc(("%.0f s" % secs) if secs else "")))
    parts.append('<section class="hero"><h1>%s</h1>%s<div class="answer"><div class="kicker">The short answer</div>%s</div>'
                 '<div class="statbar">%s</div></section>'
                 % (esc(spec.get("title", "Research report")),
                    ('<p class="q">%s</p>' % esc(spec["question"])) if spec.get("question") and spec.get("question") != spec.get("title") else "",
                    blocks(spec.get("answer", ""), claims), statbar))

    # receipts meter
    total = max(1, len(claims_list))
    meter = ('<section class="meter card"><div class="meter-head"><b>Every claim has a receipt</b><span>%d claims checked against the pages they cite</span></div>'
             '<div class="bar"><i class="ok" style="width:%.1f%%"></i><i class="one" style="width:%.1f%%"></i><i class="bad" style="width:%.1f%%"></i><i class="none" style="width:%.1f%%"></i></div>'
             '<div class="keys"><span><i class="ok"></i>%d verified by 2+ independent sites</span><span><i class="one"></i>%d single source</span>'
             '<span><i class="bad"></i>%d sources disagree</span><span><i class="none"></i>%d dropped, no receipt</span></div></section>'
             % (len(claims_list), 100 * n_ok / total, 100 * n_one / total, 100 * n_bad / total, 100 * n_drop / total, n_ok, n_one, n_bad, n_drop))
    parts.append(meter)

    if spec.get("takeaways"):
        parts.append('<section><h2>What you need to know</h2><ul class="takeaways">%s</ul></section>'
                     % "".join("<li>%s</li>" % inline(t, claims) for t in spec["takeaways"]))
    if spec.get("numbers"):
        tiles = []
        for nb in spec["numbers"]:
            cites = inline(" ".join("[%s]" % c for c in nb.get("claims", [])), claims)
            tiles.append('<div class="tile"><span class="lbl">%s</span><span class="num">%s<small>%s</small></span><span class="note">%s %s</span></div>'
                         % (esc(nb.get("label", "")), esc(nb.get("value", "")), esc(nb.get("unit", "")), esc(nb.get("note", "")), cites))
        parts.append('<section><h2>In numbers</h2><div class="tiles">%s</div></section>' % "".join(tiles))
    for ch in spec.get("charts", []):
        parts.append(chart_card(ch, claims))
    for sec in spec.get("sections", []):
        parts.append('<section class="prose"><h2>%s</h2>%s</section>' % (esc(sec.get("heading", "")), blocks(sec.get("body", ""), claims)))
    if spec.get("disagreements"):
        cards = "".join('<div class="card dis"><b>%s</b>%s<div>%s</div></div>' % (esc(d.get("topic", "")), blocks(d.get("body", ""), claims),
                        inline(" ".join("[%s]" % c for c in d.get("claims", [])), claims)) for d in spec["disagreements"])
        parts.append('<section><h2>Where sources disagree</h2>%s</section>' % cards)
    if spec.get("caveats"):
        parts.append('<section class="prose"><h2>Keep in mind</h2><ul>%s</ul></section>' % "".join("<li>%s</li>" % inline(c, claims) for c in spec["caveats"]))

    # source mix (automatic)
    parts.append(source_mix(sources))

    # receipts
    cards = []
    for c in shown:
        st = c.get("status", "")
        cls = {"verified": "ok", "single source": "one", "conflicting": "bad"}.get(st, "none")
        evs = []
        for e in c.get("evidence", []):
            if e.get("match") not in ("exact", "close"):
                continue
            s = by_src.get(e.get("source", ""), {})
            evs.append('<blockquote class="ev"><p>%s</p><footer>%s<span class="src"><a href="#%s">%s</a> &middot; %s%s</span>'
                       '<span class="score" title="trust score">%s</span><a class="open" href="%s" target="_blank" rel="noopener">Open at the quote &#8599;</a></footer></blockquote>'
                       % (esc(e.get("page_text") or e.get("quote", "")), letter_avatar(s.get("domain", "")), esc(s.get("id", "")), esc(s.get("domain", "")),
                          esc(s.get("published") or "no date"), " &middot; from summary" if e.get("from_summary") else "",
                          esc(s.get("score", "")), esc(e.get("link") or s.get("url", "#"))))
        note = '<p class="cnote">%s</p>' % esc(c["note"]) if c.get("note") else ""
        cards.append('<article class="receipt %s" id="%s" data-status="%s"><div class="rhead"><span class="badge %s">%s</span><span class="cid">%s</span>'
                     '<span class="sites">%d independent site%s</span></div><h3>%s</h3>%s%s</article>'
                     % (cls, esc(c["id"]), esc(st), cls, esc(st), esc(c["id"]), c.get("independent", 0), "" if c.get("independent", 0) == 1 else "s",
                        esc(c.get("claim", "")), note, "".join(evs)))
    parts.append('<section><div class="sechead"><h2>Receipts</h2><div class="filters" role="group"><button data-f="all" class="on">All</button>'
                 '<button data-f="verified">Verified</button><button data-f="single source">Single source</button><button data-f="conflicting">Conflicting</button></div></div>'
                 '<p class="muted">Each quote below was found on the cited page. "Open at the quote" opens the page scrolled to the exact sentence.</p>%s</section>' % "".join(cards))

    # sources: the ones read and the best of the rest, then what was ranked down and why
    def srow(s: Dict[str, Any], extra: str = "") -> str:
        flags = "".join('<span class="flag">%s</span>' % esc(f) for f in s.get("flags", []))
        return ('<div class="srow%s" id="%s">%s<div class="sbody"><a href="%s" target="_blank" rel="noopener">%s</a><div class="smeta">%s &middot; %s &middot; %s%s</div>%s</div>'
                '<div class="sscore"><div class="sbar"><i style="width:%d%%"></i></div><span>%d</span></div></div>'
                % (extra, esc(s["id"]), letter_avatar(s.get("domain", "")), esc(s.get("url", "#")), esc(s.get("title") or s.get("url", "")),
                   esc(s.get("domain", "")), esc(s.get("type", "")), esc(s.get("published") or "no date"), " &middot; read in full" if s.get("read") else "",
                   ('<div class="flags">%s</div>' % flags) if flags else "", int(s.get("score", 0)), int(s.get("score", 0))))
    top = sorted(sources, key=lambda s: (not s.get("read"), -s.get("score", 0)))[:60]
    shown_ids = {s["id"] for s in top}
    down_flags = {"listicle", "clickbait?", "sponsored?", "hosted page", "forum / social", "low authority", "not English"}
    down = sorted([s for s in sources if s["id"] not in shown_ids and down_flags & set(s.get("flags", []))], key=lambda s: s.get("score", 0))
    down_html = ""
    if down:
        counts: Dict[str, int] = {}
        for s in down:
            for f in s.get("flags", []):
                if f in down_flags:
                    counts[f] = counts.get(f, 0) + 1
        summary = ", ".join("%d %s" % (n, f) for f, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        down_html = ('<h3 class="downhead">Ranked down: %d sources</h3><p class="muted">%s. Still listed, never hidden.</p><div class="sources down">%s</div>'
                     % (len(down), esc(summary), "".join(srow(s, " dim") for s in down[:30])))
    parts.append('<section><h2>Sources</h2><p class="muted">Trust score 0-100 from the provider\'s authority and relevance, the type of site, freshness and the flags shown. '
                 'Flags lower the rank; they never hide a source.</p><div class="sources">%s</div>%s</section>' % ("".join(srow(s) for s in top), down_html))

    # method
    zero = stats.get("queries_without_results") or []
    parts.append('<section class="prose method"><h2>How this was made</h2><p>%d searches ran in parallel (%.1f s), %d unique sources were scored and the best %d were read in full (%.1f s). '
                 '%d paid calls at $0.00075 each: <b>%s</b>. %d calls came from the local cache for free.</p>%s%s</section>'
                 % (stats.get("queries", 0), float(stats.get("search_seconds", 0)), stats.get("sources", 0), stats.get("read", 0), float(stats.get("read_seconds", 0)),
                    stats.get("paid_calls", 0), esc(money(cost)), stats.get("cached_calls", 0),
                    ('<p>Queries with no results (the topic may be filtered): %s</p>' % ", ".join("<code>%s</code>" % esc(z) for z in zero)) if zero else "",
                    '<details><summary>All queries</summary><ul>%s</ul></details>' % "".join("<li><code>%s</code></li>" % esc(q) for q in stats.get("query_list", []))
                    if stats.get("query_list") else ""))
    parts.append('<footer class="foot">%s<span>Made with <a href="%s">PennySearch</a> v%s &middot; deep research for a penny</span></footer>' % (logo("foot"), REPO_URL, __version__))

    return PAGE.replace("{{TITLE}}", esc(spec.get("title", "Research report"))).replace("{{CSS}}", CSS).replace("{{JS}}", JS).replace("{{BODY}}", "\n".join(parts))


def source_mix(sources: List[Dict[str, Any]]) -> str:
    if not sources:
        return ""
    read = [s for s in sources if s.get("read")] or sources
    def counts(key):
        out: Dict[str, int] = {}
        for s in read:
            k = s.get(key) or "unknown"
            out[k] = out.get(k, 0) + 1
        return sorted(out.items(), key=lambda kv: -kv[1])
    def bars(items, title):
        mx = max(v for _, v in items) if items else 1
        rows = "".join('<div class="mrow"><span>%s</span><div><i style="width:%.0f%%"></i></div><b>%d</b></div>' % (esc(k), 100 * v / mx, v) for k, v in items[:6])
        return '<div class="mix"><b>%s</b>%s</div>' % (esc(title), rows)
    langs = [("English / Latin" if k == "latin" else k, v) for k, v in counts("lang")]
    years: Dict[str, int] = {}
    for s in read:
        y = (s.get("published") or "")[:4] or "no date"
        years[y] = years.get(y, 0) + 1
    yitems = sorted(years.items(), key=lambda kv: kv[0], reverse=True)
    return ('<section><h2>Source mix</h2><p class="muted">Of the %d pages read in full.</p><div class="card mixes">%s%s%s</div></section>'
            % (len(read), bars(counts("type"), "Type of site"), bars(langs, "Language"), bars(yitems, "Published")))


def render_files(spec_path: Path, out: Path, run: Optional[Path] = None) -> Path:
    spec_path = Path(spec_path)
    run = Path(run) if run else spec_path.parent
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    load = lambda name, default: json.loads((run / name).read_text(encoding="utf-8")) if (run / name).is_file() else default
    claims = spec.get("claims") or load("claims.json", [])
    src_doc = load("sources.json", {"sources": []})
    stats = load("stats.json", {})
    stats.setdefault("query_list", src_doc.get("queries", []))
    page = render(spec, claims, src_doc.get("sources", []), stats)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title><meta name="generator" content="PennySearch">
<style>{{CSS}}</style></head>
<body><main class="wrap">
{{BODY}}
</main><script>{{JS}}</script></body></html>"""

CSS = r"""
:root{--bg:#FAF7F2;--card:#FFFFFF;--ink:#1C1915;--muted:#6E665C;--line:#E8E0D4;--soft:#F3ECE2;--cu:#B4602A;--cu2:#D9894E;--cusoft:#F6E6D6;
--ok:#1E8A5A;--one:#C08A1E;--bad:#C2410C;--none:#B9B0A4;--mark:#FFE7A3;--bar:#D9CFC2;--c1:#B4602A;--c2:#2F6FA8;--c3:#3F8F5E;--c4:#8B5CB8;--c5:#C2410C;
--shadow:0 1px 2px rgba(40,30,20,.06),0 8px 24px rgba(40,30,20,.06);color-scheme:light}
:root[data-theme=dark]{--bg:#12100E;--card:#1B1815;--ink:#F2ECE4;--muted:#A69D91;--line:#2C2722;--soft:#221E1A;--cu:#E3995F;--cu2:#F0B27E;--cusoft:#3A2A1D;
--ok:#4CC38A;--one:#E2B04A;--bad:#F07A4A;--none:#5E5750;--mark:#5A4A1E;--bar:#3A342E;--c1:#E3995F;--c2:#6AA8E0;--c3:#6CC28E;--c4:#B48BE0;--c5:#F07A4A;
--shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.25);color-scheme:dark}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#12100E;--card:#1B1815;--ink:#F2ECE4;--muted:#A69D91;--line:#2C2722;--soft:#221E1A;--cu:#E3995F;--cu2:#F0B27E;--cusoft:#3A2A1D;
--ok:#4CC38A;--one:#E2B04A;--bad:#F07A4A;--none:#5E5750;--mark:#5A4A1E;--bar:#3A342E;--c1:#E3995F;--c2:#6AA8E0;--c3:#6CC28E;--c4:#B48BE0;--c5:#F07A4A;color-scheme:dark}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto;padding:0 20px 64px}
a{color:var(--cu);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:clamp(32px,5.4vw,52px);line-height:1.06;letter-spacing:-.025em;margin:28px 0 12px;font-weight:780}
h2{font-size:24px;letter-spacing:-.015em;margin:48px 0 14px;font-weight:740}
h3{font-size:18px;line-height:1.4;margin:6px 0 10px;font-weight:650}
code{font:14px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--soft);padding:1px 6px;border-radius:6px}
.muted{color:var(--muted);font-size:15px;margin-top:-6px}
.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;padding:14px 0;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:9px;color:var(--ink);font-weight:760;letter-spacing:-.01em}.brand:hover{text-decoration:none}
.logo{width:28px;height:28px;flex:none}
.chip{font-size:12px;font-weight:650;letter-spacing:.04em;text-transform:uppercase;padding:4px 9px;border-radius:999px;background:var(--cusoft);color:var(--cu)}
.meta{color:var(--muted);font-size:14px}.grow{flex:1}
.costpill{font:600 13px ui-monospace,Menlo,Consolas,monospace;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:4px 10px;white-space:nowrap}
.theme{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;width:34px;height:34px;cursor:pointer;font-size:16px}
.hero .q{color:var(--muted);font-size:19px;margin:0 0 20px}
.answer{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--cu);border-radius:16px;padding:20px 24px;box-shadow:var(--shadow);font-size:20px;line-height:1.55}
.answer p{margin:0 0 10px}.answer p:last-child{margin:0}
.kicker{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--cu);margin-bottom:6px}
.statbar{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0 0}
.statbar div{background:var(--soft);border-radius:12px;padding:12px 14px}.statbar b{display:block;font-size:26px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.statbar span{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}
.meter{margin-top:22px;padding:18px 20px}.meter-head{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;margin-bottom:12px}.meter-head span{color:var(--muted);font-size:14px}
.bar{display:flex;height:12px;border-radius:999px;overflow:hidden;background:var(--soft)}.bar i{display:block;height:100%}
.ok{--k:var(--ok)}.one{--k:var(--one)}.bad{--k:var(--bad)}.none{--k:var(--none)}
.bar i,.keys i{background:var(--k)}
.keys{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:10px;font-size:14px;color:var(--muted)}.keys i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
.cite{display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:20px;padding:0 5px;margin-left:3px;border-radius:6px;font:700 11px ui-monospace,Menlo,Consolas,monospace;
vertical-align:2px;background:color-mix(in srgb,var(--k) 16%,transparent);color:var(--k);border:1px solid color-mix(in srgb,var(--k) 40%,transparent)}.cite:hover{text-decoration:none;background:var(--k);color:var(--card)}
.takeaways{list-style:none;padding:0;margin:0}.takeaways li{position:relative;padding:12px 0 12px 30px;border-bottom:1px solid var(--line)}
.takeaways li:before{content:"";position:absolute;left:4px;top:21px;width:10px;height:10px;border-radius:50%;background:var(--cu);box-shadow:0 0 0 4px var(--cusoft)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;display:flex;flex-direction:column;gap:2px}
.tile .lbl{font-size:13px;color:var(--muted)}.tile .num{font-size:38px;font-weight:780;letter-spacing:-.03em;font-variant-numeric:tabular-nums}.tile small{font-size:16px;margin-left:3px;color:var(--muted)}
.tile .note{font-size:13px;color:var(--muted)}
.chart{margin:22px 0 0;padding:18px 18px 12px}.chart figcaption b{display:block;font-size:17px}.chart figcaption span{color:var(--muted);font-size:14px}
.chart svg{width:100%;height:auto;margin-top:8px;overflow:visible}.chart .grid{stroke:var(--line);stroke-width:1}.chart .ax{fill:var(--muted);font-size:12px}
.chart .val{fill:var(--ink);font-size:12.5px;font-weight:700}.chart .lab{fill:var(--ink);font-size:13px}
.chart-foot{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:10px;margin-top:6px}
.legend{display:flex;gap:16px;font-size:13px;color:var(--muted);margin-top:8px}.legend i{display:inline-block;width:12px;height:3px;border-radius:2px;margin-right:6px;vertical-align:3px}
.data summary{cursor:pointer;color:var(--cu)}.data table{border-collapse:collapse;margin-top:8px;font-size:13px}.data td,.data th{border-bottom:1px solid var(--line);padding:4px 10px;text-align:left}
.prose p{margin:0 0 14px}.prose ul{padding-left:22px}
.dis{padding:16px 18px;margin:0 0 12px;border-left:5px solid var(--bad)}.dis p{margin:6px 0}
.mixes{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;padding:18px}
.mix b{display:block;font-size:14px;margin-bottom:8px}.mrow{display:grid;grid-template-columns:1fr 70px 26px;gap:8px;align-items:center;font-size:13px;color:var(--muted);margin:4px 0}
.mrow div{height:8px;background:var(--soft);border-radius:999px;overflow:hidden}.mrow i{display:block;height:100%;background:var(--cu)}.mrow b{margin:0;text-align:right;color:var(--ink);font-size:13px}
.sechead{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px}.sechead h2{margin-bottom:6px}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--muted);border-radius:999px;padding:6px 12px;font:600 13px inherit;cursor:pointer;margin-left:4px}
.filters button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.receipt{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 18px;margin:12px 0;box-shadow:var(--shadow);position:relative;
background-image:radial-gradient(circle at 0 50%,var(--bg) 7px,transparent 7.5px),radial-gradient(circle at 100% 50%,var(--bg) 7px,transparent 7.5px)}
.receipt:target,.receipt.flash{outline:3px solid var(--cu);outline-offset:3px}
.rhead{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--muted)}.cid{font:700 12px ui-monospace,Menlo,Consolas,monospace}.sites{margin-left:auto}
.badge{font-size:11px;font-weight:750;letter-spacing:.06em;text-transform:uppercase;padding:3px 8px;border-radius:999px;color:var(--k);background:color-mix(in srgb,var(--k) 14%,transparent)}
.cnote{color:var(--muted);font-size:15px;margin:-4px 0 8px}
.ev{margin:10px 0 0;padding:12px 14px;border-radius:12px;background:var(--soft);border:1px dashed color-mix(in srgb,var(--muted) 45%,transparent)}
.ev p{margin:0 0 10px;font-size:15.5px;line-height:1.55}.ev p{background:linear-gradient(transparent 58%,var(--mark) 58%);display:inline;box-decoration-break:clone;-webkit-box-decoration-break:clone}
.ev footer{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:13px;color:var(--muted);margin-top:10px}
.ev .src{flex:1}.ev .score{font:700 12px ui-monospace,Menlo,Consolas,monospace;border:1px solid var(--line);border-radius:6px;padding:1px 6px}
.open{font-weight:650;white-space:nowrap}
.fav{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:7px;flex:none;font:800 12px system-ui;color:#fff;background:hsl(var(--h) 45% 45%)}
.sources{display:flex;flex-direction:column;gap:2px}
.srow{display:grid;grid-template-columns:26px 1fr 120px;gap:12px;align-items:start;padding:12px 0;border-bottom:1px solid var(--line)}
.srow .fav{margin-top:3px}.sbody a{color:var(--ink);font-weight:600}.smeta{font-size:13px;color:var(--muted)}
.flags{margin-top:5px}.flag{display:inline-block;font-size:11px;font-weight:650;color:var(--bad);background:color-mix(in srgb,var(--bad) 10%,transparent);border-radius:6px;padding:1px 7px;margin:0 5px 3px 0}
.sscore{display:flex;align-items:center;gap:8px;font:700 13px ui-monospace,Menlo,Consolas,monospace;padding-top:5px}.sbar{flex:1;height:7px;background:var(--soft);border-radius:999px;overflow:hidden}.sbar i{display:block;height:100%;background:var(--cu)}
.downhead{margin:28px 0 4px}.srow.dim{opacity:.78}.srow.dim .sbody a{font-weight:500}
.method{font-size:15px;color:var(--muted)}.method summary{cursor:pointer;color:var(--cu)}
.foot{display:flex;align-items:center;gap:10px;margin-top:56px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:14px}
@media (max-width:640px){body{font-size:16px}.statbar{grid-template-columns:repeat(2,1fr)}.mixes{grid-template-columns:1fr}.srow{grid-template-columns:24px 1fr}.sscore{grid-column:2}.costpill{display:none}.answer{font-size:18px}}
@media print{.top{position:static}.filters,.theme{display:none}.receipt,.card{box-shadow:none}}
"""

JS = r"""
(function(){
  var root=document.documentElement;
  try{var t=localStorage.getItem('pennysearch-theme');if(t)root.setAttribute('data-theme',t);}catch(e){}
  var btn=document.querySelector('.theme');
  if(btn)btn.addEventListener('click',function(){
    var dark=root.getAttribute('data-theme')==='dark'||(!root.getAttribute('data-theme')&&matchMedia('(prefers-color-scheme: dark)').matches);
    var next=dark?'light':'dark';root.setAttribute('data-theme',next);try{localStorage.setItem('pennysearch-theme',next);}catch(e){}
  });
  document.querySelectorAll('.filters button').forEach(function(b){b.addEventListener('click',function(){
    document.querySelectorAll('.filters button').forEach(function(x){x.classList.toggle('on',x===b);});
    var f=b.getAttribute('data-f');
    document.querySelectorAll('.receipt').forEach(function(r){r.style.display=(f==='all'||r.getAttribute('data-status')===f)?'':'none';});
  });});
  document.addEventListener('click',function(e){
    var a=e.target.closest&&e.target.closest('a.cite');if(!a)return;
    var el=document.getElementById(a.getAttribute('href').slice(1));if(!el)return;
    e.preventDefault();el.style.display='';el.scrollIntoView({behavior:'smooth',block:'center'});
    el.classList.add('flash');setTimeout(function(){el.classList.remove('flash');},1600);
  });
})();
"""
