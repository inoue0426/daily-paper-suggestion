# daily-paper-suggestion

毎日1本、今の研究に効きそうな論文を arXiv から選び、Local Ollama で批判的に要約して保存するための小さな research CI です。

## What it does

1. arXiv から perturbation / drug response / single-cell / causal intervention / AI-for-science 周辺の新着候補を取得
2. Local Ollama で候補をランキング
3. 1本だけ選択
4. 日本語で以下を出力
   - 30秒要約
   - 何が新しいか
   - 自分の研究にどう効くか
   - 一番疑うべき点
   - 読むときの問い3つ
   - 精読 / ざっと読む / Abstractだけで十分 の判定
5. `papers/daily/YYYY-MM-DD.md` に保存
6. 既読論文を `papers/seen.json` に記録
7. `run_daily.sh` 経由なら自動で commit / push

## Setup

```bash
git clone https://github.com/inoue0426/daily-paper-suggestion.git
cd daily-paper-suggestion
python3 -m pip install -r requirements.txt
ollama pull qwen3:8b
```

Ollama server が起動していなければ:

```bash
ollama serve
```

手動テスト:

```bash
bash run_daily.sh
```

## Daily scheduling on macOS

GitHub Actions runner から Mac の localhost Ollama には接続できないため、スケジュールは Mac 側で実行します。

まず runner を実行可能にします:

```bash
chmod +x run_daily.sh
```

例: 毎朝 8:00 に実行する cron:

```bash
crontab -e
```

以下を追加（`/ABSOLUTE/PATH` は clone した場所に変更）:

```cron
0 8 * * * /bin/bash /ABSOLUTE/PATH/daily-paper-suggestion/run_daily.sh >> /ABSOLUTE/PATH/daily-paper-suggestion/daily-paper.log 2>&1
```

Mac がその時間に起動していて、Ollama server が利用可能である必要があります。

## Configuration

`daily-paper.json` で以下を変更できます:

- `ollama_model`: default `qwen3:8b`
- `research_interests`: 推薦基準
- `arxiv_queries`: 探索範囲
- `max_results_per_query`: queryごとの候補数
- `max_candidates_for_llm`: Ollamaに渡す最大候補数

モデルを変える例:

```json
"ollama_model": "qwen3:14b"
```

## Design principle

この bot は単なる要約器ではなく、研究者自身の critical thinking を補助するために、毎回「一番疑うべき点」と「読むときの問い」を明示的に生成します。最終的な研究判断をモデルに委譲する設計にはしていません。
