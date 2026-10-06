# Evaluation protocol

## Freeze data before testing

Collect 90 distinct physical coursework pages, targeting 30 prose, 30 equation-heavy, and 30 diagram-heavy pages. Use 60 development pages and 30 held-out pages. Group by lecture/session and physical page; all recaptures remain in the same split. Hand-label the four true page corners in each source image. The manifest validator rejects session/page-group leakage when the manifest contains both splits.

Write 30 retrieval questions and relevant page IDs before seeing model suggestions. Keep all corpus pages in each evaluated split, not just pages where inference succeeds. Use `queries.jsonl` with `{ "query": "eigenvectors", "relevant_ids": ["page-17"] }` and an optional exact `course` filter.

Each line in the master manifest:

```json
{"id":"page-17-attempt-1","image":"images/page-17.png","session":"linear-algebra-lecture-4","page_group":"page-17","split":"heldout","category":"equations","course":"Linear algebra","corners":[[50,40],[850,40],[850,1150],[50,1150]],"manual":{"title":"Lecture 4","tags":[],"description":""}}
```

Paths are relative to the manifest. IDs distinguish image attempts; `page_group` identifies the physical page. For retrieval use one chosen attempt per physical page to avoid duplicate pages inflating scores. Category is `prose`, `equations`, or `diagrams`. Do not put ground-truth topic tags into baseline metadata unless you actually entered them during baseline use. Use the same manual fields, queries, and ranking in all comparisons.

## Compare preprocessing on development data

```bash
uv run notetaker --config deploy/pi.toml benchmark --manifest evaluation/coursework.jsonl --queries evaluation/development-queries.jsonl --split development --strategy full --output data/dev-full 2>&1 | rg '\[notetaker\]' > dev-full.log
uv run notetaker --config deploy/pi.toml benchmark --manifest evaluation/coursework.jsonl --queries evaluation/development-queries.jsonl --split development --strategy tiles --output data/dev-tiles 2>&1 | rg '\[notetaker\]' > dev-tiles.log
```

Use the actual installed config path on the Pi. Select the highest raw-metadata Recall@5 among strategies with p95 worker time below 30 seconds; prefer `full` on a tie. If neither improves retrieval or meets the time budget, keep AI experimental and report that outcome. Freeze thresholds, prompt, model/runtime versions, strategy, and query definitions before running the held-out split.

```bash
uv run notetaker benchmark --manifest evaluation/coursework.jsonl --queries evaluation/heldout-queries.jsonl --output data/heldout-run 2>&1 | rg '\[notetaker\]' > heldout-debug.log
```

Pass the Hailo configuration on the actual Pi. By default the local config disables inference, so it measures the CPU baseline only. Do not describe fake-provider output as a model result.

Each run saves a report, raw records, corrected output images, and a review template. Existing result directories are rejected to prevent accidental overwrite. A recorded-image run measures replay processing, not camera/autofocus or durable-save latency. Measure the live pipeline separately via application logs and `summarize-log`.

## Human review without rerunning the model

Copy `metadata-review.jsonl` from the run to a review file. Inspect each original and output, fill `corrected`, add annotations, and set `reviewed: true`. Preserve the frozen manifest and queries. Example annotations:

```json
{"id":"page-17-attempt-1","reviewed":true,"corrected":{"title":"Eigenvectors","tags":["linear algebra","eigenvalues"]},"annotations":{"legible":true,"clipped_content":false,"first_attempt_success":true,"title_accepted":false,"tag_count":3,"correct_tag_count":2,"unsupported_tag_count":1,"correction_seconds":12.5}}
```

`legible` and `clipped_content` describe the corrected output, including rejected attempts where appropriate. `first_attempt_success` requires an observed physical capture attempt; omit it for replay-only data. `title_accepted` means the original proposed title required no correction. Tag precision uses correct proposed tags divided by all proposed tags, not corrected tags. Omit unmeasured fields; zero is an observation, not a missing value.

```bash
uv run notetaker score-review --manifest evaluation/coursework.jsonl --benchmark-dir data/heldout-run --reviews evaluation/heldout-review.jsonl --queries evaluation/heldout-queries.jsonl --output data/heldout-reviewed.json
```

This reuses frozen raw outputs, validates all reviewed IDs and manifest/query hashes, and compares manual, manual+raw, and manual+corrected indexes. Without complete human review, corrected retrieval results remain unreported. Review assignments and disagreements should be recorded in the final write-up.

## Acceptance and report

Targets (not achieved-result claims): 90% first-attempt legible capture; warm live capture-to-save p95 <3 s after the stability gate; metadata p95 <30 s; raw AI Recall@5 improvement ≥10 percentage points; all acknowledged pages retained in interruption tests.

Report capture IoU/corner error and human legibility separately. Show category counts and failure examples. Report Recall@5 (fraction of relevant pages found in the top five) and MRR over the full ranking, using the same FTS5 tokenizer/weights in every condition. Show title acceptance, proposed-tag precision, unsupported tags, correction time, and review denominators. Small samples are exploratory; include raw counts and avoid claiming broad handwriting accuracy.

For performance separate cold model loading from warm inference; capture/autofocus, CPU preprocessing, PNG/fsync, queue delay, and runtime inference are distinct stages. `runtime_inference_ms` is not NPU-only execution time. Keep provenance and logs, report peak sampled host-worker RSS, CPU time, temperature and throttling state where available; accelerator-memory counters are currently unavailable. A replay on a laptop cannot support Pi performance claims.

Test real power loss separately from the automated process-kill tests. Record filesystem/storage medium and cooling. Report whether the application recovered pending jobs, complete orphan saves, and the last acknowledged page.

## Results

See `docs/validation.md` for development-machine test evidence. Real handwritten corpus and Pi/Hailo results remain pending; the software cannot collect your physical coursework or measure hardware that is not connected here.
