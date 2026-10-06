import uuid

from fastapi.testclient import TestClient

from notetaker.app import create_app
from notetaker.auth import set_password
from notetaker.config import Config
from notetaker.storage import SQLiteRepository


class StubRuntime:
    def __init__(self, root):
        self.repo = SQLiteRepository(root)
        self.status = {"camera": "test"}

    def start(self):
        pass

    def close(self):
        pass


def test_auth_metadata_search_and_cross_origin(tmp_path, page_image):
    config = Config(data_dir=str(tmp_path))
    set_password(tmp_path / "auth.json", "test-password-long")
    runtime = StubRuntime(tmp_path)
    page = runtime.repo.save(page_image, page_image, "Math", {})
    with TestClient(create_app(config, runtime)) as client:
        assert client.get("/api/status").status_code == 401
        client.auth = ("notetaker", "test-password-long")
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/status").json()["camera"] == "test"
        body = {
            "title": "Eigenvectors",
            "tags": ["algebra"],
            "description": "notes",
            "course": "Math",
        }
        path = "/api/pages/" + page["id"] + "/metadata"
        assert (
            client.put(path, json=body, headers={"origin": "http://evil.test"}).status_code == 403
        )
        assert client.put(path, json=body).status_code == 200
        assert client.get("/api/pages?q=eigenvectors").json()[0]["id"] == page["id"]
        assert client.get("/api/pages/" + page["id"] + "/image/original").status_code == 200
        assert client.get("/api/pages/" + str(uuid.uuid4())).status_code == 404
        body["tags"] = ["a" * 61]
        assert client.put(path, json=body).status_code == 422
