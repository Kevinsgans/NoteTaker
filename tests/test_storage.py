import multiprocessing as mp
import os

import pytest

from notetaker.models import Metadata
from notetaker.storage import SQLiteRepository


def test_duplicate_event_and_similar_page_not_discarded(tmp_path, page_image):
    repo = SQLiteRepository(tmp_path)
    page = repo.save(page_image, page_image, "Math", {}, event_id="same")
    assert repo.save(page_image, page_image, "Math", {}, event_id="same")["id"] == page["id"]
    second = repo.save(page_image, page_image, "Math", {})
    assert second["id"] != page["id"]
    assert second["duplicate_of"] == page["id"]
    assert len(repo.search()) == 2


def test_late_model_does_not_overwrite_user_edits(tmp_path, page_image):
    repo = SQLiteRepository(tmp_path)
    page = repo.save(page_image, page_image, "Math", {})
    job = repo.claim()
    repo.edit(page["id"], "", [], "My eigenvector notes", "Math")
    assert repo.finish(job, Metadata(title="Incorrect title", tags=["wrong"]))
    result = repo.get(page["id"])
    assert result["effective_title"] == ""
    assert result["effective_tags"] == []
    assert result["proposed_title"] == "Incorrect title"
    assert repo.search("eigenvector")[0]["id"] == page["id"]
    assert repo.search("wrong") == []


def test_failure_retains_page_and_retry(tmp_path, page_image):
    repo = SQLiteRepository(tmp_path)
    page = repo.save(page_image, page_image, "Math", {})
    repo.finish(repo.claim(), error="timeout")
    assert len(repo.search()) == 1
    assert repo.get(page["id"])["inference_status"] == "failed"
    repo.retry(page["id"])
    assert repo.claim()["page_id"] == page["id"]


def test_recrop_invalidates_running_job_and_preserves_original(tmp_path, page_image):
    repo = SQLiteRepository(tmp_path)
    page = repo.save(page_image, page_image, "Math", {})
    before = (tmp_path / page["original"]).read_bytes()
    old_job = repo.claim()
    updated = repo.recrop(page["id"], [[120, 40], [690, 40], [690, 560], [120, 560]])
    assert not repo.finish(old_job, Metadata(title="stale"))
    assert repo.get(page["id"])["proposed_title"] == ""
    assert updated["corrected"] != page["corrected"]
    assert (tmp_path / page["original"]).read_bytes() == before
    assert repo.claim()["generation"] == 1


def crash_save(root, image, stage):
    repo = SQLiteRepository(root)

    def fault(current):
        if current == stage:
            os._exit(77)

    repo.save(image, image, "Math", {}, fault=fault)


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("original.png", 0),
        ("corrected.png", 0),
        ("manifest", 1),
        ("rename", 1),
        ("transaction", 1),
        ("commit", 1),
    ],
)
def test_killed_save_recovery(tmp_path, page_image, stage, expected):
    process = mp.get_context("spawn").Process(target=crash_save, args=(tmp_path, page_image, stage))
    process.start()
    process.join(10)
    assert process.exitcode == 77
    repo = SQLiteRepository(tmp_path)
    repo.recover()
    assert len(repo.search()) == expected
    repo.recover()
    assert len(repo.search()) == expected


def test_corrupt_orphan_rejected(tmp_path, page_image):
    repo = SQLiteRepository(tmp_path)

    def fail(stage):
        if stage == "rename":
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError):
        repo.save(page_image, page_image, "Math", {}, fault=fail)
    path = next((tmp_path / "pages").glob("*/original.png"))
    path.write_bytes(b"corrupt")
    assert repo.recover()["incomplete"]
    assert repo.search() == []


def test_disk_full_does_not_acknowledge(tmp_path, page_image, monkeypatch):
    repo = SQLiteRepository(tmp_path)

    def full(*args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("notetaker.storage.durable_write", full)
    with pytest.raises(OSError):
        repo.save(page_image, page_image, "Math", {})
    assert repo.search() == []


def test_search_literals_filters_and_restart(tmp_path, page_image):
    repo = SQLiteRepository(tmp_path)
    page = repo.save(page_image, page_image, "Math", {})
    job = repo.claim()
    repo.finish(job, Metadata(title="Eigenvectors", tags=["linear algebra"]))
    assert repo.search("eigenvectors", course="Physics") == []
    assert repo.search('" OR * (') == []
    assert len(repo.search("eigenvectors", course="Math", after="2020-01-01")) == 1
    assert repo.search("eigenvectors", before="2000-01-01") == []
    repo.retry(page["id"])
    repo.claim()
    repo.recover()
    assert repo.get(page["id"])["inference_status"] == "queued"
