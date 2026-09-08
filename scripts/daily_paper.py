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
DOCS_DIR = ROOT / "docs"

@dataclass
class Paper:
    title: str; abstract: str; url: str; published: str; authors: list[str]; source: str = "arXiv"
    @property
    def paper_id(self) -> str: return self.url.rstrip("/").split("/")[-1]

def load_config(): return json.loads(CFG_PATH.read_text())
def load_seen(): return set(json.loads(SEEN_PATH.read_text())) if SEEN_PATH.exists() else set()
def save_seen(seen):
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True); SEEN_PATH.write_text(json.dumps(sorted(seen), indent=2)+"\n")

def make_http_session():
    retry=Retry(total=4,connect=4,read=4,status=4,backoff_factor=2,status_forcelist=(429,500,502,503,504),allowed_methods=frozenset({"GET"}),respect_retry_after_header=True)
    s=requests.Session(); s.mount("https://",HTTPAdapter(max_retries=retry)); s.headers.update({"User-Agent":"daily-paper-suggestion/1.0 (https://github.com/inoue0426/daily-paper-suggestion)"}); return s
HTTP=make_http_session()

def arxiv_search(query,max_results,cfg):
    r=HTTP.get("https://export.arxiv.org/api/query",params={"search_query":query,"start":0,"max_results":max_results,"sortBy":"submittedDate","sortOrder":"descending"},timeout=(cfg.get("arxiv_connect_timeout",10),cfg.get("arxiv_read_timeout",90))); r.raise_for_status(); feed=feedparser.parse(r.content)
    return [Paper(re.sub(r"\s+"," ",e.title).strip(),re.sub(r"\s+"," ",e.summary).strip(),e.link,e.published,[a.name for a in e.authors]) for e in feed.entries]

def ollama_generate(cfg,prompt,json_mode=False):
    payload={"model":cfg.get("ollama_model","qwen3:8b"),"prompt":prompt,"stream":False}
    if json_mode: payload["format"]="json"
    r=requests.post(cfg.get("ollama_base_url","http://127.0.0.1:11434").rstrip("/")+"/api/generate",json=payload,timeout=900); r.raise_for_status(); return r.json()["response"].strip()

def candidate_pool(cfg,seen):
    merged={}; delay=cfg.get("arxiv_request_delay_seconds",3)
    for i,q in enumerate(cfg["arxiv_queries"]):
        if i: time.sleep(delay)
        try: found=arxiv_search(q,cfg.get("max_results_per_query",20),cfg); print(f"arXiv: {len(found):2d} results for {q}")
        except requests.RequestException as exc: print(f"WARNING: arXiv query failed: {q}\n  {exc}"); continue
        for p in found:
            if p.paper_id not in seen: merged[p.paper_id]=p
    return list(merged.values())

def rank_with_ollama(cfg,papers,count):
    compact=[{"index":i,"title":p.title,"abstract":p.abstract[:2200],"published":p.published,"url":p.url} for i,p in enumerate(papers[:cfg.get("max_candidates_for_llm",60)])]; count=min(count,len(compact)); interests="\n".join(f"- {x}" for x in cfg["research_interests"])
    prompt=f"""あなたはBiomedical AI研究者のresearch scoutです。研究関心:\n{interests}\n候補から上位{count}本を選ぶ。conceptual novelty、再利用可能なmethod/representation/evaluation/experimental ideaを優先。小さなbenchmark gainより使えるidea、10本のdiversityを重視。関連を無理に作らずabstractで支えられない主張は禁止。\nCandidates:\n{json.dumps(compact,ensure_ascii=False)}\nJSONのみ: {{\"selected\":[{{\"index\":0,\"why\":\"日本語で1-2文\"}}]}}。必ず{count}件、重複なし。"""
    raw=json.loads(ollama_generate(cfg,prompt,True)).get("selected",[]); selected=[]; used=set()
    for item in raw:
        try: idx=int(item["index"])
        except Exception: continue
        if 0<=idx<len(compact) and idx not in used: selected.append((papers[idx],str(item.get("why","読む価値が高い候補。")))); used.add(idx)
        if len(selected)>=count: break
    for idx in range(len(compact)):
        if len(selected)>=count: break
        if idx not in used: selected.append((papers[idx],"ランキング結果の不足分として補完。")); used.add(idx)
    return selected

def summarize_with_ollama(cfg,paper,why):
    interests="\n".join(f"- {x}" for x in cfg["research_interests"])
    prompt=f"""以下の論文から良いideaだけを拾う。基本日本語、technical termは英語可。宣伝調禁止、abstract外の推測禁止。\n研究関心:\n{interests}\nTitle:{paper.title}\nAuthors:{', '.join(paper.authors)}\nAbstract:{paper.abstract}\n選定理由:{why}\n次のJSONのみ返す: {{\"summary\":\"30秒要約。Markdown bullet 2-4個\",\"steal\":\"この論文から盗むならここ。再利用価値の高いidea 1-3個をMarkdown bullet\",\"use\":\"自分の研究に本当に使えそうな点。無理な接続は禁止。Markdown\",\"doubt\":\"strongest caveat/alternative explanation/assumption 1-2個。Markdown\",\"priority\":\"精読|ざっと読む|Abstractだけで十分\"}}"""
    return json.loads(ollama_generate(cfg,prompt,True))

def main():
    cfg=load_config(); seen=load_seen(); papers=candidate_pool(cfg,seen)
    if not papers: raise SystemExit("No unseen paper candidates found.")
    count=min(int(cfg.get("daily_paper_count",10)),len(papers)); selected=rank_with_ollama(cfg,papers,count); today=datetime.now(timezone.utc).date().isoformat(); OUT_DIR.mkdir(parents=True,exist_ok=True); DOCS_DIR.mkdir(parents=True,exist_ok=True)
    sections=[f"# Daily Paper Suggestions — {today}","",f"今日は **{len(selected)}本**。全部を読む前提ではなく、各論文から使えるideaを拾うためのリストです。",""]; page=[]
    for rank,(p,why) in enumerate(selected,1):
        print(f"[{rank}/{len(selected)}] Summarizing: {p.title}"); s=summarize_with_ollama(cfg,p,why)
        sections += [f"## {rank}. {p.title}","",f"- **URL:** {p.url}",f"- **Published:** {p.published}",f"- **Authors:** {', '.join(p.authors)}","",f"### 今日入れた理由\n{why}",f"### 30秒要約\n{s.get('summary','')}",f"### この論文から盗むならここ\n{s.get('steal','')}",f"### 自分の研究にどう使えそうか\n{s.get('use','')}",f"### そのまま信じない方がいい点\n{s.get('doubt','')}",f"### 読む優先度\n**{s.get('priority','')}**","","---",""]
        page.append({"title":p.title,"url":p.url,"published":p.published,"authors":", ".join(p.authors),"why":why,**s}); seen.add(p.paper_id)
    (OUT_DIR/f"{today}.md").write_text("\n".join(sections)); (DOCS_DIR/"latest.json").write_text(json.dumps({"date":today,"papers":page},ensure_ascii=False,indent=2)); save_seen(seen); print(f"Wrote daily report and docs/latest.json")
if __name__=="__main__": main()
