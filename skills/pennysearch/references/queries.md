# Writing queries that find good sources

- **Short and specific.** 3-8 words, the nouns that would be on the page you want: `global electric car sales 2024`, not "how are electric cars doing these days".
- **Pass the question.** `--question` (or the plan's `question`) is sent with every search and improves ranking; the query itself can stay short.
- **Go to the source.** Add `site:` for the body that publishes the number: `site:iea.org`, `site:sec.gov`, `site:europa.eu`, a company's `investor.` site, the project's docs or GitHub.
- **Fresh numbers.** `after:2025` or `after:2025-06-01` for anything that changes; `before:` to look at an older state.
- **Language.** `lang:en` keeps results in English (results in other scripts are flagged `not English`). For a local topic add a second query in that language, `lang:zh` or `lang:zh,en`, and translate the quotes in the report.
- **Exclude noise.** `-site:pinterest.com`, `-"press release"`, `-coupon`.
- **Look for the other side.** One query per sub-question with "criticism", "problems", "risks", "failed", "lawsuit" or "vs".
- **Exact phrases** for names of reports, laws or products: `"Global EV Outlook 2025"`.
- **More results cost nothing extra.** The price is per call, so use `count` 20-50 in deep mode instead of more calls.
- **No results?** Shorten the query, drop operators one by one, or try another language. Several sensitive topics return nothing on purpose; say so in the report rather than guessing.

Reading the output: results are sorted by trust score. A high score with no flags is a good first read.
`press release`, `listicle` and `sponsored?` mean marketing; `forum / social` means one person's
experience; `low authority` comes from the provider's own site rating.
