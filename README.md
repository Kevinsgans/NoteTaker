# NoteTaker

A local note scanner for Raspberry Pi 5, Camera Module 3, and AI HAT+ 2. Capture and search work without inference. The original color image is preserved; suggested titles and tags can be corrected.

**Status:** implemented and tested with recorded/synthetic images on a development machine. Pi camera integration and Hailo inference require the hardware smoke test. No coursework accuracy or Pi latency result is claimed.

## Run on a development machine

Install [uv](https://docs.astral.sh/uv/), then from this directory:

```bash
uv sync --locked
uv run notetaker demo
uv run notetaker setup
uv run notetaker serve
```

Open <http://127.0.0.1:8000>. Username: `notetaker`; password: the one entered during setup. The default configuration uses a recorded image and disables AI. Change `provider` to `"fake"` to exercise background jobs with explicitly synthetic metadata. Add PNG/JPEG files to `evaluation/replay/` and use **Next replay frame** to select them.

The interface includes automatic/manual capture, course labels, original/corrected images, four-corner correction, editable metadata, course/date-filtered search, inference retry, and stage timings. Automatic capture must be enabled explicitly. “Save anyway” bypasses quality rejection so you can fix a crop manually. Capture settings applied in the UI last until restart; edit `config.toml` for persistent defaults.

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

## Architecture

- `camera`: the sole camera owner runs Picamera2 or recorded-image capture in a process. A bounded shared buffer holds only the latest preview.
- `vision`: CPU OpenCV page detection, quality scoring, stability/rearm logic, and perspective correction.
- `storage`: image directories and SQLite WAL/FULL transactions. Complete staging manifests can be recovered after interruption. Inference jobs are persisted alongside pages.
- `inference`: one persistent Hailo model worker, supervised with a hard per-job deadline; a fake provider supports offline tests.
- `runtime` and `app`: worker coordination, authenticated FastAPI endpoints, and a responsive local interface.
- `evaluation`: fixed-split replay measurements, retrieval ablations, human review scoring, and structured-log summaries.

See [architecture and durability](docs/architecture.md), [Pi deployment](docs/pi-setup.md), and [evaluation protocol](evaluation/README.md).

## Debug a complete capture flow

Run the service, capture a page in the browser, then stop with Ctrl-C:

```bash
uv run notetaker serve 2>&1 | rg '\[notetaker\]' > notetaker-debug.log
uv run notetaker summarize-log --log notetaker-debug.log --output data/live-metrics.json
```

Every measurement has a `[notetaker]` prefix. Model output and images stay local. A Pi startup records package versions, model/configuration hashes, RAM, firmware identification where available, and throttling state in `data_dir/provenance.json`. Save this file alongside logs for each run.

For a deterministic recorded-image flow:

```bash
uv run notetaker benchmark --manifest evaluation/demo.jsonl --queries evaluation/demo-queries.jsonl --output data/demo-benchmark 2>&1 | rg '\[notetaker\]' > notetaker-benchmark.log
```

The demo manifest is explicitly synthetic. Replace it with actual held-out notes before making quality claims. Reports use `null` for unavailable/unmeasured fields.

Development test results, screenshots, synthetic observations, and remaining hardware checks are recorded in [validation.md](docs/validation.md).
