# daily-paper-suggestion

毎日10本、今の研究に効きそうな論文を arXiv から選び、Local Ollama で日本語中心に要約して保存するための research CI です。

## What it does

1. arXiv から perturbation / drug response / single-cell / causal intervention / AI-for-science 周辺の新着候補を取得
2. Local Ollama で候補をランキング
3. 上位10本を、似た論文だけに偏らないように選択
4. 各論文について日本語中心で以下を出力
   - 30秒要約
   - この論文から盗むならここ
   - 自分の研究にどう使えそうか
   - そのまま信じない方がいい点
   - 精読 / ざっと読む / Abstractだけで十分 の判定
5. `papers/daily/YYYY-MM-DD.md` に10本まとめて保存
6. 既読論文を `papers/seen.json` に記録
7. `run_daily.sh` 経由なら自動で commit / push

狙いは「10本すべてを精読する」ことではなく、毎日広くscanして、各論文から再利用できるproblem formulation / representation / method / loss / evaluation / experimental designを拾うことです。

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

10本を順番に要約するため、1本版より実行時間は長くなります。

## Daily scheduling on macOS

GitHub Actions runner から Mac の localhost Ollama には接続できないため、スケジュールは Mac 側で実行します。

```bash
chmod +x run_daily.sh
crontab -e
```

例: 毎朝 8:00 に実行:

```cron
0 8 * * * /bin/bash /ABSOLUTE/PATH/daily-paper-suggestion/run_daily.sh >> /ABSOLUTE/PATH/daily-paper-suggestion/daily-paper.log 2>&1
```

Mac がその時間に起動していて、Ollama server が利用可能である必要があります。

## Configuration

`daily-paper.json` で以下を変更できます:

- `ollama_model`: default `qwen3:8b`
- `daily_paper_count`: 1日あたりの推薦本数。default `10`
- `research_interests`: 推薦基準
- `arxiv_queries`: 探索範囲
- `max_results_per_query`: queryごとの候補数
- `max_candidates_for_llm`: Ollamaに渡す最大候補数

モデルを変える例:

```json
"ollama_model": "qwen3:14b"
```

## Design principle

この bot は論文のstoryを鵜呑みにするための要約器ではありません。各論文から「使えるidea」を抽出しつつ、同時に strongest caveat を明示します。

PerturbRx 等への関連付けも、abstractから自然に言える場合だけ行い、無理な接続やunsupported claimは避けるpromptにしています。最終的に何を読むか、何を研究に取り込むかの判断は研究者側に残します。
