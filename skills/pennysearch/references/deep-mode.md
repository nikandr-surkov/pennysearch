# Deep mode, step by step

All files live in one run folder: `./research/<short-slug>-<YYYYMMDD-HHMM>/`.

```
plan.json      the queries (you)
sources.json   every unique source with score, flags, ids S01, S02, ... (gather)
raw/S01.md     the pages read in full (gather)
claims.json    claims with verbatim quotes (scouts), then receipts and status (verify, checker)
review.md      what is missing or weak (skeptic)
spec.json      what the report says (writer)
stats.json     calls, cost, time, queries without results (gather)
report.html    the result (report)
```

## 1. Plan

Split the question into 3-6 sub-questions a reader would ask. For each, write 2-5 queries. Mix:
- one broad query in plain words,
- one or two with `site:` for the primary source (statistics office, regulator, the company's own docs or filings, the paper),
- one with `after:` for the latest numbers,
- one in the local language when the topic is local (`lang:zh`, `lang:de`, ...),
- one that looks for the other side ("criticism", "problems", "risks", "vs").

```json
{
  "question": "Is the electric car market still growing?",
  "lang": "en",
  "subquestions": [
    {"id": "Q1", "text": "How many electric cars were sold in 2024 and 2025?"},
    {"id": "Q2", "text": "Which countries drive the growth?"},
    {"id": "Q3", "text": "What slows it down?"}
  ],
  "queries": [
    {"q": "global electric car sales 2024", "sub": "Q1", "count": 20},
    {"q": "Global EV Outlook electric car sales", "sub": "Q1", "site": ["iea.org"]},
    {"q": "electric car sales 2025 first half", "sub": "Q1", "after": "2025-06"},
    {"q": "China new energy vehicle sales 2024", "sub": "Q2", "lang": "zh,en"},
    {"q": "Europe electric car sales slowdown", "sub": "Q3", "after": "2025"}
  ],
  "read": 24
}
```

`count` is free to raise (the price is per call, not per result), up to 50.

## 2. Gather

```
python3 <skill>/scripts/penny.py gather <run>/plan.json --out <run> --budget 0.25
```

It prints progress and a summary: unique sources, pages read, paid calls, cost, seconds, and the
queries that returned nothing (worth a sentence in the report: the topic may be filtered or the
query too narrow). Read `sources.json` to see which ids belong to which sub-question (`subs`).

## 3. Scouts

One scout per sub-question, in parallel when you can start subagents. Give each the role prompt
from roles.md, the sub-question, the run folder, and the list of source ids for it (sources whose
`subs` contain it, plus any other read page that looks relevant). Each scout returns a JSON list:

```json
[{"id": "Q1-1", "sub": "Q1", "claim": "More than 17 million electric cars were sold in 2024.",
  "value": 17.3, "unit": "million", "date": "2024",
  "evidence": [{"source": "S03", "quote": "copied word for word from raw/S03.md"}]}]
```

Merge all scouts' lists into `claims.json` and renumber the ids C1, C2, ... Merge claims that say
the same thing and keep all their evidence (that is how a claim gets two independent sites).

## 4. Verify

```
python3 <skill>/scripts/penny.py verify <run>
```

It matches every quote against its page (exact, or close for small differences like quotes,
dashes and spacing) and writes `match`, `link` and `page_text` into each evidence entry, plus
`receipts`, `independent` and `status` into each claim. A quote that is not on its page is
`missing`; a claim with no matched quote is `no receipt`. Send those back to the scout once, then drop them.

## 5. Check

The checker reads claims.json and the pages. It compares numbers across sources (year, scope,
unit, definition), sets `"status": "conflicting"` and a `"note"` where they really disagree, and
removes claims that the quote does not actually support. verify never overwrites "conflicting".

## 6. Skeptic

The skeptic writes `review.md`: missing sub-questions, one-sided or single-site sourcing, stale
numbers, press releases or forum posts carrying a key claim, and the strongest counter-view.
At most one follow-up round (plan2.json, gather into the same folder, scouts on the new pages,
verify again).

## 7. Write

The writer builds `spec.json` (format in spec.md) from claims with status verified, single source
or conflicting. Every sentence with a fact carries its claim ids `[C3]`. Charts only use numbers
that are claims. Mention the queries that returned nothing if they matter for the topic.

## 8. Render and report back

```
python3 <skill>/scripts/penny.py report <run>/spec.json --out <run>/report.html --open
python3 <skill>/scripts/penny.py cost <run>
```

In chat: the short answer, the strongest caveat, the path to report.html, and the cost.
