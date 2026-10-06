import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: str = "data"
    camera: str = "replay"
    replay_dir: str = "evaluation/replay"
    provider: str = "disabled"
    model_path: str = ""
    model_size: int = 336
    strategy: str = "full"
    course: str = "Unsorted"
    auto_capture: bool = False
    stable_seconds: float = 0.75
    blur_min: float = 45.0
    brightness_min: float = 45.0
    brightness_max: float = 250.0
    inference_timeout: float = 60.0
    preview_width: int = 640
    port: int = 8000
    host: str = "127.0.0.1"

    @property
    def root(self) -> Path:
        return Path(self.data_dir).resolve()

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def load_config(path: str | Path) -> Config:
    with open(path, "rb") as f:
        config = Config(**tomllib.load(f))
    if config.camera not in {"replay", "picamera2"}:
        raise ValueError("camera must be replay or picamera2")
    if config.provider not in {"disabled", "fake", "hailo"}:
        raise ValueError("provider must be disabled, fake, or hailo")
    if config.strategy not in {"full", "tiles"}:
        raise ValueError("strategy must be full or tiles")
    if config.inference_timeout <= 0 or config.stable_seconds <= 0:
        raise ValueError("timeouts must be positive")
    if not 160 <= config.preview_width <= 1280 or not 32 <= config.model_size <= 2048:
        raise ValueError("unsupported preview or model size")
    if config.brightness_min >= config.brightness_max:
        raise ValueError("brightness_min must be below brightness_max")
    return config
