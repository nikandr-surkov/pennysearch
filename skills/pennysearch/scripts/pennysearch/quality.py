"""Source quality: canonical URLs, source types, language, and a transparent trust score with flags.

Raw search results mix official pages with spam, press releases dressed up as news, listicles and
results in the wrong language. Every source gets plain flags (shown in the report) and a 0-100 score
built from the provider's authority and relevance numbers plus the rules below. Nothing is hidden:
a low score lowers the rank, it never removes a source.
"""
from __future__ import annotations

import re
import unicodedata
import urllib.parse
from datetime import date, datetime
from typing import Any, Dict, List, Optional

TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ref_src$|igshid$|spm$|share$|si$|aff|affiliate|clickid$|yclid$|_hs)", re.I)


def canonical_url(url: str) -> str:
    """Same page, same key: lower-case host without www, no tracking params, no fragment, no trailing slash."""
    p = urllib.parse.urlsplit((url or "").strip())
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if p.port and p.port not in (80, 443):
        host += ":%d" % p.port
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)])
    path = re.sub(r"/+$", "", p.path) or "/"
    scheme = "https" if p.scheme in ("http", "https") else p.scheme
    return urllib.parse.urlunsplit((scheme, host, path, query, ""))


def clean_url(url: str) -> str:
    """The URL to open or fetch: tracking params and the fragment removed, host and scheme kept as they are."""
    p = urllib.parse.urlsplit((url or "").strip())
    query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)])
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, query, ""))


def domain_of(url: str) -> str:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def registrable(host: str) -> str:
    """example.co.uk -> example.co.uk, news.bbc.co.uk -> bbc.co.uk, a.b.example.com -> example.com (good enough for 'independent sources')."""
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "gov", "ac", "edu", "ne", "or"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


# --- source types -----------------------------------------------------------------------------

PRIMARY_SUFFIXES = (".gov", ".mil", ".int", ".edu")
PRIMARY_PATTERNS = re.compile(r"(^|\.)(gov|gouv|gob|go|govt|admin|ac|edu)\.[a-z]{2}$")
PRIMARY_DOMAINS = {
    "europa.eu", "who.int", "oecd.org", "imf.org", "worldbank.org", "un.org", "iea.org", "irena.org", "wto.org",
    "bis.org", "ecb.europa.eu", "federalreserve.gov", "sec.gov", "arxiv.org", "nature.com", "science.org",
    "thelancet.com", "nejm.org", "bmj.com", "pnas.org", "ieee.org", "acm.org", "springer.com", "sciencedirect.com",
    "wiley.com", "cell.com", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "w3.org", "ietf.org", "iso.org",
    "python.org", "nodejs.org", "developer.mozilla.org", "statista.com", "ourworldindata.org", "census.gov",
}
DOCS_HINT = re.compile(r"^(docs|developer|developers|api-docs|learn|support|help|investor|investors|ir)\.")
NEWS_DOMAINS = {
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com", "nytimes.com", "washingtonpost.com",
    "bbc.com", "bbc.co.uk", "theguardian.com", "economist.com", "cnbc.com", "cnn.com", "npr.org", "axios.com",
    "techcrunch.com", "theverge.com", "arstechnica.com", "wired.com", "theinformation.com", "semafor.com",
    "nikkei.com", "asia.nikkei.com", "scmp.com", "caixinglobal.com", "technode.com", "restofworld.org",
    "politico.com", "politico.eu", "aljazeera.com", "dw.com", "lemonde.fr", "spiegel.de", "venturebeat.com",
    "zdnet.com", "engadget.com", "businessinsider.com", "fortune.com", "forbes.com", "time.com", "vox.com",
}
UGC_DOMAINS = {
    "reddit.com", "quora.com", "medium.com", "x.com", "twitter.com", "youtube.com", "tiktok.com", "facebook.com",
    "instagram.com", "linkedin.com", "zhihu.com", "tieba.baidu.com", "weibo.com", "bilibili.com", "stackexchange.com",
    "stackoverflow.com", "news.ycombinator.com", "substack.com", "blogspot.com", "wordpress.com", "tumblr.com",
    "pinterest.com", "threads.net", "t.me", "dev.to", "hashnode.dev",
}
PRESS_DOMAINS = {"prnewswire.com", "businesswire.com", "globenewswire.com", "einpresswire.com", "accesswire.com",
                 "newswire.com", "prweb.com", "openpr.com", "issuewire.com", "pr.com", "marketersmedia.com"}
WIKI_DOMAINS = {"wikipedia.org", "wikimedia.org", "baike.baidu.com", "fandom.com"}

HOSTED_LABELS = {"elearning", "moodle", "lms", "blog", "blogs", "sites", "wiki", "forum", "forums", "events", "students",
                 "student", "community", "people", "staff", "my", "web", "pages", "courses", "library"}
CLICKBAIT = re.compile(r"what (most )?people get wrong|the truth about|you won'?t believe|here'?s why|everything you need to know|"
                       r"shocking|secret(s)? (they|nobody)|will blow your mind|(nobody|no one) is talking about", re.I)
LISTICLE = re.compile(r"\b(top|best)\s+\d{1,3}\b|\b\d{1,3}\s+best\b|\bbest\b.*\b(20\d\d)\b|\bultimate guide\b|\bvs\.?\b.*\bwhich\b", re.I)
PRESS_PATH = re.compile(r"/(press-?releases?|news-?releases?|pr)/", re.I)
SPONSORED = re.compile(r"\b(sponsored|advertorial|partner content|paid post|promoted)\b", re.I)


def source_type(url: str) -> str:
    host = domain_of(url)
    reg = registrable(host)
    if host.split(".")[0] in HOSTED_LABELS and host != reg:
        return "hosted page"   # a blog, course or forum on an official domain: not the institution speaking
    if reg in PRESS_DOMAINS or PRESS_PATH.search(urllib.parse.urlsplit(url).path or ""):
        return "press release"
    if DOCS_HINT.match(host):
        return "official docs"
    if host.endswith(PRIMARY_SUFFIXES) or PRIMARY_PATTERNS.search(host) or host in PRIMARY_DOMAINS or reg in PRIMARY_DOMAINS:
        return "primary"
    if reg in WIKI_DOMAINS or host.endswith("wikipedia.org"):
        return "encyclopedia"
    if host in NEWS_DOMAINS or reg in NEWS_DOMAINS:
        return "news"
    if host in UGC_DOMAINS or reg in UGC_DOMAINS:
        return "forum / social"
    if reg == "github.com" or host.endswith("github.io"):
        return "code"
    return "web"


# --- language -------------------------------------------------------------------------------

SCRIPTS = [
    ("zh", re.compile(r"[一-鿿㐀-䶿]")),
    ("ja", re.compile(r"[぀-ヿ]")),
    ("ko", re.compile(r"[가-힯]")),
    ("ru", re.compile(r"[Ѐ-ӿ]")),
    ("ar", re.compile(r"[؀-ۿ]")),
    ("he", re.compile(r"[֐-׿]")),
    ("hi", re.compile(r"[ऀ-ॿ]")),
    ("th", re.compile(r"[฀-๿]")),
    ("el", re.compile(r"[Ͱ-Ͽ]")),
]


def guess_lang(text: str) -> str:
    """Script-based guess: good enough to tell English results from Chinese or Russian ones."""
    t = text or ""
    letters = sum(1 for ch in t if ch.isalpha())
    if not letters:
        return "unknown"
    best, best_n = "", 0
    for code, rx in SCRIPTS:
        n = len(rx.findall(t))
        if n > best_n:
            best, best_n = code, n
    if best == "zh" and SCRIPTS[1][1].search(t):
        best = "ja"
    if best_n / letters >= 0.25:
        return best
    return "latin"


# --- dates ----------------------------------------------------------------------------------

def parse_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    m = re.search(r"(20\d\d|19\d\d)[-/.年](\d{1,2})(?:[-/.月](\d{1,2}))?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
        try:
            return date(y, max(1, min(12, mo)), max(1, min(28, d)))
        except ValueError:
            return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%d %B %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 6].strip(), fmt).date()
        except ValueError:
            continue
    m = re.search(r"\b(20\d\d)\b", s)
    return date(int(m.group(1)), 6, 30) if m else None


# --- score ----------------------------------------------------------------------------------

TYPE_POINTS = {"primary": 22, "official docs": 18, "news": 10, "encyclopedia": 4, "code": 6, "web": 0, "hosted page": -6,
               "forum / social": -10, "press release": -14}


def assess(src: Dict[str, Any], want_lang: Optional[str] = None, today: Optional[date] = None, stale_years: float = 3.0) -> Dict[str, Any]:
    """Adds `type`, `lang`, `flags`, `score` (0-100) to a search result dict and returns it."""
    today = today or date.today()
    url = src.get("url", "")
    text = " ".join([src.get("title", ""), src.get("snippet", ""), src.get("summary", "")[:300]])
    stype = source_type(url)
    lang = guess_lang(text)
    flags: List[str] = []
    auth = float(src.get("authority", -1))
    rer = float(src.get("rerank", -1))
    if stype == "press release":
        flags.append("press release")
    if stype == "forum / social":
        flags.append("forum / social")
    if LISTICLE.search(src.get("title", "")):
        flags.append("listicle")
    if CLICKBAIT.search(src.get("title", "")):
        flags.append("clickbait?")
    if stype == "hosted page":
        flags.append("hosted page")
    if SPONSORED.search(text) or re.search(r"[?&](aff|affiliate|ref)=", url, re.I):
        flags.append("sponsored?")
    if 0 <= auth <= 1:
        flags.append("low authority")
    wl = (want_lang or "").split(",")[0].strip().lower()
    if wl == "en" and lang not in ("latin", "unknown"):
        flags.append("not English")
    d = parse_date(src.get("date", ""))
    if d and (today - d).days > stale_years * 365:
        flags.append("old (%d)" % d.year)

    score = 30.0
    score += (auth / 4.0) * 30 if auth >= 0 else 10
    score += (rer / 3.0) * 20 if rer >= 0 else 6
    score += TYPE_POINTS.get(stype, 0)
    score -= 8 * sum(1 for f in flags if f in ("listicle", "sponsored?", "clickbait?"))
    score -= 10 if "not English" in flags else 0
    score -= 6 if any(f.startswith("old") for f in flags) else 0
    if d and (today - d).days <= 365:
        score += 4
    src.update({"type": stype, "lang": lang, "flags": flags, "score": int(max(0, min(100, round(score)))),
                "domain": domain_of(url), "published": d.isoformat() if d else ""})
    return src


def pick_for_reading(sources: List[Dict[str, Any]], n: int, per_site: int = 2, skip_flags=("press release",)) -> List[Dict[str, Any]]:
    """Best-scored sources with variety: at most `per_site` per site, and flagged types only if nothing better is left."""
    ranked = sorted(sources, key=lambda s: (-s.get("score", 0), s.get("id", "")))
    out: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for pass_ in (0, 1):
        for s in ranked:
            if len(out) >= n:
                return out
            if s in out:
                continue
            reg = registrable(s.get("domain", ""))
            if seen.get(reg, 0) >= per_site:
                continue
            if pass_ == 0 and any(f in skip_flags for f in s.get("flags", [])):
                continue
            out.append(s)
            seen[reg] = seen.get(reg, 0) + 1
    return out


def norm_text(t: str) -> str:
    """Normalizes text for quote matching: NFKC, unified quotes and dashes, collapsed spaces, lower case."""
    t = unicodedata.normalize("NFKC", t or "")
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[‐-―−]", "-", t)
    t = re.sub(r"[*_`#>|]+", " ", t)  # markdown marks from fetched pages
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()
