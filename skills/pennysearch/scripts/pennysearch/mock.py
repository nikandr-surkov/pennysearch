"""Offline stand-in for the search API (PENNYSEARCH_MOCK=<folder>), used by the tests and for trying
the workflow without a key.

<folder>/search.json   {"default": [results...], "by_query": {"query text": [results...]}}
<folder>/pages/<name>.md  page text; <name> is the URL's host + path with every non-alphanumeric char as "_"
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Dict, Tuple


def page_name(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return re.sub(r"[^A-Za-z0-9]+", "_", (p.netloc + p.path).strip("/")).strip("_")[:120]


def mock_transport(folder: Path):
    folder = Path(folder)
    data = json.loads((folder / "search.json").read_text(encoding="utf-8")) if (folder / "search.json").is_file() else {"default": []}

    def transport(url: str, body: bytes, headers: Dict[str, str], timeout: float) -> Tuple[int, Dict[str, str], bytes]:
        payload = json.loads(body.decode("utf-8"))
        if url.endswith("/search"):
            q = payload.get("query", "")
            rows = data.get("by_query", {}).get(q, data.get("default", []))
            return 200, {}, json.dumps({"results": rows[: payload.get("count", 10)]}).encode("utf-8")
        page = folder / "pages" / (page_name(payload.get("url", "")) + ".md")
        if not page.is_file():
            return 200, {}, json.dumps({"title": "", "content": ""}).encode("utf-8")
        return 200, {}, json.dumps({"title": page.stem, "content": page.read_text(encoding="utf-8")}).encode("utf-8")

    return transport
