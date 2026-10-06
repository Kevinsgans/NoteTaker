import hashlib
import importlib.metadata
import json
import logging
import platform
import shutil
import subprocess
import time
from pathlib import Path

import psutil

logger = logging.getLogger("notetaker")


def event(name, **fields):
    logger.info("[notetaker] %s", json.dumps({"event": name, **fields}, default=str))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resources():
    process = psutil.Process()
    cpu = process.cpu_times()
    temperature = None
    thermal = Path("/sys/class/thermal/thermal_zone0/temp")
    if thermal.exists():
        temperature = float(thermal.read_text()) / 1000
    return {
        "rss_bytes": process.memory_info().rss,
        "cpu_seconds": cpu.user + cpu.system,
        "temperature_c": temperature,
        "accelerator_memory_bytes": None,
    }


def provenance(config):
    packages = {}
    for name in ("notetaker", "numpy", "opencv-python-headless", "picamera2", "hailort"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    hardware = {}
    for key, command in (
        ("hailo_device", ["hailortcli", "fw-control", "identify"]),
        ("throttling", ["vcgencmd", "get_throttled"]),
    ):
        if shutil.which(command[0]):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                hardware[key] = {"returncode": result.returncode, "output": result.stdout.strip()}
            except (OSError, subprocess.TimeoutExpired):
                hardware[key] = {"error": "probe failed"}
    model_hash = None
    model_error = None
    if config.model_path:
        try:
            model_hash = sha256_file(config.model_path)
        except OSError as exc:
            model_error = type(exc).__name__
    return {
        "time": time.time(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "host_ram_bytes": psutil.virtual_memory().total,
        "packages": packages,
        "config_sha256": config.fingerprint(),
        "provider": config.provider,
        "model_sha256": model_hash,
        "model_file_error": model_error,
        "hardware": hardware,
    }
