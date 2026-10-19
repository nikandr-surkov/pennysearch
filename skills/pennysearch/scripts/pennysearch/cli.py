"""Command line: setup, check, search, fetch, gather, verify, report, cost."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import List, Optional

from . import __version__
from .api import (KEYS_URL, PRICE_PER_CALL, TOPUP_URL, AuthError, BalanceError, Cache, Client, NoKeyError, PennyError,
                  cache_dir, client_from_env, config_path, load_key, mask, save_key)
from .gather import compose, gather, load_plan
from .quality import assess
from .report import render_files
from .verify import verify


def _out(s: str = "") -> None:
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("ascii", "replace").decode("ascii"))


def _client(args) -> Client:
    kw = {}
    if getattr(args, "budget", None) is not None:
        kw["budget"] = args.budget
    if getattr(args, "no_cache", False):
        kw["cache"] = Cache(enabled=False)
    if getattr(args, "qps", None):
        kw["search_qps"] = args.qps
    return client_from_env(**kw)


# ---------------------------------------------------------------------------

def cmd_setup(args) -> int:
    _out("PennySearch setup (about 2 minutes)\n")
    _out("1. Opening the key page: " + KEYS_URL)
    _out("   Sign in, click \"Create new API key\", name it pennysearch, copy it.")
    _out("   Your balance pays for searches: $0.75 per 1,000. Top up a few dollars at " + TOPUP_URL)
    if not args.no_browser:
        try:
            webbrowser.open(KEYS_URL)
        except Exception:
            pass
    _out("")
    key = getpass.getpass("2. Paste the key here (it stays hidden) and press Enter: ").strip()
    if not key:
        _out("No key entered. Run setup again when you have one.")
        return 2
    _out("3. Testing the key with one search...")
    try:
        Client(key, cache=Cache(enabled=False)).search("pennysearch key test", count=1)
    except AuthError:
        _out("The key was rejected. Copy it again (it starts with sk-) and rerun setup.")
        return 3
    except BalanceError:
        p = save_key(key)
        _out("The key works but the balance is empty. Saved to %s. Top up at %s" % (p, TOPUP_URL))
        return 4
    except PennyError as e:
        _out("Could not test the key: %s" % mask(str(e), key))
        return 1
    p = save_key(key)
    _out("Works. Saved to %s (only your user can read it)." % p)
    _out("You can also set PENNYSEARCH_API_KEY instead; the environment variable wins.")
    return 0


def cmd_check(args) -> int:
    key, where = load_key()
    if os.environ.get("PENNYSEARCH_MOCK"):
        _out("ok (mock mode: %s)" % os.environ["PENNYSEARCH_MOCK"])
        return 0
    if not key:
        _out("no key: ask the user to run setup in their own terminal")
        return 2
    try:
        Client(key, cache=Cache(enabled=False)).search("pennysearch status check", count=1)
    except AuthError:
        _out("key rejected (from %s)" % where)
        return 3
    except BalanceError:
        _out("balance too low: top up at " + TOPUP_URL)
        return 4
    except PennyError as e:
        _out("error: %s" % mask(str(e), key))
        return 1
    _out("ok (key from %s)" % ("environment" if where.startswith("env") else "config file"))
    return 0


def cmd_search(args) -> int:
    c = _client(args)
    q = compose({"q": args.query, "site": args.site, "not_site": args.not_site, "lang": args.lang, "after": args.after, "before": args.before})
    rows = c.search(q, count=args.count, question=args.question, content=args.content)
    for r in rows:
        assess(r, want_lang=args.lang)
    rows.sort(key=lambda r: -r["score"])
    if args.json:
        _out(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        if not rows:
            _out("No results for: %s" % q)
        for i, r in enumerate(rows, 1):
            flags = (" [" + ", ".join(r["flags"]) + "]") if r["flags"] else ""
            _out("[%d] %s  (score %d)%s" % (i, r["title"] or r["url"], r["score"], flags))
            _out("    %s" % r["url"])
            _out("    %s | %s | authority %s | relevance %s" % (r["published"] or "no date", r["type"], _n(r["authority"]), _n(r["rerank"])))
            if r["snippet"]:
                _out("    " + r["snippet"][:240])
    _cost_line(c)
    return 0


def _n(v: float) -> str:
    return "-" if v is None or v < 0 else ("%g" % v)


def cmd_fetch(args) -> int:
    c = _client(args)
    page = c.fetch(args.url)
    if args.json:
        _out(json.dumps(page, ensure_ascii=False, indent=1))
    else:
        _out("# " + (page.get("title") or args.url))
        _out(page.get("content", "")[: args.max_chars] if args.max_chars else page.get("content", ""))
    _cost_line(c)
    return 0


def cmd_gather(args) -> int:
    c = _client(args)
    plan = load_plan(Path(args.plan))
    stats = gather(c, plan, Path(args.out), read=args.read, workers=args.workers, log=_out)
    if args.json:
        _out(json.dumps(stats, ensure_ascii=False, indent=1))
    return 5 if stats.get("stopped") else 0


def cmd_verify(args) -> int:
    t = verify(Path(args.run))
    _out("Receipts: %d of %d quotes found on their pages (%d exact, %d close, %d missing)." % (
        t["exact"] + t["close"], t["evidence"], t["exact"], t["close"], t["missing"]))
    _out("Claims: %d verified (2+ sites), %d single source, %d without a receipt (leave these out of the report)." % (
        t["verified"], t["single source"], t["no receipt"]))
    return 0


def cmd_report(args) -> int:
    out = render_files(Path(args.spec), Path(args.out), Path(args.run) if args.run else None)
    _out("Report: %s" % out.resolve())
    if args.open:
        try:
            webbrowser.open(out.resolve().as_uri())
        except Exception:
            pass
    return 0


def cmd_cost(args) -> int:
    p = Path(args.run) / "stats.json"
    if not p.is_file():
        _out("No stats.json in %s yet." % args.run)
        return 1
    s = json.loads(p.read_text(encoding="utf-8"))
    _out("%d paid calls = $%.5f (%d from cache, free). %.1f s." % (s.get("paid_calls", 0), s.get("cost_usd", 0), s.get("cached_calls", 0), s.get("seconds", 0)))
    return 0


def cmd_cache(args) -> int:
    root = cache_dir()
    if args.action == "where":
        _out(str(root))
        return 0
    n = 0
    if root.is_dir():
        for p in root.rglob("*.json"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    _out("Removed %d cached replies from %s" % (n, root))
    return 0


def _cost_line(c: Client) -> None:
    sys.stderr.write("(%d paid calls, $%.5f; %d from cache)\n" % (c.billed_calls, c.cost, c.stats["search_cached"] + c.stats["fetch_cached"]))


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="penny.py", description="PennySearch: deep research for a penny. $%.5f per paid call." % PRICE_PER_CALL)
    p.add_argument("--version", action="version", version="pennysearch " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="save your API key (run this yourself, in a terminal)")
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("check", help="exit 0 ready, 2 no key, 3 bad key, 4 low balance")
    s.set_defaults(fn=cmd_check)

    def money_opts(sp):
        sp.add_argument("--budget", type=float, help="stop before spending more than this many USD")
        sp.add_argument("--no-cache", action="store_true", help="always call the API")

    s = sub.add_parser("search", help="one search")
    s.add_argument("query")
    s.add_argument("--count", type=int, default=10)
    s.add_argument("--question", help="the user's question, improves ranking")
    s.add_argument("--lang", help="en, zh, or a list like en,zh")
    s.add_argument("--after")
    s.add_argument("--before")
    s.add_argument("--site", action="append", default=[])
    s.add_argument("--not-site", action="append", default=[])
    s.add_argument("--content", action="store_true", help="include full page text")
    s.add_argument("--json", action="store_true")
    money_opts(s)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("fetch", help="read one page as text")
    s.add_argument("url")
    s.add_argument("--json", action="store_true")
    s.add_argument("--max-chars", type=int, default=0)
    money_opts(s)
    s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("gather", help="run a plan: all searches in parallel, then read the best pages")
    s.add_argument("plan")
    s.add_argument("--out", required=True)
    s.add_argument("--read", type=int, help="pages to read in full (default from the plan, else 24)")
    s.add_argument("--workers", type=int, default=16)
    s.add_argument("--qps", type=float, help="searches per second (default 40, the API allows 100)")
    s.add_argument("--json", action="store_true")
    money_opts(s)
    s.set_defaults(fn=cmd_gather)

    s = sub.add_parser("verify", help="check every quote in claims.json against the saved pages")
    s.add_argument("run")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("report", help="build the HTML report from spec.json")
    s.add_argument("spec")
    s.add_argument("--out", required=True)
    s.add_argument("--run", help="run folder (default: the folder of spec.json)")
    s.add_argument("--open", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("cost", help="what a run cost")
    s.add_argument("run")
    s.set_defaults(fn=cmd_cost)

    s = sub.add_parser("cache", help="where the cache is, or clear it")
    s.add_argument("action", choices=["where", "clear"])
    s.set_defaults(fn=cmd_cache)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except NoKeyError as e:
        _out(str(e))
        return e.exit_code
    except PennyError as e:
        _out("error: %s" % e)
        return e.exit_code
    except KeyboardInterrupt:
        return 130
