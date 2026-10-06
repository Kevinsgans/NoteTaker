# Development validation — 2026-10-06

These results were obtained on the development Mac (ARM64, Python 3.14.6). Camera input was a generated printed-text image, not handwriting. Hailo inference was mocked/faked for integration tests. These observations do not establish Raspberry Pi performance, real handwriting accuracy, or achievement of the portfolio targets.

## Automated checks

- **32 tests passed** in the final complete suite.
- Ruff lint and format checks passed.
- Python source distribution and wheel built successfully, including browser assets.
- One dependency warning: Starlette deprecates its HTTPX-based test client. It did not affect test results.

Coverage includes page detection/warping and blur rejection; stability/rearming; automatic capture without repeated saves; preserved original images; idempotent capture events and perceptual duplicate flags; inference failures and hard timeout termination; absent model with functioning capture; malformed VLM output and context cleanup; manual edits surviving late model output; stale jobs invalidated by recropping; session leakage checks; frozen-output human review and retrieval comparisons; authenticated API access and cross-origin write rejection.

Durability tests terminate a subprocess after writing the original, writing the corrected image, flushing the manifest, renaming the directory, starting the database transaction, and committing. Recovery retains complete saves and withholds incomplete ones. Disk-full and corrupt-orphan conditions are also tested. This is process-crash testing, not a physical power-loss test.

## Browser verification

Headless installed Chrome exercised capture → saved page → fake metadata → manual edit → search → corner adjustment. The test passed at 1440-pixel desktop width and 390-pixel mobile width with no JavaScript errors and no horizontal page overflow. Screenshots were visually inspected.

Run again with your Chrome/Chromium executable:

```bash
uv run --with playwright scripts/browser_smoke.py --browser '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
```

The script uses an isolated temporary data directory and an ephemeral password kept in process memory. It does not change your application password or notes. Artifacts are written under `data/browser-smoke/`: desktop/mobile screenshots and server log. The test environment is removed afterward.

## Measured synthetic observations

| Observation | Result | Sample and scope |
| --- | --- | --- |
| Page crop IoU | 0.9983 | One generated flat rectangular page |
| Mean corner error | 0.49 pixels | Same synthetic page, annotated known rectangle |
| CPU preprocessing | 4.65 ms | Single recorded-image benchmark |
| Replay processing total | 13.14 ms | Includes output PNG write; excludes camera/AI |
| Capture-to-durable-save | 19.74 ms | One browser-triggered replay capture |
| Capture-to-searchable | 253.68 ms | Same replay capture, **fake** metadata, cold worker |

These are individual observations, not statistically useful latency distributions. The benchmark supports p50/p95 reporting for larger datasets, with sample counts and nulls for missing measurements. Model retries/recrops are excluded from first-capture end-to-end metrics. Cold and warm inference are reported separately by the log summarizer.

Local raw artifacts: `data/demo-benchmark/report.json`, `data/demo-benchmark/raw.jsonl`, `data/browser-smoke/server.log`, and `data/browser-smoke/metrics.json`. They are ignored by Git; archive relevant artifacts alongside the final coursework report. The synthetic benchmark's zero retrieval score with inference disabled is expected and is not a model-quality result.

## Remaining hardware/data validation

- Camera Module 3 focus, exposure, sensor-mode transitions, camera disconnect/reconnect, and actual screen interaction.
- Hailo-10H driver/firmware/runtime/model compatibility and real VLM output quality. Pin the successful combination via the hardware smoke report.
- Development calibration and full-page-versus-tiles selection on actual handwriting.
- Frozen 30-page/30-query held-out evaluation, correction measurements, and retrieval improvement.
- Sustained Pi temperatures, throttling, host memory, startup/restart, disk-full operation, and physical power-loss recovery.

CI configuration covers Linux/Python 3.11 and 3.13; remote CI has not been run from this workspace.
