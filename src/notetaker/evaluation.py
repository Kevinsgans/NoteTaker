import json
import sqlite3
import time
from pathlib import Path

import cv2
import numpy as np

from .inference import InferenceRunner
from .storage import fts_query
from .telemetry import event, provenance, sha256_file
from .vision import OpenCVProcessor, ordered


def load_manifest(path):
    path = Path(path).resolve()
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ids, sessions, pages = set(), {}, {}
    for row in rows:
        for field in ("id", "image", "session", "page_group", "split", "category", "course"):
            if field not in row:
                raise ValueError(f"Missing {field} in manifest")
        if row["id"] in ids:
            raise ValueError("Duplicate sample ID")
        ids.add(row["id"])
        if row["split"] not in {"development", "heldout"}:
            raise ValueError("split must be development or heldout")
        if row["category"] not in {"prose", "equations", "diagrams"}:
            raise ValueError("category must be prose, equations or diagrams")
        for mapping, key in ((sessions, row["session"]), (pages, row["page_group"])):
            if key in mapping and mapping[key] != row["split"]:
                raise ValueError("Data leakage: session or physical page spans splits")
            mapping[key] = row["split"]
        row["image"] = str((path.parent / row["image"]).resolve())
    return rows


def percentiles(values):
    return {
        "n": len(values),
        "p50": float(np.percentile(values, 50)) if values else None,
        "p95": float(np.percentile(values, 95)) if values else None,
    }


def retrieval(rows, queries, mode):
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE VIRTUAL TABLE idx USING fts5(id UNINDEXED,title,tags,description,course,tokenize='unicode61')"
    )
    for row in rows:
        manual = row.get("manual", {})
        extra = {} if mode == "manual" else row.get("raw" if mode == "raw" else "corrected", {})
        db.execute(
            "INSERT INTO idx VALUES(?,?,?,?,?)",
            (
                row["id"],
                " ".join(filter(None, [manual.get("title", ""), extra.get("title", "")])),
                " ".join(dict.fromkeys(manual.get("tags", []) + extra.get("tags", []))),
                manual.get("description", ""),
                row["course"],
            ),
        )
    results = []
    for query in queries:
        terms = fts_query(query["query"])
        sql = "SELECT id FROM idx WHERE idx MATCH ?"
        args = [terms]
        if query.get("course"):
            sql += " AND course=?"
            args.append(query["course"])
        sql += " ORDER BY bm25(idx,0,4,3,1,1),id"
        ranked = [r[0] for r in db.execute(sql, args)] if terms else []
        relevant = set(query["relevant_ids"])
        if not relevant or not relevant <= {r["id"] for r in rows}:
            raise ValueError(
                "Queries must reference nonempty relevant IDs within the evaluated split"
            )
        first = next((i + 1 for i, page_id in enumerate(ranked) if page_id in relevant), None)
        results.append(
            {
                "query": query["query"],
                "top5": ranked[:5],
                "recall_at_5": len(relevant.intersection(ranked[:5])) / len(relevant),
                "reciprocal_rank": 1 / first if first else 0,
            }
        )
    db.close()
    return {
        "queries": results,
        "recall_at_5": float(np.mean([r["recall_at_5"] for r in results])) if results else None,
        "mrr": float(np.mean([r["reciprocal_rank"] for r in results])) if results else None,
    }


def benchmark(config, manifest, output, query_path=None, split="heldout"):
    # Read all splits before filtering, so accidental session leakage is detected.
    all_rows = load_manifest(manifest)
    rows = [row for row in all_rows if row["split"] == split]
    if not rows:
        raise ValueError("No samples in selected split")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "report.json").exists() or (output / "raw.jsonl").exists():
        raise ValueError("Use a fresh output directory to preserve previous measurements")
    processor = OpenCVProcessor(config)
    runner = InferenceRunner(config) if config.provider != "disabled" else None
    measured, records = [], []
    try:
        with open(output / "raw.jsonl", "x") as raw:
            for row in rows:
                record = {
                    "id": row["id"],
                    "category": row["category"],
                    "accepted": False,
                    "image_sha256": sha256_file(row["image"]),
                }
                evaluated = {**row, "raw": {}}
                start = time.monotonic()
                image = cv2.imread(row["image"])
                if image is None:
                    raise ValueError("Cannot decode " + row["image"])
                try:
                    processed = processor.process(image)
                    record.update(
                        {
                            "accepted": True,
                            "preprocess_ms": processed["preprocess_ms"],
                            "corners": processed["corners"],
                            "quality": processed["quality"],
                        }
                    )
                    if row.get("corners"):
                        predicted, truth = ordered(processed["corners"]), ordered(row["corners"])
                        intersection, _ = cv2.intersectConvexConvex(predicted, truth)
                        union = cv2.contourArea(predicted) + cv2.contourArea(truth) - intersection
                        record["crop_iou"] = float(intersection / union)
                        record["corner_mean_error_pixels"] = float(
                            np.linalg.norm(predicted - truth, axis=1).mean()
                        )
                    path = output / f"{len(records):04d}.png"
                    if not cv2.imwrite(str(path), processed["corrected"]):
                        raise OSError("Cannot write benchmark image")
                    if runner:
                        result = runner.run(path, row["course"])
                        record["inference"] = result
                        if "metadata" in result:
                            evaluated["raw"] = result["metadata"]
                except ValueError as exc:
                    record["capture_error"] = str(exc)
                record["replay_total_ms"] = (time.monotonic() - start) * 1000
                record["annotations"] = row.get("annotations", {})
                records.append(record)
                measured.append(evaluated)
                raw.write(json.dumps(record) + "\n")
                raw.flush()
                event("benchmark_sample", **record)
    finally:
        if runner:
            runner.close()
    queries = []
    if query_path:
        queries = [
            json.loads(line) for line in Path(query_path).read_text().splitlines() if line.strip()
        ]
    annotation_rows = [r["annotations"] for r in records if r["annotations"]]

    def mean_annotation(field):
        values = [a[field] for a in annotation_rows if field in a]
        return {"n": len(values), "mean": float(np.mean(values)) if values else None}

    tag_total = sum(a.get("tag_count", 0) for a in annotation_rows)
    report = {
        "provenance": provenance(config),
        "manifest_sha256": sha256_file(manifest),
        "queries_sha256": sha256_file(query_path) if query_path else None,
        "scope": "Recorded-image benchmark; not live camera latency or coursework accuracy without annotations",
        "synthetic": config.provider == "fake" or any(r.get("synthetic", False) for r in rows),
        "split": split,
        "samples": len(rows),
        "physical_pages": len({r["page_group"] for r in rows}),
        "categories": {
            c: sum(r["category"] == c for r in rows) for c in ("prose", "equations", "diagrams")
        },
        "capture_acceptance_rate": sum(r["accepted"] for r in records) / len(records),
        "crop_iou": percentiles([r["crop_iou"] for r in records if "crop_iou" in r]),
        "preprocess_ms": percentiles([r["preprocess_ms"] for r in records if "preprocess_ms" in r]),
        "replay_total_ms": percentiles([r["replay_total_ms"] for r in records]),
        "worker_total_ms": percentiles(
            [
                r["inference"]["metrics"]["worker_total_ms"]
                for r in records
                if "metrics" in r.get("inference", {})
            ]
        ),
        "human_review": {
            f: mean_annotation(f)
            for f in (
                "legible",
                "clipped_content",
                "first_attempt_success",
                "title_accepted",
                "correction_seconds",
                "unsupported_tag_count",
            )
        },
        "tag_precision": sum(a.get("correct_tag_count", 0) for a in annotation_rows) / tag_total
        if tag_total
        else None,
        "retrieval": {
            mode: retrieval(measured, queries, mode) for mode in ("manual", "raw", "corrected")
        },
    }
    report["review_complete"] = all("corrected" in r for r in rows)
    if not report["review_complete"]:
        report["retrieval"]["corrected"] = {
            "queries": [],
            "recall_at_5": None,
            "mrr": None,
            "status": "Human review required",
        }
    raw_recall = report["retrieval"]["raw"]["recall_at_5"]
    base_recall = report["retrieval"]["manual"]["recall_at_5"]
    report["raw_recall_gain_percentage_points"] = (
        (raw_recall - base_recall) * 100 if raw_recall is not None else None
    )
    (output / "report.json").write_text(json.dumps(report, indent=2))
    (output / "metadata-review.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "id": r["id"],
                    "raw": r["raw"],
                    "corrected": r.get("corrected", r["raw"]),
                    "reviewed": False,
                    "annotations": r.get("annotations", {}),
                }
            )
            + "\n"
            for r in measured
        )
    )
    event("benchmark_report", output=str(output), samples=len(rows), synthetic=report["synthetic"])
    return report


def summarize_log(path, output):
    rows = []
    for line in Path(path).read_text().splitlines():
        if "[notetaker] " in line:
            try:
                rows.append(json.loads(line.split("[notetaker] ", 1)[1]))
            except json.JSONDecodeError:
                pass
    fields = (
        "capture_to_save_ms",
        "capture_to_searchable_ms",
        "capture_ms",
        "preprocess_ms",
        "storage_ms",
        "queue_ms",
        "inference_wall_ms",
        "sampled_peak_worker_rss_bytes",
    )
    result = {
        field: percentiles([row[field] for row in rows if isinstance(row.get(field), (int, float))])
        for field in fields
    }
    result["inference_by_load_state"] = {
        state: {
            field: percentiles(
                [
                    row[field]
                    for row in rows
                    if row.get("cold") is cold and isinstance(row.get(field), (int, float))
                ]
            )
            for field in ("model_load_ms", "inference_wall_ms", "capture_to_searchable_ms")
        }
        for state, cold in (("cold", True), ("warm", False))
    }
    Path(output).write_text(json.dumps(result, indent=2))
    return result


def score_review(manifest, benchmark_dir, reviews, queries, output):
    """Score human review against frozen model output without rerunning inference."""
    root = Path(benchmark_dir)
    report = json.loads((root / "report.json").read_text())
    if sha256_file(manifest) != report["manifest_sha256"]:
        raise ValueError("Manifest changed since benchmark; keep the original frozen manifest")
    if report["queries_sha256"] and sha256_file(queries) != report["queries_sha256"]:
        raise ValueError("Queries changed since benchmark")
    rows = [r for r in load_manifest(manifest) if r["split"] == report["split"]]
    raw = {
        r["id"]: r
        for r in [json.loads(line) for line in (root / "raw.jsonl").read_text().splitlines()]
    }
    annotations = [
        json.loads(line) for line in Path(reviews).read_text().splitlines() if line.strip()
    ]
    reviewed = {r["id"]: r for r in annotations}
    if len(reviewed) != len(annotations) or set(reviewed) != {r["id"] for r in rows}:
        raise ValueError("Review must cover each evaluated sample exactly once")
    from .models import Metadata

    for row in rows:
        review = reviewed[row["id"]]
        if not review.get("reviewed"):
            raise ValueError("Set reviewed=true only after human assessment")
        row["raw"] = raw[row["id"]].get("inference", {}).get("metadata", {})
        row["corrected"] = Metadata(**review["corrected"]).checked().model_dump()
        row["annotations"] = review.get("annotations", {})
        a = row["annotations"]
        for key in ("legible", "clipped_content", "first_attempt_success", "title_accepted"):
            if key in a and not isinstance(a[key], bool):
                raise ValueError(f"{key} must be boolean")
        if not 0 <= a.get("correct_tag_count", 0) <= a.get("tag_count", 0) <= 5:
            raise ValueError("Invalid tag counts")
        if a.get("correction_seconds", 0) < 0:
            raise ValueError("correction_seconds cannot be negative")
    query_rows = [
        json.loads(line) for line in Path(queries).read_text().splitlines() if line.strip()
    ]
    report["retrieval"] = {
        mode: retrieval(rows, query_rows, mode) for mode in ("manual", "raw", "corrected")
    }
    report["review_sha256"] = sha256_file(reviews)
    report["queries_sha256"] = sha256_file(queries)
    report["review_complete"] = True
    report["human_review"] = {}
    for field in (
        "legible",
        "clipped_content",
        "first_attempt_success",
        "title_accepted",
        "correction_seconds",
        "unsupported_tag_count",
    ):
        values = [r["annotations"][field] for r in rows if field in r["annotations"]]
        report["human_review"][field] = {
            "n": len(values),
            "mean": float(np.mean(values)) if values else None,
        }
    total = sum(r["annotations"].get("tag_count", 0) for r in rows)
    report["tag_precision"] = (
        sum(r["annotations"].get("correct_tag_count", 0) for r in rows) / total if total else None
    )
    raw_recall = report["retrieval"]["raw"]["recall_at_5"]
    baseline = report["retrieval"]["manual"]["recall_at_5"]
    report["raw_recall_gain_percentage_points"] = (
        (raw_recall - baseline) * 100 if raw_recall is not None else None
    )
    with open(output, "x") as f:
        json.dump(report, f, indent=2)
    event("review_scored", output=str(output))
    return report
