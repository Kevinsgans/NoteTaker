# Wrist Paddle

Move your right hand left and right to steer a paddle on the Pi display. Keep the ball in play for as many returns as you can. The first version needs one player, one camera, and one screen.

**Play on your Mac:** from this repository, run:

```bash
uv run --extra game wrist-paddle
```

Move the mouse horizontally, press **Space** to serve, and **Esc** to quit. Each paddle return earns one point; a miss ends the round. Press Space again to replay. The best score lasts for the session. The game dependencies are optional for [NoteTaker](#existing-project-notetaker).

**Status:** mouse mode and game logic tested locally. The Pi wrist adapter is implemented; physical camera tracking and Hailo-10H performance are not yet verified.

## First experiment: can the camera follow your wrist?

Use the Raspberry Pi 5, Camera Module 3, and AI HAT+ 2. With the full Hailo application stack installed, run the following **on the Pi, from the hailo-apps checkout**. These commands run the upstream pose demo, not the game:

```bash
source setup_env.sh
hailo-pose --arch hailo10h --list-models
```

If a compatible pose model is available and installed, start the camera demo:

```bash
hailo-pose --arch hailo10h --input rpi --show-fps
```

Stand facing the camera and move one wrist across the frame. Confirm the demo follows it and record the model name and displayed FPS. The [pose README](https://github.com/hailo-ai/hailo-apps/blob/main/hailo_apps/python/pipeline_apps/pose_estimation/README.md) illustrates older Hailo devices, so verify the model for the **10H** before building around it. If no compatible model is listed, continue with mouse input on the Mac while resolving device support. See the [Hailo run guide](https://github.com/hailo-ai/hailo-apps/blob/main/doc/user_guide/running_applications.md) for setup and CLI options.

## Play using your wrist on the Pi

After the upstream demo works, stop it with Ctrl+C to free the camera. In the terminal with the Hailo environment still active, save its interpreter path, then change to **this project's checkout** and launch the game:

```bash
POSE_PYTHON="$(command -v python)"
cd /path/to/NoteTaker
uv run --extra game wrist-paddle --input pose --pose-python "$POSE_PYTHON"
```

Replace the checkout path with your local directory. Use the Pi desktop/display. The game runs its pose worker in the Hailo environment and keeps Pygame in the project's environment. If the upstream demo required a specific Hailo-10H pose model, pass the same `--hef-path /path/to/model.hef` here.

Face the camera as the only player and press Space. Move your right wrist left or right. The Python [pose callback](https://github.com/hailo-ai/hailo-apps/blob/main/hailo_apps/python/pipeline_apps/pose_estimation/pose_estimation.py) supplies person-relative wrist coordinates; the adapter converts them to frame coordinates, and the game mirrors and smooths them. If the wrist disappears for half a second, play pauses. Show your wrist again to resume. In pose mode the FPS readout counts pose callbacks received per second; in mouse mode it shows display FPS.

To record the tracking status and score during a playtest (from the same terminal):

```bash
uv run --extra game wrist-paddle --input pose --pose-python "$POSE_PYTHON" 2>&1 | rg --line-buffered '\[wrist_paddle\]' > wrist-paddle-debug.log
```

Run the game logic and adapter tests with `uv run pytest -q tests/test_paddle.py`.

**First playtest:** finish a round using only your wrist after pressing Start. Check that moving left and right reaches both edges, one return gives exactly one point, and briefly stepping out of view pauses play without costing a round. Record one minute of play with the observed pose FPS and any missed or jittery movements. That is enough for the first demo.

---

## Existing project: NoteTaker

A local note scanner for Raspberry Pi 5, Camera Module 3, and AI HAT+ 2. Capture and search work without inference. The original color image is preserved; suggested titles and tags can be corrected.

**Status:** implemented and tested with recorded/synthetic images on a development machine. Pi camera integration and Hailo inference require the hardware smoke test. No coursework accuracy or Pi latency result is claimed.

### Run on a development machine

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

### Architecture

- `camera`: the sole camera owner runs Picamera2 or recorded-image capture in a process. A bounded shared buffer holds only the latest preview.
- `vision`: CPU OpenCV page detection, quality scoring, stability/rearm logic, and perspective correction.
- `storage`: image directories and SQLite WAL/FULL transactions. Complete staging manifests can be recovered after interruption. Inference jobs are persisted alongside pages.
- `inference`: one persistent Hailo model worker, supervised with a hard per-job deadline; a fake provider supports offline tests.
- `runtime` and `app`: worker coordination, authenticated FastAPI endpoints, and a responsive local interface.
- `evaluation`: fixed-split replay measurements, retrieval ablations, human review scoring, and structured-log summaries.

See [architecture and durability](docs/architecture.md), [Pi deployment](docs/pi-setup.md), and [evaluation protocol](evaluation/README.md).

### Debug a complete capture flow

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
