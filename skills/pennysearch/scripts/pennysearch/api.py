"""Search and Fetch client: keys, rate limits, retries, a local cache and a hard budget.

The API is a pay-per-call web search for agents: `POST {base}/search` and `POST {base}/fetch`,
billed only when a call returns something. Prices and limits are kept in one place below so a
change on the provider side is a one-line fix.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__

API_BASE = os.environ.get("PENNYSEARCH_API_BASE", "https://api.deepseek.com/v1/tools")
KEYS_URL = "https://platform.deepseek.com/api_keys"
TOPUP_URL = "https://platform.deepseek.com/top_up"
DOCS_URL = "https://api-docs.deepseek.com/guides/search"

PRICE_PER_CALL = 0.75 / 1000  # USD per billed Search or Fetch call
# Provider limits (Oct 2026): 100 searches/s and 25 fetches/s per account, plus a per-site limit on
# fetches. We stay well under them by default; both can be raised with flags.
SEARCH_QPS = 40.0
FETCH_QPS = 15.0
SITE_QPS = 1.0
MAX_QUERY_CHARS = 128
MAX_COUNT = 50
TIMEOUT = 30.0
FETCH_TIMEOUT = 10.0   # a slow page should not hold up a whole run; gather swaps it for the next candidate
MAX_RETRIES = 4
FETCH_NET_RETRIES = 0  # a page that timed out once rarely answers the second time

SEARCH_TTL = 24 * 3600      # cached search results are reused for a day
FETCH_TTL = 7 * 24 * 3600   # fetched pages for a week

KEY_ENVS = ("PENNYSEARCH_API_KEY", "DEEPSEEK_API_KEY")


# ---------------------------------------------------------------------------
# Errors (each maps to a CLI exit code)

class PennyError(Exception):
    exit_code = 1


class NoKeyError(PennyError):
    exit_code = 2


class AuthError(PennyError):
    exit_code = 3


class BalanceError(PennyError):
    exit_code = 4


class BudgetError(PennyError):
    exit_code = 5


# ---------------------------------------------------------------------------
# Paths and key storage

def config_dir() -> Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "pennysearch"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pennysearch"


def cache_dir() -> Path:
    if os.environ.get("PENNYSEARCH_CACHE"):
        return Path(os.environ["PENNYSEARCH_CACHE"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "pennysearch" / "cache"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "pennysearch"


def config_path() -> Path:
    return config_dir() / "config.json"


def load_key() -> Tuple[Optional[str], str]:
    """Returns (key, where). Environment variables win over the config file."""
    for name in KEY_ENVS:
        v = os.environ.get(name, "").strip()
        if v:
            return v, "env:" + name
    p = config_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            v = str(data.get("api_key", "")).strip()
            if v:
                return v, str(p)
        except (OSError, ValueError):
            pass
    return None, ""


def save_key(key: str) -> Path:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"api_key": key.strip()}, f)
    try:
        os.chmod(str(p), 0o600)
    except OSError:
        pass
    return p


def mask(text: str, key: Optional[str]) -> str:
    """Never let a key reach a log, an error message or a report."""
    if key and key in text:
        text = text.replace(key, key[:3] + "..." + key[-2:])
    return text


# ---------------------------------------------------------------------------
# Throttling

class Throttle:
    """Spaces calls to at most `qps` per second (thread safe)."""

    def __init__(self, qps: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.gap = 1.0 / qps if qps > 0 else 0.0
        self._next = 0.0
        self._lock = threading.Lock()
        self._clock = clock
        self._sleep = sleep

    def wait(self) -> None:
        if self.gap <= 0:
            return
        with self._lock:
            now = self._clock()
            at = max(now, self._next)
            self._next = at + self.gap
        delay = at - now
        if delay > 0:
            self._sleep(delay)


class SiteThrottle:
    """One Throttle per site, created on first use."""

    def __init__(self, qps: float, clock=time.monotonic, sleep=time.sleep):
        self.qps, self._clock, self._sleep = qps, clock, sleep
        self._by_site: Dict[str, Throttle] = {}
        self._lock = threading.Lock()

    def wait(self, site: str) -> None:
        with self._lock:
            t = self._by_site.get(site)
            if t is None:
                t = self._by_site[site] = Throttle(self.qps, self._clock, self._sleep)
        t.wait()


# ---------------------------------------------------------------------------
# Cache

class Cache:
    def __init__(self, root: Optional[Path] = None, enabled: bool = True, clock=time.time):
        self.root = root or cache_dir()
        self.enabled = enabled
        self._clock = clock

    def _path(self, kind: str, payload: Dict[str, Any]) -> Path:
        blob = json.dumps({"k": kind, "p": payload}, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        return self.root / kind / h[:2] / (h + ".json")

    def get(self, kind: str, payload: Dict[str, Any], ttl: float) -> Optional[Any]:
        if not self.enabled:
            return None
        p = self._path(kind, payload)
        try:
            if self._clock() - p.stat().st_mtime > ttl:
                return None
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, kind: str, payload: Dict[str, Any], value: Any) -> None:
        if not self.enabled:
            return
        p = self._path(kind, payload)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp%d" % threading.get_ident())
            tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp), str(p))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Transport (swappable for tests and the offline demo)

Transport = Callable[[str, bytes, Dict[str, str], float], Tuple[int, Dict[str, str], bytes]]


def http_transport(url: str, body: bytes, headers: Dict[str, str], timeout: float) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        return e.code, dict(e.headers or {}), raw


# ---------------------------------------------------------------------------
# Result shapes

def _unwrap(data: Any) -> Any:
    """Accept both {"results": ...} and {"data": {"results": ...}} style replies."""
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], (dict, list)):
        return data["data"]
    return data


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalize_result(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": str(r.get("title") or "").strip(),
        "url": str(r.get("url") or r.get("link") or "").strip(),
        "snippet": str(r.get("snippet") or "").strip(),
        "summary": str(r.get("summary") or "").strip(),
        "content": str(r.get("content") or ""),
        "site": str(r.get("site_name") or r.get("site") or "").strip(),
        "icon": str(r.get("site_icon") or "").strip(),
        "date": str(r.get("date") or r.get("published") or "").strip(),
        "authority": _num(r.get("authority"), -1.0),   # 0-4 from the provider, -1 if missing
        "rerank": _num(r.get("rerank_score") if r.get("rerank_score") is not None else r.get("rerank"), -1.0),  # 0-3
    }


MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!$,|>~<=&%'\":;/?@^])")


def unescape_markdown(text: str) -> str:
    r"""Fetched pages come as Markdown with punctuation escaped (Solid\-State, US\$1\.3). Quotes must match the
    words people read on the page, so the escapes go."""
    return MD_ESCAPE.sub(r"\1", text or "")


def normalize_page(data: Any, url: str) -> Dict[str, Any]:
    d = _unwrap(data)
    if isinstance(d, list):
        d = d[0] if d and isinstance(d[0], dict) else {}
    if not isinstance(d, dict):
        d = {}
    return {"url": url, "title": unescape_markdown(str(d.get("title") or "")).strip(),
            "content": unescape_markdown(str(d.get("content") or d.get("markdown") or ""))}


# ---------------------------------------------------------------------------
# Client

class Client:
    """Search and Fetch with throttling, retries, caching and a spend cap.

    `budget` is in USD; a call that would go over it raises BudgetError before it is sent.
    Cache hits are free and do not count toward the budget.
    """

    def __init__(self, key: str, base: str = API_BASE, search_qps: float = SEARCH_QPS, fetch_qps: float = FETCH_QPS,
                 site_qps: float = SITE_QPS, timeout: float = TIMEOUT, retries: int = MAX_RETRIES,
                 budget: Optional[float] = None, cache: Optional[Cache] = None, transport: Transport = http_transport,
                 clock=time.monotonic, sleep=time.sleep, rng=random.random):
        self.key = key
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.budget = budget
        self.cache = cache if cache is not None else Cache()
        self.transport = transport
        self._sleep, self._rng = sleep, rng
        self._throttle = {"search": Throttle(search_qps, clock, sleep), "fetch": Throttle(fetch_qps, clock, sleep)}
        self._sites = SiteThrottle(site_qps, clock, sleep)
        self._lock = threading.Lock()
        self.stats = {"search_billed": 0, "fetch_billed": 0, "search_cached": 0, "fetch_cached": 0,
                      "search_empty": 0, "fetch_empty": 0, "retries": 0}
        self._reserved = 0  # paid calls in flight, so parallel calls cannot overshoot the budget

    # -- money

    @property
    def billed_calls(self) -> int:
        return self.stats["search_billed"] + self.stats["fetch_billed"]

    @property
    def cost(self) -> float:
        return self.billed_calls * PRICE_PER_CALL

    def _reserve(self) -> None:
        with self._lock:
            if self.budget is not None and (self.billed_calls + self._reserved + 1) * PRICE_PER_CALL > self.budget + 1e-12:
                raise BudgetError("Budget of $%.4f reached after %d paid calls ($%.4f). Raise it with --budget."
                                  % (self.budget, self.billed_calls, self.cost))
            self._reserved += 1

    def _settle(self, kind: str, billed: bool) -> None:
        with self._lock:
            self._reserved -= 1
            self.stats[kind + ("_billed" if billed else "_empty")] += 1

    # -- transport with retries

    def backoff(self, attempt: int, retry_after: Optional[str] = None) -> float:
        delay = min(30.0, 2.0 ** attempt) * (0.5 + self._rng())
        if retry_after:
            try:
                delay = max(delay, min(60.0, float(retry_after)))
            except ValueError:
                pass
        return delay

    def _post(self, kind: str, payload: Dict[str, Any], site: Optional[str] = None) -> Any:
        url = "%s/%s" % (self.base, kind)
        timeout = min(self.timeout, FETCH_TIMEOUT) if kind == "fetch" else self.timeout
        net_retries = min(self.retries, FETCH_NET_RETRIES) if kind == "fetch" else self.retries
        body = json.dumps(payload).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.key, "Content-Type": "application/json",
                   "Accept": "application/json", "User-Agent": "pennysearch/" + __version__}
        attempt = 0
        while True:
            self._throttle[kind].wait()
            if site:
                self._sites.wait(site)
            try:
                status, rheaders, raw = self.transport(url, body, headers, timeout)
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
                if attempt < net_retries:
                    self.stats["retries"] += 1
                    self._sleep(self.backoff(attempt))
                    attempt += 1
                    continue
                raise PennyError(mask("Network error after %d retries: %s" % (net_retries, getattr(e, "reason", e)), self.key))
            if 200 <= status < 300:
                try:
                    return json.loads(raw.decode("utf-8") or "null")
                except ValueError:
                    raise PennyError("The search API sent a reply that is not JSON.")
            detail = self._detail(raw)
            if status == 429 or status >= 500:
                if attempt < self.retries:
                    self.stats["retries"] += 1
                    ra = {k.lower(): v for k, v in (rheaders or {}).items()}.get("retry-after")
                    self._sleep(self.backoff(attempt, ra))
                    attempt += 1
                    continue
                raise PennyError(mask("HTTP %d after %d retries: %s" % (status, self.retries, detail), self.key))
            if status == 401:
                raise AuthError("The API key was rejected (401). Make a new one at %s and run setup again." % KEYS_URL)
            low = detail.lower()
            if status == 402 or "insufficient" in low or "balance" in low:
                raise BalanceError("Your balance is too low (HTTP %d). Top up at %s" % (status, TOPUP_URL))
            raise PennyError(mask("HTTP %d: %s" % (status, detail), self.key))

    @staticmethod
    def _detail(raw: bytes) -> str:
        text = raw.decode("utf-8", "replace")[:400]
        try:
            data = json.loads(text)
        except ValueError:
            return text
        err = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(err, dict):
            return str(err.get("message") or err)
        return str(err)

    # -- endpoints

    def search(self, query: str, count: int = 10, question: Optional[str] = None, content: bool = False,
               location: Optional[str] = None) -> List[Dict[str, Any]]:
        query = " ".join((query or "").split())
        if not 1 <= len(query) <= MAX_QUERY_CHARS:
            raise PennyError("A query must be 1-%d characters, this one is %d: %r" % (MAX_QUERY_CHARS, len(query), query[:70]))
        payload: Dict[str, Any] = {"query": query, "count": max(1, min(MAX_COUNT, int(count)))}
        if question and question.strip():
            payload["ori_question"] = " ".join(question.split())[:MAX_QUERY_CHARS]
        if content:
            payload["need_content"] = True
        if location:
            payload["localization"] = location
        hit = self.cache.get("search", payload, SEARCH_TTL)
        if hit is not None:
            with self._lock:
                self.stats["search_cached"] += 1
            return hit
        self._reserve()
        billed = False
        try:
            data = _unwrap(self._post("search", payload))
            rows = data.get("results") if isinstance(data, dict) else data
            out = [normalize_result(r) for r in (rows or []) if isinstance(r, dict)]
            out = [r for r in out if r["url"]]
            billed = bool(out)
        finally:
            self._settle("search", billed)
        self.cache.put("search", payload, out)
        return out

    def fetch(self, url: str) -> Dict[str, Any]:
        url = (url or "").strip()
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise PennyError("Only http(s) links can be fetched: %r" % url[:100])
        payload = {"url": url}
        hit = self.cache.get("fetch", payload, FETCH_TTL)
        if hit is not None:
            with self._lock:
                self.stats["fetch_cached"] += 1
            return hit
        self._reserve()
        billed = False
        try:
            page = normalize_page(self._post("fetch", payload, site=parts.netloc.lower()), url)
            billed = bool(page["content"].strip())
        finally:
            self._settle("fetch", billed)
        if billed:
            self.cache.put("fetch", payload, page)
        return page


def client_from_env(**kw: Any) -> Client:
    key, _ = load_key()
    mock = os.environ.get("PENNYSEARCH_MOCK")
    if mock:
        from .mock import mock_transport
        return Client(key or "mock-key", transport=mock_transport(Path(mock)), **kw)
    if not key:
        raise NoKeyError("No API key yet. Run: python %s setup" % _script_hint())
    return Client(key, **kw)


def _script_hint() -> str:
    return str(Path(sys.argv[0]).resolve()) if sys.argv and sys.argv[0] else "penny.py"
