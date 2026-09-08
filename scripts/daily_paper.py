from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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


def make_http_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": "daily-paper-suggestion/1.0 (https://github.com/inoue0426/daily-paper-suggestion)"
    })
    return session


HTTP = make_http_session()


def arxiv_search(query: str, max_results: int, cfg: dict[str, Any]) -> list[Paper]:
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    r = HTTP.get(
        "https://export.arxiv.org/api/query",
        params=params,
        timeout=(cfg.get("arxiv_connect_timeout", 10), cfg.get("arxiv_read_timeout", 90)),
    )
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out: list[Paper] = []
    for e in feed.entries:
        out.append(Paper(
            title=re.sub(r"\s+", " ", e.title).strip(),
            abstract=re.sub(r"\s+", " ", e.summary).strip(),
            url=e.link,
            published=e.published,
            authors=[a.name for a in e.authors],
        ))
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
    r = requests.post(f"{base}/api/generate", json=payload, timeout=900)
    r.raise_for_status()
    return r.json()["response"].strip()


def candidate_pool(cfg: dict[str, Any], seen: set[str]) -> list[Paper]:
    merged: dict[str, Paper] = {}
    queries = cfg["arxiv_queries"]
    delay = cfg.get("arxiv_request_delay_seconds", 3)

    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(delay)
        try:
            found = arxiv_search(query, cfg.get("max_results_per_query", 20), cfg)
            print(f"arXiv: {len(found):2d} results for {query}")
        except requests.RequestException as exc:
            print(f"WARNING: arXiv query failed after retries: {query}\n  {exc}")
            continue
        for p in found:
            if p.paper_id not in seen:
                merged[p.paper_id] = p

    return list(merged.values())


def rank_with_ollama(cfg: dict[str, Any], papers: list[Paper], count: int) -> list[tuple[Paper, str]]:
    compact = []
    for i, p in enumerate(papers[: cfg.get("max_candidates_for_llm", 60)]):
        compact.append({
            "index": i,
            "title": p.title,
            "abstract": p.abstract[:2200],
            "published": p.published,
            "url": p.url,
        })

    count = min(count, len(compact))
    interests = "\n".join(f"- {x}" for x in cfg["research_interests"])
    prompt = f"""あなたは計算生物学・Biomedical AI研究者のために、今日読む価値が高い論文を選ぶresearch scoutです。

研究関心:
{interests}

候補から上位{count}本を選んでください。
重視する順序:
1. 研究者の考え方や問題設定を変えうるconceptual novelty
2. 自分の研究に転用できるmethod / representation / evaluation / experimental idea
3. perturbation, intervention, drug response, single-cell, causal/state-transition, AI-for-scienceとの関連
4. 単なる小さなbenchmark improvementより、再利用可能なideaを優先
5. 10本が似た話に偏らないよう、ある程度diversityを持たせる
6. 関連を無理に作らない。abstractで支えられない主張はしない

Candidates:
{json.dumps(compact, ensure_ascii=False)}

JSONのみを返してください:
{{"selected": [{{"index": 0, "why": "日本語で1-2文"}}]}}
selectedは必ず{count}件、indexは重複なし。
"""
    result = json.loads(ollama_generate(cfg, prompt, json_mode=True))
    raw = result.get("selected", [])

    selected: list[tuple[Paper, str]] = []
    used: set[int] = set()
    for item in raw:
        try:
            idx = int(item["index"])
        except Exception:
            continue
        if 0 <= idx < len(compact) and idx not in used:
            selected.append((papers[idx], str(item.get("why", "読む価値が高い候補。"))))
            used.add(idx)
        if len(selected) >= count:
            break

    if len(selected) < count:
        for idx in range(len(compact)):
            if idx not in used:
                selected.append((papers[idx], "ランキング結果の不足分として補完。"))
                used.add(idx)
            if len(selected) >= count:
                break

    return selected


def summarize_with_ollama(cfg: dict[str, Any], paper: Paper, why: str) -> str:
    interests = "\n".join(f"- {x}" for x in cfg["research_interests"])
    prompt = f"""以下の論文を、研究者が『全部を真似するのではなく、良いideaだけ盗む』ために要約してください。
基本は日本語。標準的なtechnical termは英語のままでよいです。宣伝調は禁止。abstractから言えないことは推測しないでください。

研究関心:
{interests}

Paper:
Title: {paper.title}
Authors: {", ".join(paper.authors)}
Published: {paper.published}
URL: {paper.url}
Abstract:
{paper.abstract}

選定理由:
{why}

以下のMarkdown形式で簡潔に書いてください。

### 30秒要約
2-4 bullet。

### この論文から盗むならここ
最も再利用価値があるideaを1-3個。methodそのものだけでなく、problem formulation、representation、loss、evaluation、experimental designでもよい。

### 自分の研究にどう使えそうか
本当に関係がある場合だけ具体的に。PerturbRxやintervention modeling等への接続を無理に作らない。

### そのまま信じない方がいい点
strongest caveat / alternative explanation / assumptionを1-2個。

### 読む優先度
「精読」「ざっと読む」「Abstractだけで十分」のどれか1つ + 理由1文。
"""
    return ollama_generate(cfg, prompt)


def main() -> None:
    cfg = load_config()
    seen = load_seen()
    papers = candidate_pool(cfg, seen)
    if not papers:
        raise SystemExit("No unseen paper candidates found; all arXiv requests may have failed or all results were already seen.")

    count = int(cfg.get("daily_paper_count", 10))
    count = min(count, len(papers))
    print(f"Candidate pool: {len(papers)} unseen papers; selecting {count}")

    selected = rank_with_ollama(cfg, papers, count)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    out = OUT_DIR / f"{today}.md"

    sections = [
        f"# Daily Paper Suggestions — {today}",
        "",
        f"今日は **{len(selected)}本**。全部を読む前提ではなく、各論文から使えるideaを拾うためのリストです。",
        "",
        "## 今日の10本",
        "",
    ]

    for rank, (paper, why) in enumerate(selected, start=1):
        print(f"[{rank}/{len(selected)}] Summarizing: {paper.title}")
        summary = summarize_with_ollama(cfg, paper, why)
        sections.extend([
            f"## {rank}. {paper.title}",
            "",
            f"- **URL:** {paper.url}",
            f"- **Published:** {paper.published}",
            f"- **Authors:** {', '.join(paper.authors)}",
            f"- **Source:** {paper.source}",
            "",
            "### 今日入れた理由",
            why,
            "",
            summary,
            "",
            "---",
            "",
        ])
        seen.add(paper.paper_id)

    out.write_text("\n".join(sections))
    save_seen(seen)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
