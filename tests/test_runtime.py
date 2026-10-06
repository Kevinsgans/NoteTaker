import time
from dataclasses import replace

import cv2

from notetaker.config import Config
from notetaker.inference import InferenceRunner
from notetaker.runtime import Runtime


def test_fake_inference_persistent_and_timeout(tmp_path, page_image):
    image = tmp_path / "page.png"
    cv2.imwrite(str(image), page_image)
    config = Config(provider="fake")
    runner = InferenceRunner(config)
    try:
        assert runner.run(image, "Math")["metrics"]["cold"]
        assert not runner.run(image, "Math")["metrics"]["cold"]
    finally:
        runner.close()
    runner = InferenceRunner(replace(config, inference_timeout=0.001))
    try:
        assert "timed out" in runner.run(image, "Math")["error"]
        assert runner.process is None
    finally:
        runner.close()


def test_replay_capture_and_background_inference(tmp_path, page_image):
    replay = tmp_path / "replay"
    replay.mkdir()
    cv2.imwrite(str(replay / "page.png"), page_image)
    runtime = Runtime(
        Config(data_dir=str(tmp_path / "data"), replay_dir=str(replay), provider="fake")
    )
    runtime.start()
    try:
        result = runtime.command("capture", timeout=15)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            page = runtime.repo.get(result["page_id"])
            if page["inference_status"] == "completed":
                break
            time.sleep(0.1)
        assert page["inference_status"] == "completed"
        assert page["effective_title"] == "Synthetic demo page"
        assert runtime.preview().startswith(b"\xff\xd8")
        assert page["metrics"]["capture_to_save_ms"] > 0
    finally:
        runtime.close()


def test_missing_camera_fails_visibly(tmp_path):
    runtime = Runtime(Config(data_dir=str(tmp_path / "data"), replay_dir=str(tmp_path / "missing")))
    runtime.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and runtime.status["camera"] == "starting":
            time.sleep(0.1)
        assert runtime.status["camera"] == "error"
        assert "No replay images" in runtime.status["error"]
    finally:
        runtime.close()


def hung_worker(config, requests, responses):
    requests.get()
    time.sleep(30)


def test_hung_native_call_is_terminated(tmp_path, page_image, monkeypatch):
    import notetaker.inference as inference

    monkeypatch.setattr(inference, "inference_worker", hung_worker)
    runner = InferenceRunner(Config(provider="fake", inference_timeout=0.4))
    start = time.monotonic()
    try:
        assert runner.run(tmp_path / "unused.png", "Math")["error"] == "Inference timed out"
        assert time.monotonic() - start < 4
        assert runner.process is None
    finally:
        runner.close()


def test_missing_model_does_not_block_capture(tmp_path, page_image):
    replay = tmp_path / "replay"
    replay.mkdir()
    cv2.imwrite(str(replay / "page.png"), page_image)
    runtime = Runtime(
        Config(
            data_dir=str(tmp_path / "data"),
            replay_dir=str(replay),
            provider="hailo",
            model_path=str(tmp_path / "missing.hef"),
        )
    )
    runtime.start()
    try:
        result = runtime.command("capture", timeout=15)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            page = runtime.repo.get(result["page_id"])
            if page["inference_status"] == "failed":
                break
            time.sleep(0.1)
        assert page["inference_status"] == "failed"
        assert len(runtime.repo.search()) == 1
    finally:
        runtime.close()


def test_automatic_capture_latches(tmp_path, page_image):
    replay = tmp_path / "replay"
    replay.mkdir()
    cv2.imwrite(str(replay / "page.png"), page_image)
    runtime = Runtime(
        Config(data_dir=str(tmp_path / "data"), replay_dir=str(replay), auto_capture=True)
    )
    runtime.start()
    try:
        deadline = time.monotonic() + 5
        while not runtime.repo.search() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert len(runtime.repo.search()) == 1
        time.sleep(1)
        assert len(runtime.repo.search()) == 1
    finally:
        runtime.close()
