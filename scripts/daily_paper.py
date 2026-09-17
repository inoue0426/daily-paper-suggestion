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
    compact=[{"index":i,"title":p.title,"abstract":p.abstract[:2200],"published":p.published,"url":p.url} for i,p in enumerate(papers[:cfg.get("max_candidates_for_llm",90)])]
    count=min(count,len(compact)); interests="\n".join(f"- {x}" for x in cfg["research_interests"])
    max_perturb=int(cfg.get("max_perturbation_like",3)); min_outside=int(cfg.get("min_outside_core_biomed",3))
    prompt=f"""You are a research scout for a biomedical AI researcher. Research interests:\n{interests}\n\nSelect the top {count} papers from the candidates. Prioritize conceptual novelty and reusable methods, representations, evaluation ideas, or experimental designs. Prefer ideas that could change how a research problem is approached over small benchmark gains.\n\nImportant: enforce exploration diversity.\n- Select at most {max_perturb} papers primarily focused on perturbation, drug response, or single-cell perturbation.\n- Select at least {min_outside} papers from outside the core biomedical perturbation area, such as AI for science, agents, scientific reasoning, causal ML, world models, graph/geometric ML, uncertainty, active learning, verification, or generative modeling.\n- Do not over-select papers from the same method family or biological task.\n- Treat the set as '10 windows into different research directions,' not a ranking of near-duplicate papers.\n- Do not rank a paper highly merely because it is close to the current research. A distant field should rank highly if it contains a strong transferable idea.\n- Do not invent connections or capabilities that are not supported by the abstract.\n\nCandidates:\n{json.dumps(compact,ensure_ascii=False)}\n\nReturn JSON only: {{\"selected\":[{{\"index\":0,\"why\":\"1-2 concise English sentences explaining why this is a useful and distinct research window\",\"theme\":\"short English theme name\"}}]}}. Return exactly {count} unique entries."""
    raw=json.loads(ollama_generate(cfg,prompt,True)).get("selected",[]); selected=[]; used=set(); themes=[]
    for item in raw:
        try: idx=int(item["index"])
        except Exception: continue
        if 0<=idx<len(compact) and idx not in used:
            why=str(item.get("why","A high-value paper worth scanning.")); theme=str(item.get("theme","other"))
            selected.append((papers[idx],f"[{theme}] {why}")); themes.append(theme); used.add(idx)
        if len(selected)>=count: break
    for idx in range(len(compact)):
        if len(selected)>=count: break
        if idx not in used: selected.append((papers[idx],"[fallback] Added to fill the remaining slot after ranking.")); used.add(idx)
    return selected

def summarize_with_ollama(cfg,paper,why):
    interests="\n".join(f"- {x}" for x in cfg["research_interests"])
    prompt=f"""Extract the best ideas from the paper below. Write in clear, concise English. Technical terms may remain as standard technical English. Avoid promotional language and do not speculate beyond the abstract.\nResearch interests:\n{interests}\nTitle:{paper.title}\nAuthors:{', '.join(paper.authors)}\nAbstract:{paper.abstract}\nSelection reason:{why}\nReturn only the following JSON: {{\"summary\":\"A 30-second summary in 2-4 Markdown bullets\",\"steal\":\"1-3 highly reusable ideas worth stealing, in Markdown bullets\",\"use\":\"How this could genuinely be useful for my research. Do not force a connection. Markdown\",\"doubt\":\"1-2 strongest caveats, alternative explanations, or assumptions worth checking. Markdown\",\"priority\":\"Deep read|Skim|Abstract only\"}}"""
    return json.loads(ollama_generate(cfg,prompt,True))

def main():
    cfg=load_config(); seen=load_seen(); papers=candidate_pool(cfg,seen)
    if not papers: raise SystemExit("No unseen paper candidates found.")
    count=min(int(cfg.get("daily_paper_count",10)),len(papers)); selected=rank_with_ollama(cfg,papers,count); today=datetime.now(timezone.utc).date().isoformat(); OUT_DIR.mkdir(parents=True,exist_ok=True); DOCS_DIR.mkdir(parents=True,exist_ok=True)
    sections=[f"# Daily Paper Suggestions — {today}","",f"Today's list has **{len(selected)} papers**. The goal is not to read every paper in full, but to scan diverse research directions and extract reusable ideas.",""]; page=[]
    for rank,(p,why) in enumerate(selected,1):
        print(f"[{rank}/{len(selected)}] Summarizing: {p.title}"); s=summarize_with_ollama(cfg,p,why)
        sections += [f"## {rank}. {p.title}","",f"- **URL:** {p.url}",f"- **Published:** {p.published}",f"- **Authors:** {', '.join(p.authors)}","",f"### Why it made today's list\n{why}",f"### 30-second summary\n{s.get('summary','')}",f"### Ideas worth stealing\n{s.get('steal','')}",f"### How this could help my research\n{s.get('use','')}",f"### Strongest caveat\n{s.get('doubt','')}",f"### Reading priority\n**{s.get('priority','')}**","","---",""]
        page.append({"title":p.title,"url":p.url,"published":p.published,"authors":", ".join(p.authors),"why":why,**s}); seen.add(p.paper_id)
    (OUT_DIR/f"{today}.md").write_text("\n".join(sections)); (DOCS_DIR/"latest.json").write_text(json.dumps({"date":today,"papers":page},ensure_ascii=False,indent=2)); save_seen(seen); print(f"Wrote daily report and docs/latest.json")
if __name__=="__main__": main()
