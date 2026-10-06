from pathlib import Path
from typing import Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="", max_length=160)
    tags: list[str] = Field(default_factory=list, max_length=5)

    def checked(self):
        if any(not tag or len(tag) > 60 for tag in self.tags):
            raise ValueError("tags must contain 1–60 characters")
        self.tags = list(dict.fromkeys(self.tags))
        return self


class CameraSource(Protocol):
    def preview(self) -> np.ndarray: ...
    def capture(self) -> np.ndarray: ...
    def close(self) -> None: ...


class PageProcessor(Protocol):
    def detect(self, image: np.ndarray) -> np.ndarray | None: ...
    def process(self, image: np.ndarray, corners=None, force=False) -> dict: ...


class MetadataProvider(Protocol):
    def generate(self, path: Path, course: str) -> tuple[Metadata, dict]: ...
    def close(self) -> None: ...


class PageRepository(Protocol):
    def save(self, original, corrected, course: str, metrics: dict, **kwargs) -> dict: ...
    def get(self, page_id: str) -> dict: ...
    def search(self, query: str = "", **kwargs) -> list[dict]: ...
