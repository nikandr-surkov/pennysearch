"""Gather: run a whole research plan in parallel, dedupe, score, pick what to read, fetch it.

plan.json
{
  "question": "...",                 # the user's question, sent with each query for better ranking
  "lang": "en",                      # wanted language (flags results in other scripts)
  "subquestions": [{"id": "Q1", "text": "..."}],
  "queries": [{"q": "...", "sub": "Q1", "count": 20, "lang": "en", "after": "2025", "site": ["iea.org"], "not_site": []}],
  "read": 24                         # how many pages to fetch in full (default 24)
}

Writes into the run folder: sources.json, raw/<id>.md (one per fetched page), stats.json.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .api import BudgetError, Client, PennyError
from .quality import assess, canonical_url, clean_url, pick_for_reading


def compose(q: Dict[str, Any], default_lang: Optional[str] = None) -> str:
    parts = [str(q.get("q") or q.get("query") or "").strip()]
    for s in q.get("site") or []:
        parts.append("site:" + str(s).strip())
    for s in q.get("not_site") or []:
        parts.append("-site:" + str(s).strip())
    lang = q.get("lang", default_lang)
    if lang:
        parts.append("lang:" + str(lang).replace(" ", ""))
    if q.get("after"):
        parts.append("after:" + str(q["after"]).strip())
    if q.get("before"):
        parts.append("before:" + str(q["before"]).strip())
    return " ".join(p for p in parts if p)


def load_plan(path: Path) -> Dict[str, Any]:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or not plan.get("queries"):
        raise PennyError("plan.json needs a \"queries\" list.")
    return plan


def gather(client: Client, plan: Dict[str, Any], out: Path, read: Optional[int] = None, workers: int = 16,
           log: Callable[[str], None] = print) -> Dict[str, Any]:
    out = Path(out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    question = plan.get("question", "")
    want = plan.get("lang")
    queries = [dict(q) for q in plan["queries"]]
    for q in queries:
        q["text"] = compose(q, want)

    # 1. all searches at once (the client keeps them under the rate limit)
    results: Dict[int, List[Dict[str, Any]]] = {}
    errors: List[str] = []
    stopped = None
    log("Searching: %d queries in parallel..." % len(queries))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(client.search, q["text"], int(q.get("count", 20)), question): i for i, q in enumerate(queries)}
        for f in as_completed(futs):
            i = futs[f]
            try:
                results[i] = f.result()
            except BudgetError as e:
                stopped = str(e)
                results[i] = []
            except PennyError as e:
                errors.append("%s: %s" % (queries[i]["text"], e))
                results[i] = []
    t_search = time.monotonic() - t0

    # 2. merge duplicates (and an earlier round in the same folder), keep where each source was found
    old_queries: List[str] = []
    by_url: Dict[str, Dict[str, Any]] = {}
    if (out / "sources.json").is_file():
        try:
            prev = json.loads((out / "sources.json").read_text(encoding="utf-8"))
            old_queries = list(prev.get("queries", []))
            for s in prev.get("sources", []):
                by_url[s["canonical"]] = s
        except (ValueError, KeyError):
            by_url, old_queries = {}, []
    offset = len(old_queries)
    found = 0
    for i, q in enumerate(queries):
        for rank, r in enumerate(results.get(i, [])):
            found += 1
            key = canonical_url(r["url"])
            s = by_url.get(key)
            if s is None:
                s = dict(r)
                s["url"] = clean_url(r["url"])
                s["canonical"] = key
                s["queries"] = []
                s["subs"] = []
                s["best_rank"] = rank
                by_url[key] = s
            else:
                for k in ("snippet", "summary", "date", "title"):
                    if not s.get(k) and r.get(k):
                        s[k] = r[k]
                s["authority"] = max(s.get("authority", -1), r.get("authority", -1))
                s["rerank"] = max(s.get("rerank", -1), r.get("rerank", -1))
                s["best_rank"] = min(s["best_rank"], rank)
            s["queries"].append(i + offset)
            if q.get("sub") and q["sub"] not in s["subs"]:
                s["subs"].append(q["sub"])
    sources = list(by_url.values())
    for s in sources:
        assess(s, want_lang=want)
        s["score"] = min(100, s["score"] + min(6, 2 * (len(s["queries"]) - 1)))  # found by several queries
        s.pop("content", None)
    # ids: kept from an earlier round, new sources continue the numbering in score order
    taken = [int(s["id"][1:]) for s in sources if str(s.get("id", "")).startswith("S") and s["id"][1:].isdigit()]
    nxt = max(taken) + 1 if taken else 1
    for s in sorted([s for s in sources if not s.get("id")], key=lambda s: (-s["score"], s["best_rank"])):
        s["id"] = "S%02d" % nxt
        nxt += 1
    sources.sort(key=lambda s: (-s["score"], s["best_rank"]))

    # 3. read the best pages in full; a page that fails, times out or is only a paywall stub is swapped for the next candidate
    n_read = int(read if read is not None else plan.get("read", 24))
    queue = pick_for_reading([s for s in sources if not s.get("read")], n_read + max(6, n_read // 2))
    log("Found %d results, %d unique sources. Reading the best %d..." % (found, len(sources), min(n_read, len(queue))))
    t1 = time.monotonic()
    good = 0
    if not stopped and queue:
        pending = iter(queue)
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 12))) as ex:
            inflight: Dict[Any, Dict[str, Any]] = {}

            def submit_next() -> bool:
                for s in pending:
                    inflight[ex.submit(client.fetch, s["url"])] = s
                    return True
                return False

            for _ in range(min(n_read, len(queue))):
                submit_next()
            while inflight:
                done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
                for f in done:
                    s = inflight.pop(f)
                    text = ""
                    try:
                        page = f.result()
                        text = page.get("content", "")
                        if not text.strip():
                            s["fetch_error"] = "empty page"
                    except BudgetError as e:
                        stopped = str(e)
                        s["fetch_error"] = "budget"
                    except PennyError as e:
                        s["fetch_error"] = str(e)[:200]
                    if text.strip():
                        s["read"] = True
                        s["chars"] = len(text)
                        if len(text) < 1500 and "short page" not in s["flags"]:
                            s["flags"].append("short page")  # often a paywall: only the teaser came back
                        else:
                            good += 1
                        head = "# %s\n\nsource: %s\nurl: %s\ndate: %s\n\n---\n\n" % (page.get("title") or s["title"], s["id"], s["url"], s.get("published") or s.get("date") or "")
                        (out / "raw" / (s["id"] + ".md")).write_text(head + text, encoding="utf-8")
                    if not stopped and good + len(inflight) < n_read:
                        submit_next()
    t_read = time.monotonic() - t1

    zero = [queries[i]["text"] for i in range(len(queries)) if not results.get(i)]
    stats = {
        "question": question,
        "queries": len(queries),
        "queries_without_results": zero,
        "results": found,
        "sources": len(sources),
        "read": sum(1 for s in sources if s.get("read")),
        "search_seconds": round(t_search, 2),
        "read_seconds": round(t_read, 2),
        "seconds": round(time.monotonic() - t0, 2),
        "paid_calls": client.billed_calls,
        "cached_calls": client.stats["search_cached"] + client.stats["fetch_cached"],
        "cost_usd": round(client.cost, 5),
        "errors": errors[:20],
        "stopped": stopped,
        "flags": _flag_counts(sources),
    }
    (out / "sources.json").write_text(json.dumps({"question": question, "lang": want, "subquestions": plan.get("subquestions", []),
                                                    "queries": old_queries + [q["text"] for q in queries], "sources": sources}, ensure_ascii=False, indent=1), encoding="utf-8")
    round_seconds = stats["seconds"]
    prev = out / "stats.json"
    if prev.is_file():  # a follow-up round adds to the totals
        try:
            old = json.loads(prev.read_text(encoding="utf-8"))
            for k in ("paid_calls", "cached_calls", "seconds", "search_seconds", "read_seconds", "cost_usd", "queries", "results"):
                stats[k] = round(stats[k] + old.get(k, 0), 5) if isinstance(stats[k], float) else stats[k] + old.get(k, 0)
            stats["rounds"] = old.get("rounds", 1) + 1
        except ValueError:
            pass
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    log("Done in %.1f s: %d sources, %d read, %d paid calls, $%.5f%s." % (
        round_seconds, stats["sources"], stats["read"], client.billed_calls, client.cost,
        " (stopped: budget)" if stopped else ""))
    if zero:
        log("%d queries returned nothing (the topic may be filtered, or the query too narrow)." % len(zero))
    return stats


def _flag_counts(sources: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for s in sources:
        for f in s.get("flags", []):
            key = "old" if f.startswith("old") else f
            out[key] = out.get(key, 0) + 1
    return out
