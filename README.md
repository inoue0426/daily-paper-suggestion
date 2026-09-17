# daily-paper-suggestion

A lightweight research CI that selects 10 papers per day from arXiv, summarizes them with a local Ollama model, and saves the results as a daily research feed.

👉 **GitHub Pages:** https://inoue0426.github.io/daily-paper-suggestion/

## What it does

1. Fetches recent arXiv candidates around perturbation, drug response, single-cell biology, causal intervention, AI for science, and related areas.
2. Ranks candidates with a local Ollama model.
3. Selects the top 10 while explicitly encouraging topic diversity.
4. Produces the following for each paper:
   - 30-second summary
   - Ideas worth stealing
   - How it could be useful for my research
   - Strongest caveat or reason for skepticism
   - Reading priority: deep read / skim / abstract only
5. Saves all 10 papers to `papers/daily/YYYY-MM-DD.md`.
6. Tracks previously seen papers in `papers/seen.json`.
7. When run through `run_daily.sh`, automatically commits and pushes the new report.

The goal is not to read all 10 papers in full. The goal is to scan broadly every day and extract reusable problem formulations, representations, methods, losses, evaluation ideas, and experimental designs.

## Setup

```bash
git clone https://github.com/inoue0426/daily-paper-suggestion.git
cd daily-paper-suggestion
python3 -m pip install -r requirements.txt
ollama pull qwen3:8b
```

If the Ollama server is not already running:

```bash
ollama serve
```

Manual test:

```bash
bash run_daily.sh
```

Because the pipeline summarizes 10 papers sequentially, a full daily run takes longer than a single-paper test.

## Daily scheduling on macOS

A GitHub Actions runner cannot access Ollama running on your Mac's localhost, so scheduling is handled locally on the Mac.

```bash
chmod +x run_daily.sh
crontab -e
```

Example: run every morning at 8:00 AM:

```cron
0 8 * * * /bin/bash /ABSOLUTE/PATH/daily-paper-suggestion/run_daily.sh >> /ABSOLUTE/PATH/daily-paper-suggestion/daily-paper.log 2>&1
```

The Mac must be awake at that time and the Ollama server must be available.

## Configuration

You can change the following settings in `daily-paper.json`:

- `ollama_model`: default `qwen3:8b`
- `daily_paper_count`: number of recommendations per day; default `10`
- `research_interests`: ranking criteria
- `arxiv_queries`: search scope
- `max_results_per_query`: number of candidates retrieved per query
- `max_candidates_for_llm`: maximum number of candidates sent to Ollama

Example model change:

```json
"ollama_model": "qwen3:14b"
```

## Design principle

This bot is not meant to accept each paper's narrative at face value. It extracts reusable ideas while also surfacing the strongest caveat, alternative explanation, or assumption worth checking.

Connections to ongoing projects such as PerturbRx should only be made when they are directly supported by the abstract. The prompts explicitly discourage forced connections and unsupported claims. The final decision about what to read, trust, or incorporate into research remains with the researcher.
