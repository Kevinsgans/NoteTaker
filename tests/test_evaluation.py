import json

import pytest

from notetaker.evaluation import load_manifest, percentiles, retrieval


def test_retrieval_comparison():
    rows = [
        {
            "id": "a",
            "course": "Math",
            "manual": {},
            "raw": {"title": "Eigenvectors"},
            "corrected": {"title": "Eigenvectors"},
        },
        {"id": "b", "course": "Math", "manual": {}, "raw": {"title": "Calculus"}},
    ]
    query = [{"query": "eigenvectors", "relevant_ids": ["a"]}]
    assert retrieval(rows, query, "manual")["recall_at_5"] == 0
    assert retrieval(rows, query, "raw")["recall_at_5"] == 1
    assert retrieval(rows, query, "corrected")["mrr"] == 1
    with pytest.raises(ValueError):
        retrieval(rows, [{"query": "x", "relevant_ids": ["missing"]}], "raw")


def test_reject_session_leakage(tmp_path):
    rows = [
        {
            "id": str(i),
            "image": "x.png",
            "session": "same",
            "page_group": str(i),
            "split": split,
            "category": "prose",
            "course": "Math",
        }
        for i, split in enumerate(["development", "heldout"])
    ]
    path = tmp_path / "manifest.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="leakage"):
        load_manifest(path)


def test_empty_measurement_is_not_zero():
    assert percentiles([]) == {"n": 0, "p50": None, "p95": None}


def test_benchmark_review_round_trip(tmp_path, page_image):
    import cv2

    from notetaker.config import Config
    from notetaker.evaluation import benchmark, score_review

    image = tmp_path / "page.png"
    cv2.imwrite(str(image), page_image)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "id": "one",
                "image": "page.png",
                "session": "a",
                "page_group": "a",
                "split": "heldout",
                "category": "prose",
                "course": "Math",
                "synthetic": True,
            }
        )
        + "\n"
    )
    queries = tmp_path / "queries.jsonl"
    queries.write_text(json.dumps({"query": "Eigenvectors", "relevant_ids": ["one"]}) + "\n")
    output = tmp_path / "run"
    report = benchmark(Config(provider="fake"), manifest, output, queries)
    assert report["synthetic"]
    assert report["retrieval"]["corrected"]["recall_at_5"] is None
    review = tmp_path / "review.jsonl"
    review.write_text(
        json.dumps(
            {
                "id": "one",
                "reviewed": True,
                "corrected": {"title": "Eigenvectors", "tags": []},
                "annotations": {"title_accepted": False},
            }
        )
        + "\n"
    )
    scored = score_review(manifest, output, review, queries, tmp_path / "scored.json")
    assert scored["retrieval"]["corrected"]["recall_at_5"] == 1
    assert scored["retrieval"]["raw"]["recall_at_5"] == 0
    manifest.write_text(manifest.read_text() + "\n")
    with pytest.raises(ValueError, match="Manifest changed"):
        score_review(manifest, output, review, queries, tmp_path / "other.json")
