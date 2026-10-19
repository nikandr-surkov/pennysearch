# spec.json

The writer's output. Everything is optional except `title` and `answer`. Text fields accept
`**bold**`, `*italic*`, `` `code` ``, `[label](https://link)`, `- ` lists, and claim citations
`[C3]` or `[C3, C7]`, which become colored chips (green verified, amber single source, red
conflicting) that jump to the receipt.

```json
{
  "title": "Is the electric car market still growing?",
  "question": "Are electric car sales still growing worldwide, and where does the growth come from?",
  "mode": "deep",
  "answer": "Yes. More than 17 million electric cars were sold in 2024, over one in five new cars [C1]. China made almost two thirds of them [C2].",
  "takeaways": [
    "Sales passed 17 million in 2024 [C1].",
    "China is the engine: about 11 million [C2]."
  ],
  "numbers": [
    {"label": "Sold in 2024", "value": "17.3", "unit": "M", "note": "battery and plug-in", "claims": ["C1"]}
  ],
  "charts": [
    {"type": "bar", "title": "Electric car sales worldwide", "subtitle": "million per year", "unit": "M",
     "labels": ["2021", "2022", "2023", "2024"], "values": [6.6, 10.2, 14, 17.3], "claims": ["C1", "C4"],
     "note": "IEA figures, rounded"},
    {"type": "line", "title": "Share of new cars", "unit": "%", "labels": ["2021", "2022", "2023", "2024"],
     "series": [{"name": "China", "values": [16, 29, 38, 48]}, {"name": "World", "values": [9, 14, 18, 22]}]},
    {"type": "hbar", "title": "Price per 1,000 searches", "unit": "$", "labels": ["This API", "Brave"], "values": [0.75, 5],
     "lower_is_better": true}
  ],
  "sections": [
    {"heading": "Where the growth comes from", "body": "China sold about 11 million [C2].\n\n- Europe was flat [C5]\n- The US grew a little [C6]"}
  ],
  "disagreements": [
    {"topic": "How big is China's share?", "body": "One source counts plug-in hybrids, the other does not.", "claims": ["C2", "C9"]}
  ],
  "caveats": ["Two queries about subsidies returned nothing."]
}
```

Bars highlight the largest value (or the smallest with `lower_is_better`), or the index in `highlight`.
The report adds on its own: the receipts meter, the source mix (type, language, year), every
receipt with its quote and an "Open at the quote" link, the sources with trust scores and flags,
and how the run was made (queries, time, cost).
