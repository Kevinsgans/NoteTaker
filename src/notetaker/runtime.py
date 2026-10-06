import json
import multiprocessing as mp
import threading
import time
import uuid
from queue import Empty

from .camera import camera_worker
from .inference import InferenceRunner
from .models import Metadata
from .storage import SQLiteRepository
from .telemetry import event, provenance


class Runtime:
    def __init__(self, config):
        self.config = config
        self.repo = SQLiteRepository(config.root, config.provider != "disabled")
        self.ctx = mp.get_context("spawn")
        self.stop = self.ctx.Event()
        self.commands = self.ctx.Queue(8)
        self.responses = self.ctx.Queue(8)
        self.statuses = self.ctx.Queue(1)
        self.preview_buffer = self.ctx.Array("B", 1024 * 1024, lock=False)
        self.preview_length = self.ctx.Value("i", 0)
        self.status = {
            "camera": "starting",
            "camera_source": config.camera,
            "model": config.provider,
            "storage": "starting",
        }
        self.results = {}
        self.condition = threading.Condition()
        self.command_lock = threading.Lock()
        self.runner = InferenceRunner(config)
        self.camera = None
        self.threads = []

    def start(self):
        self.status["recovery"] = self.repo.recover()
        self.status["storage"] = "ready"
        (self.config.root / "provenance.json").write_text(
            json.dumps(provenance(self.config), indent=2)
        )
        self.camera = self.ctx.Process(
            target=camera_worker,
            args=(
                self.config,
                self.commands,
                self.responses,
                self.statuses,
                self.preview_buffer,
                self.preview_length,
                self.stop,
            ),
            daemon=True,
        )
        self.camera.start()
        for target in (self._collect, self._inference):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self.threads.append(thread)

    def _collect(self):
        while not self.stop.is_set():
            try:
                self.status.update(self.statuses.get(timeout=0.1))
            except Empty:
                pass
            if not self.camera.is_alive():
                self.status["camera"] = "stopped after bounded retries; restart service"
            try:
                while True:
                    response = self.responses.get_nowait()
                    with self.condition:
                        self.results[response["request_id"]] = response
                        if len(self.results) > 100:
                            self.results.pop(next(iter(self.results)))
                        self.condition.notify_all()
            except Empty:
                pass

    def _inference(self):
        if self.config.provider == "disabled":
            return
        consecutive_errors = 0
        while not self.stop.is_set():
            if consecutive_errors >= 3:
                self.status["model"] = "paused after three failures; retry a page to resume"
                self.stop.wait(0.25)
                if self.status.pop("resume_model", False):
                    consecutive_errors = 0
                continue
            job = None
            try:
                job = self.repo.claim()
                if job is None:
                    self.stop.wait(0.2)
                    continue
                page = self.repo.get(job["page_id"])
                self.status["model"] = "running"
                start = time.monotonic()
                result = self.runner.run(
                    self.config.root / page["corrected"], page["course"], self.stop
                )
                if "error" in result:
                    consecutive_errors += 1
                    self.repo.finish(job, error=result["error"])
                    self.status["model"] = result["error"]
                    event("inference_failed", page_id=job["page_id"], error=result["error"])
                else:
                    metrics = result["metrics"]
                    metrics.update(
                        {
                            "queue_ms": job["queue_ms"],
                            "inference_wall_ms": (time.monotonic() - start) * 1000,
                            "generation": job["generation"],
                            "attempt": job["attempts"] + 1,
                        }
                    )
                    committed = self.repo.finish(job, Metadata(**result["metadata"]), metrics)
                    if committed:
                        metrics["inference_to_index_ms"] = (time.monotonic() - start) * 1000
                        captured = page["metrics"].get("capture_started_at")
                        if captured and job["generation"] == 0 and job["attempts"] == 0:
                            metrics["capture_to_searchable_ms"] = (time.time() - captured) * 1000
                        with self.repo.connection() as db:
                            db.execute(
                                "UPDATE pages SET inference_metrics=? WHERE id=? AND generation=?",
                                (json.dumps(metrics), job["page_id"], job["generation"]),
                            )
                        event("inference_completed", page_id=job["page_id"], **metrics)
                    else:
                        event("inference_superseded", page_id=job["page_id"])
                    consecutive_errors = 0
                    self.status["model"] = "ready"
            except Exception as exc:
                self.status["storage"] = "error: " + type(exc).__name__
                event("worker_error", error=type(exc).__name__)
                if job is not None:
                    try:
                        self.repo.finish(job, error="Supervisor failure: " + type(exc).__name__)
                    except Exception:
                        pass  # Recovery reclaims the running job after storage becomes available.
                consecutive_errors += 1
                self.stop.wait(1)

    def command(self, action, timeout=30, **fields):
        with self.command_lock:
            return self._command(action, timeout, **fields)

    def _command(self, action, timeout=30, **fields):
        request_id = fields.pop("request_id", None) or str(uuid.uuid4())
        self.commands.put({"action": action, "request_id": request_id, **fields}, timeout=1)
        deadline = time.monotonic() + timeout
        with self.condition:
            while request_id not in self.results:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "Camera request timed out; check recent pages before retrying"
                    )
                self.condition.wait(remaining)
            result = self.results.pop(request_id)
        if "error" in result:
            raise ValueError(result["error"])
        return result

    def preview(self):
        with self.preview_length.get_lock():
            return bytes(self.preview_buffer[: self.preview_length.value])

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=4)
        self.runner.close()
        if self.camera:
            self.camera.join(timeout=3)
            if self.camera.is_alive():
                self.camera.terminate()
                self.camera.join(timeout=2)
            if self.camera.is_alive():
                self.camera.kill()
                self.camera.join(timeout=2)
        for queue in (self.commands, self.responses, self.statuses):
            queue.cancel_join_thread()
            queue.close()
        if not any(thread.is_alive() for thread in self.threads):
            if self.camera:
                self.camera.close()
            self.camera = self.commands = self.responses = self.statuses = None
            self.preview_buffer = self.preview_length = self.stop = None
            self.threads.clear()
