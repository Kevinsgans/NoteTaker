import logging
import time
from pathlib import Path
from queue import Empty, Full

import cv2

from .storage import SQLiteRepository
from .telemetry import resources
from .vision import OpenCVProcessor, StabilityGate, warp


class ReplayCamera:
    def __init__(self, directory):
        self.files = sorted(
            p for p in Path(directory).glob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
        )
        if not self.files:
            raise ValueError("No replay images; run notetaker demo first or configure replay_dir")
        self.index = 0

    def preview(self):
        image = cv2.imread(str(self.files[self.index]))
        if image is None:
            raise ValueError("Cannot read replay image")
        return image

    def capture(self):
        return self.preview()

    def next(self):
        self.index = (self.index + 1) % len(self.files)

    def close(self):
        pass


class PiCamera:
    def __init__(self):
        from libcamera import controls
        from picamera2 import Picamera2

        self.camera = Picamera2()
        self.preview_config = self.camera.create_preview_configuration(
            main={"size": (1280, 960), "format": "RGB888"}
        )
        self.still_config = self.camera.create_still_configuration(
            main={"size": self.camera.sensor_resolution, "format": "RGB888"}
        )
        self.camera.configure(self.preview_config)
        self.camera.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        self.camera.start()

    def preview(self):
        # Picamera2 RGB888 memory order is BGR, matching OpenCV.
        return self.camera.capture_array("main")

    def capture(self):
        if not self.camera.autofocus_cycle():
            raise ValueError("Autofocus failed; reposition page or improve lighting")
        return self.camera.switch_mode_and_capture_array(self.still_config, "main")

    def close(self):
        self.camera.close()


def latest(queue, item):
    try:
        queue.put_nowait(item)
    except Full:
        try:
            queue.get_nowait()
        except Empty:
            return
        try:
            queue.put_nowait(item)
        except Full:
            pass


def camera_worker(config, commands, responses, statuses, preview_buffer, preview_length, stop):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    source = None
    repo = SQLiteRepository(config.root, config.provider != "disabled")
    processor = OpenCVProcessor(config)
    gate = StabilityGate(config.stable_seconds)
    course, automatic = config.course, config.auto_capture
    last_page = None
    errors = 0
    status = {"camera": "starting", "course": course, "auto_capture": automatic}
    try:
        while not stop.is_set():
            request = None
            try:
                if source is None:
                    source = (
                        PiCamera()
                        if config.camera == "picamera2"
                        else ReplayCamera(config.replay_dir)
                    )
                image = source.preview()
                ratio = min(1, config.preview_width / image.shape[1])
                preview = cv2.resize(image, None, fx=ratio, fy=ratio)
                corners = processor.detect(preview)
                quality = processor.quality(warp(preview, corners)) if corners is not None else None
                ready = gate.update(corners, quality is not None and quality["acceptable"], preview)
                ok, jpg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok and len(jpg) <= len(preview_buffer):
                    with preview_length.get_lock():
                        preview_buffer[: len(jpg)] = jpg.tobytes()
                        preview_length.value = len(jpg)
                try:
                    request = commands.get_nowait()
                except Empty:
                    pass
                if request and request["action"] == "settings":
                    course = request.get("course", course)
                    automatic = request.get("auto_capture", automatic)
                    responses.put({"request_id": request["request_id"], "ok": True}, timeout=1)
                if request and request["action"] == "next":
                    if isinstance(source, ReplayCamera):
                        source.next()
                        ready = False
                        gate = StabilityGate(config.stable_seconds)
                    responses.put({"request_id": request["request_id"], "ok": True}, timeout=1)
                capture = request is not None and request["action"] == "capture"
                if capture or (automatic and ready):
                    start = time.monotonic()
                    capture_started_at = time.time()
                    try:
                        still = source.capture()
                        capture_ms = (time.monotonic() - start) * 1000
                        result = processor.process(
                            still, force=capture and request.get("force", False)
                        )
                        page = repo.save(
                            still,
                            result["corrected"],
                            course,
                            {
                                "capture_ms": capture_ms,
                                "capture_started_at": capture_started_at,
                                "preprocess_ms": result["preprocess_ms"],
                                "quality": result["quality"],
                                **resources(),
                            },
                            corners=result["corners"],
                            event_id=request["request_id"] if capture else None,
                        )
                        status.pop("capture_error", None)
                        status["storage"] = "ready"
                        last_page = page["id"]
                        gate.captured(preview)
                        if capture:
                            responses.put(
                                {"request_id": request["request_id"], "page_id": page["id"]},
                                timeout=1,
                            )
                    except Exception as exc:
                        status["capture_error"] = type(exc).__name__ + ": " + str(exc)[:200]
                        if isinstance(exc, OSError):
                            status["storage"] = "error: " + str(exc)[:100]
                        gate.captured(preview)
                        if capture:
                            responses.put(
                                {
                                    "request_id": request["request_id"],
                                    "error": status["capture_error"],
                                },
                                timeout=1,
                            )
                status.pop("error", None)
                status.update(
                    {
                        "camera": "ready",
                        "course": course,
                        "auto_capture": automatic,
                        "corners": corners.tolist() if corners is not None else None,
                        "preview_size": [preview.shape[1], preview.shape[0]],
                        "quality": quality,
                        "latched": gate.latched,
                        "last_page": last_page,
                        "updated": time.time(),
                    }
                )
                latest(statuses, status.copy())
                errors = 0
                stop.wait(0.08)
            except Exception as exc:
                errors += 1
                status.update(
                    {
                        "camera": "error",
                        "error": type(exc).__name__ + ": " + str(exc)[:200],
                        "updated": time.time(),
                    }
                )
                latest(statuses, status.copy())
                if request:
                    latest(
                        responses, {"request_id": request["request_id"], "error": status["error"]}
                    )
                if source:
                    source.close()
                    source = None
                if errors >= 3:
                    return
                stop.wait(errors)
    finally:
        if source:
            source.close()
