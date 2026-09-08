from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import requests

ROOT = Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / "daily-paper.json"
SEEN_PATH = ROOT / "papers" / "seen.json"
OUT_DIR = ROOT / "papers" / "daily"


@dataclass
class Paper:
    title: str
    abstract: str
    url: str
    published: str
    authors: list[str]
    source: str = "arXiv"

    @property
    def paper_id(self) -> str:
        return self.url.rstrip("/").split("/")[-1]


def load_config() -> dict[str, Any]:
    return json.loads(CFG_PATH.read_text())


def load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    return set(json.loads(SEEN_PATH.read_text()))


def save_seen(seen: set[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=2) + "\n")


def arxiv_search(query: str, max_results: int) -> list[Paper]:
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    r = requests.get("https://export.arxiv.org/api/query", params=params, timeout=30)
    r.raise_for_status()
    feed = feedparser.loads(r.text)
    out = []
    for e in feed.entries:
        out.append(
            Paper(
                title=re.sub(r"\s+", " ", e.title).strip(),
                abstract=re.sub(r"\s+", " ", e.summary).strip(),
                url=e.link,
                published=e.published,
                authors=[a.name for a in e.authors],
            )
        )
    return out


def ollama_generate(cfg: dict[str, Any], prompt: str, json_mode: bool = False) -> str:
    base = cfg.get("ollama_base_url", "http://127.0.0.1:11434").rstrip("/")
    payload = {
        "model": cfg.get("ollama_model", "qwen3:8b"),
        "prompt": prompt,
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"
    r = requests.post(f"{base}/api/generate", json=payload, timeout=600)
    r.raise_for_status()
    return r.json()["response"].strip()


def candidate_pool(cfg: dict[str, Any], seen: set[str]) -> list[Paper]:
    merged: dict[str, Paper] = {}
    for query in cfg["arxiv_queries"]:
        for p in arxiv_search(query, cfg.get("max_results_per_query", 20)):
            if p.paper_id not in seen:
                merged[p.paper_id] = p
    return list(merged.values())


def rank_with_ollama(cfg: dict[str, Any], papers: list[Paper]) -> tuple[Paper, str]:
    compact = []
    for i, p in enumerate(papers[: cfg.get("max_candidates_for_llm", 40)]):
        compact.append({
            "index": i,
            "title": p.title,
            "abstract": p.abstract[:2600],
            "published": p.published,
            "url": p.url,
        })

    interests = "\n".join(f"- {x}" for x in cfg["research_interests"])
    prompt = f"""You are selecting exactly ONE paper for a computational biomedical AI researcher to read today.

Research interests:
{interests}

Selection priorities:
1. Could materially change how the researcher thinks about intervention/perturbation modeling.
2. Offers a conceptual or methodological tool, not merely a small benchmark gain.
3. Relevant to drug response, perturbation biology, representation learning, causal/state-transition modeling, or AI-for-science.
4. Recent work is preferred, but an unusually important conceptual paper may win.
5. Avoid redundant candidates.

Candidates:
{json.dumps(compact, ensure_ascii=False)}

Return strict JSON only:
{{"index": integer, "why_this_one": "2-4 sentences"}}
"""
    result = json.loads(ollama_generate(cfg, prompt, json_mode=True))
    idx = int(result["index"])
    if idx < 0 or idx >= len(compact):
        idx = 0
    return papers[idx], str(result.get("why_this_one", "Selected as today's highest-value paper."))


def summarize_with_ollama(cfg: dict[str, Any], paper: Paper, why: str) -> str:
    interests = "\n".join(f"- {x}" for x in cfg["research_interests"])
    prompt = f"""Summarize the paper below for a researcher. Be critical, not promotional.

Research interests:
{interests}

Paper:
Title: {paper.title}
Authors: {", ".join(paper.authors)}
Published: {paper.published}
URL: {paper.url}
Abstract:
{paper.abstract}

Why it was selected:
{why}

Write concise Markdown in Japanese while preserving standard technical terms in English.

Required sections:
## 30秒要約
3-5 bullets.

## 何が新しいか
Explain the central conceptual novelty, not just model components.

## 研究にどう効くか
Connect specifically to perturbation-conditioned state transition, drug response, intervention geometry, representation learning, or research methodology when genuinely relevant. Do not force a connection.

## 一番疑うべき点
Give the strongest alternative explanation, assumption, confound, or evaluation weakness.

## 読むときの問い
Give exactly 3 questions the researcher should answer while reading.

## 判定
One of: 精読 / ざっと読む / Abstractだけで十分
Then one sentence why.

Do not claim anything not supported by the abstract. Explicitly say when full-text verification is needed.
"""
    return ollama_generate(cfg, prompt)


def main() -> None:
    cfg = load_config()
    seen = load_seen()
    papers = candidate_pool(cfg, seen)
    if not papers:
        raise SystemExit("No unseen paper candidates found.")

    paper, why = rank_with_ollama(cfg, papers)
    summary = summarize_with_ollama(cfg, paper, why)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    out = OUT_DIR / f"{today}.md"
    md = f"""# {paper.title}

- **URL:** {paper.url}
- **Published:** {paper.published}
- **Authors:** {", ".join(paper.authors)}
- **Source:** {paper.source}

## 今日これを選んだ理由

{why}

{summary}
"""
    out.write_text(md)
    seen.add(paper.paper_id)
    save_seen(seen)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
