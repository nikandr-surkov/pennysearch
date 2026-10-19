"""Tests for PennySearch. No network, no key: a fake transport stands in for the API.

    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "pennysearch" / "scripts"))

from pennysearch import api, quality, verify as verify_mod, report  # noqa: E402
from pennysearch.api import BudgetError, Cache, Client, AuthError, BalanceError, Throttle  # noqa: E402
from pennysearch.gather import compose, gather  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.lock = threading.Lock()

    def now(self):
        return self.t

    def sleep(self, d):
        with self.lock:
            self.t += d


def fake(results_by_query=None, pages=None, fail_first=0, status=None):
    """Transport that answers searches from a dict and fetches from `pages` (url -> text)."""
    state = {"calls": [], "fails": fail_first}

    def transport(url, body, headers, timeout):
        payload = json.loads(body.decode("utf-8"))
        state["calls"].append((url.rsplit("/", 1)[-1], payload))
        if status:
            return status, {}, json.dumps({"error": {"message": "nope"}}).encode()
        if state["fails"] > 0:
            state["fails"] -= 1
            return 429, {"Retry-After": "0"}, b"{}"
        if url.endswith("/search"):
            rows = (results_by_query or {}).get(payload["query"], (results_by_query or {}).get("*", []))
            return 200, {}, json.dumps({"results": rows}).encode()
        text = (pages or {}).get(payload["url"], "")
        return 200, {}, json.dumps({"title": "Page", "content": text}).encode()

    transport.state = state
    return transport


def client(transport, **kw):
    clock = FakeClock()
    return Client("sk-test-123456", transport=transport, cache=kw.pop("cache", Cache(enabled=False)), clock=clock.now, sleep=clock.sleep, rng=lambda: 0.5, **kw)


def r(url, title="Title", authority=3, rerank=2.5, date_="2026-09-01", snippet="snippet text", summary=""):
    return {"url": url, "title": title, "authority": authority, "rerank_score": rerank, "date": date_, "snippet": snippet, "summary": summary}


class TestQuality(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(quality.canonical_url("http://www.Example.com/a/?utm_source=x&id=2#top"), "https://example.com/a?id=2")
        self.assertEqual(quality.canonical_url("https://example.com/"), "https://example.com/")

    def test_types(self):
        self.assertEqual(quality.source_type("https://www.iea.org/reports/x"), "primary")
        self.assertEqual(quality.source_type("https://data.gov.uk/x"), "primary")
        self.assertEqual(quality.source_type("https://www.prnewswire.com/news/x"), "press release")
        self.assertEqual(quality.source_type("https://example.com/press-releases/x"), "press release")
        self.assertEqual(quality.source_type("https://www.reddit.com/r/x"), "forum / social")
        self.assertEqual(quality.source_type("https://docs.python.org/3/"), "official docs")
        self.assertEqual(quality.source_type("https://www.reuters.com/x"), "news")

    def test_lang(self):
        self.assertEqual(quality.guess_lang("Electric cars sold 17 million"), "latin")
        self.assertEqual(quality.guess_lang("电动汽车销量增长"), "zh")
        self.assertEqual(quality.guess_lang("Продажи электромобилей"), "ru")

    def test_flags_and_score(self):
        good = quality.assess(dict(r("https://www.iea.org/x")), want_lang="en", today=date(2026, 10, 19))
        spam = quality.assess(dict(r("https://cheap-tools.biz/x", title="Top 10 best search APIs 2026", authority=1, rerank=1)), want_lang="en", today=date(2026, 10, 19))
        zh = quality.assess(dict(r("https://example.cn/x", title="电动汽车销量", snippet="电动汽车销量增长很快")), want_lang="en", today=date(2026, 10, 19))
        self.assertGreater(good["score"], spam["score"])
        self.assertIn("listicle", spam["flags"])
        self.assertIn("low authority", spam["flags"])
        self.assertIn("not English", zh["flags"])
        old = quality.assess(dict(r("https://x.com/a", date_="2019-01-01")), today=date(2026, 10, 19))
        self.assertTrue(any(f.startswith("old") for f in old["flags"]))

    def test_pick_variety(self):
        srcs = [dict(id="S%02d" % i, score=90 - i, domain="same.com", flags=[]) for i in range(5)] + [dict(id="S10", score=50, domain="other.org", flags=[])]
        picked = quality.pick_for_reading(srcs, 3, per_site=2)
        self.assertEqual([s["id"] for s in picked], ["S00", "S01", "S10"])

    def test_parse_date(self):
        self.assertEqual(quality.parse_date("2025-03-07"), date(2025, 3, 7))
        self.assertEqual(quality.parse_date("2024年5月"), date(2024, 5, 1))
        self.assertIsNone(quality.parse_date(""))


class TestClient(unittest.TestCase):
    def test_search_bills_only_results(self):
        t = fake({"has": [r("https://a.com/1")], "none": []})
        c = client(t)
        self.assertEqual(len(c.search("has")), 1)
        self.assertEqual(c.search("none"), [])
        self.assertEqual(c.stats["search_billed"], 1)
        self.assertEqual(c.stats["search_empty"], 1)
        self.assertAlmostEqual(c.cost, 0.00075)

    def test_retries_then_succeeds(self):
        t = fake({"*": [r("https://a.com/1")]}, fail_first=2)
        c = client(t)
        self.assertEqual(len(c.search("q")), 1)
        self.assertEqual(c.stats["retries"], 2)

    def test_auth_and_balance(self):
        with self.assertRaises(AuthError):
            client(fake(status=401)).search("q")
        with self.assertRaises(BalanceError):
            client(fake(status=402)).search("q")

    def test_budget_stops_before_overspend(self):
        c = client(fake({"*": [r("https://a.com/1")]}), budget=0.002)  # 2 calls fit, the 3rd does not
        c.search("a")
        c.search("b")
        with self.assertRaises(BudgetError):
            c.search("c")
        self.assertEqual(c.billed_calls, 2)

    def test_cache_hit_is_free(self):
        with tempfile.TemporaryDirectory() as d:
            t = fake({"*": [r("https://a.com/1")]})
            c = client(t, cache=Cache(Path(d)))
            c.search("same", count=5)
            c.search("same", count=5)
            self.assertEqual(c.billed_calls, 1)
            self.assertEqual(c.stats["search_cached"], 1)
            self.assertEqual(len(t.state["calls"]), 1)

    def test_query_limits(self):
        with self.assertRaises(api.PennyError):
            client(fake()).search("x" * 200)

    def test_key_never_in_errors(self):
        key = "sk-secret-abcdef"
        def tr(url, body, headers, timeout):
            return 400, {}, json.dumps({"error": {"message": "bad request for " + key}}).encode()
        c = Client(key, transport=tr, cache=Cache(enabled=False), sleep=lambda s: None)
        with self.assertRaises(api.PennyError) as cm:
            c.search("q")
        self.assertNotIn(key, str(cm.exception))

    def test_throttle_spacing(self):
        clock = FakeClock()
        th = Throttle(10, clock.now, clock.sleep)
        for _ in range(11):
            th.wait()
        self.assertAlmostEqual(clock.t, 1.0, places=6)


class TestGatherVerifyReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "run"
        page_a = "# Global EV Outlook\n\nIn 2024, more than 17 million electric cars were sold worldwide, over 20% of all new cars.\n"
        page_b = ("Sales report\n\nElectric car sales reached 17.3 million units in 2024, according to the agency. China accounted for "
                  "almost two thirds of the total.")
        filler = "\n\n" + "Background paragraph about the car market and its history. " * 30  # real pages are long; stubs under 1,500 chars trigger a backup read
        page_a, page_b = page_a + filler, page_b + filler
        self.pages = {"https://www.iea.org/reports/ev-2025": page_a, "https://www.reuters.com/ev-sales": page_b,
                      "https://www.prnewswire.com/pr/ev": "Buy our charger now."}
        res = {
            "ev sales 2024 lang:en": [r("https://www.iea.org/reports/ev-2025", "Global EV Outlook 2025"),
                                      r("https://www.reuters.com/ev-sales?utm_source=x", "EV sales hit record")],
            "ev market share lang:en": [r("https://www.reuters.com/ev-sales", "EV sales hit record"),
                                        r("https://www.prnewswire.com/pr/ev", "Top 10 best EV chargers 2026", authority=1, rerank=1)],
            "censored topic lang:en": [],
        }
        self.transport = fake(res, self.pages)
        self.plan = {"question": "Is the EV market growing?", "lang": "en",
                     "queries": [{"q": "ev sales 2024", "sub": "Q1"}, {"q": "ev market share", "sub": "Q2"}, {"q": "censored topic"}], "read": 2}

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_flow(self):
        c = client(self.transport)
        stats = gather(c, self.plan, self.run, log=lambda s: None)
        src = json.loads((self.run / "sources.json").read_text(encoding="utf-8"))["sources"]
        self.assertEqual(stats["sources"], 3)  # the reuters duplicate (tracking param) is merged
        self.assertEqual(stats["queries_without_results"], ["censored topic lang:en"])
        reuters = next(s for s in src if "reuters" in s["url"])
        self.assertEqual(len(reuters["queries"]), 2)
        self.assertEqual(stats["read"], 2)  # the press release is skipped while better pages exist
        ids = {s["domain"]: s["id"] for s in src}

        claims = [
            {"id": "C1", "claim": "More than 17 million EVs were sold in 2024.", "evidence": [
                {"source": ids["iea.org"], "quote": "In 2024, more than 17 million electric cars were sold worldwide"},
                {"source": ids["reuters.com"], "quote": "Electric car sales reached 17.3 million units in 2024"}]},
            {"id": "C2", "claim": "China made almost two thirds of sales.", "evidence": [
                {"source": ids["reuters.com"], "quote": "China accounted for almost two-thirds of the total."}]},
            {"id": "C3", "claim": "Made up.", "evidence": [{"source": ids["iea.org"], "quote": "Sales fell by half in 2024 across every market"}]},
        ]
        (self.run / "claims.json").write_text(json.dumps(claims), encoding="utf-8")
        t = verify_mod.verify(self.run)
        out = {c["id"]: c for c in json.loads((self.run / "claims.json").read_text(encoding="utf-8"))}
        self.assertEqual(out["C1"]["status"], "verified")
        self.assertEqual(out["C2"]["status"], "single source")  # "two-thirds" vs "two thirds": a close match
        self.assertIn(out["C2"]["evidence"][0]["match"], ("close", "exact"))
        self.assertEqual(out["C3"]["status"], "no receipt")
        self.assertIn("#:~:text=", out["C1"]["evidence"][0]["link"])
        self.assertEqual(t["verified"], 1)

        spec = {"title": "Is the EV market growing? <script>alert(1)</script>", "answer": "Yes [C1]. China leads [C2].",
                "takeaways": ["17 million sold [C1]"], "numbers": [{"label": "Sold", "value": "17.3", "unit": "M", "claims": ["C1"]}],
                "charts": [{"type": "bar", "title": "Sales", "labels": ["2022", "2023", "2024"], "values": [10.2, 14, 17.3], "unit": "M"},
                           {"type": "line", "title": "Share", "labels": ["2022", "2024"], "series": [{"name": "World", "values": [14, 22]}]},
                           {"type": "hbar", "title": "Price", "labels": ["A", "B"], "values": [0.75, 5], "lower_is_better": True}],
                "sections": [{"heading": "Details", "body": "Text with **bold** [C2].\n\n- one\n- two"}]}
        (self.run / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        page = report.render_files(self.run / "spec.json", self.run / "report.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn('href="#C1"', page)
        self.assertIn("Open at the quote", page)
        self.assertNotIn('id="C3"', page)  # claims without a receipt never reach the report
        self.assertIn("<svg", page)

    def test_follow_up_round_keeps_ids(self):
        c = client(self.transport)
        gather(c, self.plan, self.run, log=lambda s: None)
        first = {s["canonical"]: s["id"] for s in json.loads((self.run / "sources.json").read_text(encoding="utf-8"))["sources"]}
        plan2 = {"question": "x", "lang": "en", "queries": [{"q": "ev market share"}], "read": 5}
        gather(client(self.transport), plan2, self.run, log=lambda s: None)
        second = {s["canonical"]: s["id"] for s in json.loads((self.run / "sources.json").read_text(encoding="utf-8"))["sources"]}
        for k, v in first.items():
            self.assertEqual(second[k], v)

    def test_budget_stops_gather(self):
        c = client(self.transport, budget=0.0015)
        stats = gather(c, self.plan, self.run, log=lambda s: None)
        self.assertTrue(stats["stopped"])
        self.assertLessEqual(c.billed_calls, 2)


class TestPage(unittest.TestCase):
    def test_markdown_escapes_removed(self):
        page = api.normalize_page({"title": r"Solid\-State", "content": r"\# Funding Exceeds US\$1\.3 Billion in 2025\, Says TrendForce \(SSB\)"}, "https://x.com")
        self.assertEqual(page["content"], "# Funding Exceeds US$1.3 Billion in 2025, Says TrendForce (SSB)")
        self.assertEqual(page["title"], "Solid-State")


class TestFlags(unittest.TestCase):
    def test_hosted_and_clickbait(self):
        s = quality.assess(dict(r("https://elearning.asc.edu.ag/people-get-wrong-openai-ipo", title="What Most People Get Wrong About The Openai Ipo")), want_lang="en")
        self.assertEqual(s["type"], "hosted page")
        self.assertIn("clickbait?", s["flags"])
        self.assertEqual(quality.source_type("https://www.mit.edu/research"), "primary")


class TestCompose(unittest.TestCase):
    def test_operators(self):
        self.assertEqual(compose({"q": "ev sales", "site": ["iea.org"], "lang": "en", "after": "2025"}), "ev sales site:iea.org lang:en after:2025")


class TestTextFragment(unittest.TestCase):
    def test_short_and_long(self):
        self.assertEqual(verify_mod.text_fragment("https://a.com/x#old", "Short sentence here"), "https://a.com/x#:~:text=Short%20sentence%20here")
        long = "one two three four five six seven eight nine ten eleven twelve"
        self.assertEqual(verify_mod.text_fragment("https://a.com/x", long),
                         "https://a.com/x#:~:text=one%20two%20three%20four%20five,eight%20nine%20ten%20eleven%20twelve")
        self.assertIn("%2D", verify_mod.text_fragment("https://a.com", "a well-known fact"))


if __name__ == "__main__":
    unittest.main()
