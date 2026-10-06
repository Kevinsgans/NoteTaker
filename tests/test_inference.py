import json
import sys
import types
from contextlib import contextmanager

import cv2
import pytest

from notetaker.config import Config
from notetaker.inference import HailoProvider


class Device:
    def release(self):
        pass


class Model:
    result = '{"title":"Eigenvectors","tags":["linear algebra"]}'
    clears = 0

    def __init__(self, device, path):
        pass

    @contextmanager
    def generate(self, **kwargs):
        assert kwargs["frames"][0].shape == (336, 336, 3)
        yield [self.result]

    def clear_context(self):
        Model.clears += 1

    def release(self):
        pass


@pytest.fixture
def fake_hailo(monkeypatch):
    monkeypatch.setitem(sys.modules, "hailo_platform", types.SimpleNamespace(VDevice=Device))
    monkeypatch.setitem(sys.modules, "hailo_platform.genai", types.SimpleNamespace(VLM=Model))
    Model.clears = 0


def test_hailo_adapter_calls_and_validates(fake_hailo, tmp_path, page_image):
    path = tmp_path / "page.png"
    cv2.imwrite(str(path), page_image)
    provider = HailoProvider(Config(strategy="tiles"))
    try:
        metadata, metrics = provider.generate(path, "Math")
        assert metadata.title == "Eigenvectors"
        assert metrics["views"] == 5
        assert metrics["npu_only_ms"] is None
        assert Model.clears == 5
    finally:
        provider.close()


@pytest.mark.parametrize(
    "output",
    ["not JSON", '{"title":"x","tags":["" ]}', json.dumps({"title": "x", "tags": ["a"] * 6})],
)
def test_malformed_output_clears_context(fake_hailo, tmp_path, page_image, monkeypatch, output):
    monkeypatch.setattr(Model, "result", output)
    path = tmp_path / "page.png"
    cv2.imwrite(str(path), page_image)
    provider = HailoProvider(Config())
    try:
        with pytest.raises(ValueError):
            provider.generate(path, "Math")
        assert Model.clears == 1
    finally:
        provider.close()
