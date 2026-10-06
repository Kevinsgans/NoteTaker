# Architecture and failure behavior

## Execution and data movement

The camera sensor and Pi ISP produce host-resident BGR images. OpenCV on the Pi CPU detects a quadrilateral, measures quality, and warps the full-resolution page. This is not Hailo-accelerated. Images are PNG files on the Pi filesystem; SQLite stores metadata, image paths, generations, metrics, and jobs.

The service starts one camera process and one lazy inference process. A supervisor thread claims one durable inference job at a time and passes its image path, not an image array, to the inference process. That process decodes and resizes on the host, supplies RGB model inputs to HailoRT, and validates the returned JSON on the host. HailoRT moves supported model work and data across PCIe to the Hailo-10H and its dedicated memory. The application does not assume every runtime operation is on the NPU. `runtime_inference_ms` includes runtime work and transfers; NPU-only and PCIe-only timings are unavailable and remain null.

A bounded shared JPEG buffer keeps one preview; status notifications are also bounded. Commands are bounded separately. Saved pages queue in SQLite, not in an unbounded in-memory image queue. The browser never talks directly to Hailo.

Camera states are searching → stable for 750 ms → capture → save → waiting for removal/change. Substantial scene changes must persist for 500 ms before rearming. Preview corner drift is measured against the stability anchor, so gradual movement cannot pass merely by moving slowly between frames. Full-resolution detection and quality are rerun before saving. Thresholds require calibration on the development split; glossy paper, curved pages, faint pencil, and low-contrast backgrounds are outside the controlled initial setup.

## Durability

1. Write and fsync original and corrected PNGs in a unique staging directory.
2. Write a manifest with checksums and fsync it and the directory.
3. Rename into `pages/` on the same filesystem; fsync both parent directories.
4. Commit the page, full-text entry, and job in one SQLite transaction (`WAL`, `synchronous=FULL`).
5. Record timing and acknowledge the save.

At startup, complete orphan directories are checksum-validated and registered idempotently. Incomplete or corrupt directories are reported and retained for inspection; they are never exposed as saved pages. Do not put staging and pages on different mounted filesystems. An interrupted post-commit acknowledgment may leave a valid saved page; API event IDs allow a client retry to resolve the same page. Similarity hashes flag possible duplicates rather than removing them. Detection compares the latest 500 saved pages; it is a convenience, not identity proof.

A new crop writes a new immutable derived image, then atomically changes the page pointer and job generation. Old images remain available on disk. Completion of an old generation cannot update a new crop. Explicit manual title/tag values, including empty values, always take precedence over model proposals. Inference never edits manual descriptions or course labels.

Retries are bounded: camera reconnect tries three times, interrupted running jobs are requeued up to three attempts, and the inference supervisor pauses after three consecutive failures until a user retries. A 60-second deadline terminates and replaces a hung model process. Camera health reports a stopped worker; restart the service after fixing the camera. Persisted pages remain usable. Use one Uvicorn worker and one service instance per data directory.

SQLite/fsync checks validate process-crash recovery. Actual power-loss durability depends on filesystem and storage-controller behavior; a real Pi power-cut test is still required.

## Local API

All routes, including images and static UI files, require HTTP Basic authentication. `notetaker setup` writes a salted PBKDF2 digest with owner-only permissions. Successful authentication proofs are cached briefly to avoid repeating expensive derivation for every preview request. Cross-origin writes are rejected. The interface uses text nodes for metadata, a same-origin content security policy, and no external assets.

| Method / route | Contract |
| --- | --- |
| GET `/api/status`, `/api/preview` | Worker health and latest JPEG |
| PUT `/api/settings` | `{course, auto_capture}`; session settings |
| POST `/api/capture` | `{force, event_id?}`; optional UUID for idempotent client retries; returns a durably saved page |
| GET `/api/pages?q=&course=&after=&before=` | Literal-token OR search ranked by FTS5 BM25; ISO date filters, latest 100 results |
| GET `/api/pages/{id}` | Images, effective/manual/proposed metadata, inference status and timings |
| GET `/api/pages/{id}/image/{original,corrected}` | Preserved or current corrected PNG |
| PUT `/api/pages/{id}/metadata` | `{title,tags,description,course}`; saves reviewed manual values |
| PUT `/api/pages/{id}/corners` | `{corners:[[x,y],...]}` in original image pixels; four convex corners |
| POST `/api/pages/{id}/retry` | Queue metadata again; rejects an already-running job |
| POST `/api/replay/next` | Advance development image input |

## Model contract

Start with the supported Qwen2-VL-2B-Instruct HEF through HailoRT's Python VLM API. The default 336-pixel input is letterboxed to preserve page boundaries rather than cropping away page edges. The experimental `tiles` strategy makes five independent calls: full page plus four overlapping 60%-size crops. It chooses the first nonempty title and unions tags in reading order, capped at five. All calls share the per-page 60-second deadline. This simple merge rule is fixed before comparison and may surface contradictory tags; score those as model errors.

Input is a saved page and course context. Output must be JSON with a title up to 160 characters and up to five nonempty tags, each at most 60 characters. Empty title/tags are allowed. Invalid output fails the job without changing indexed metadata. Context is cleared after each request. Do not send ordinary Ollama VLM requests: Hailo's Ollama layer does not imply VLM support.

References: [Hailo VLM example](https://github.com/hailo-ai/hailo-apps/blob/main/.github/instructions/gen-ai-development.md), [Hailo GenAI usage](https://github.com/hailo-ai/hailo_model_zoo_genai/blob/main/docs/USAGE.rst), [Pi camera software](https://www.raspberrypi.com/documentation/computers/camera_software.html).
