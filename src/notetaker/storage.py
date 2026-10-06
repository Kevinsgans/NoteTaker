import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import cv2

from .models import Metadata
from .telemetry import event
from .vision import fingerprint, warp


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_write(path, data):
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def encode(image):
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise OSError("image encoding failed")
    return encoded.tobytes()


def fts_query(query):
    # Treat user text as literal tokens, never as FTS operators.
    return " OR ".join('"' + word + '"' for word in re.findall(r"\w+", query, re.UNICODE)[:32])


class SQLiteRepository:
    def __init__(self, root, inference_enabled=True):
        self.root = Path(root)
        self.inference_enabled = inference_enabled
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("pages", "staging"):
            (self.root / name).mkdir(exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS pages (
                    id TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, created TEXT NOT NULL,
                    course TEXT NOT NULL, original TEXT NOT NULL, corrected TEXT NOT NULL,
                    corners TEXT NOT NULL, fingerprint TEXT NOT NULL, duplicate_of TEXT,
                    title TEXT, tags TEXT, description TEXT NOT NULL DEFAULT '',
                    proposed_title TEXT NOT NULL DEFAULT '', proposed_tags TEXT NOT NULL DEFAULT '[]',
                    reviewed INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 0,
                    metrics TEXT NOT NULL, inference_metrics TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    page_id TEXT PRIMARY KEY REFERENCES pages(id), generation INTEGER NOT NULL,
                    status TEXT NOT NULL, queued REAL NOT NULL, started REAL,
                    finished REAL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                    page_id UNINDEXED, title, tags, description, course,
                    tokenize='unicode61'
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.root / "notes.sqlite3", timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _index(self, db, page_id):
        p = db.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
        db.execute("DELETE FROM search_index WHERE page_id=?", (page_id,))
        db.execute(
            "INSERT INTO search_index VALUES(?,?,?,?,?)",
            (
                page_id,
                p["title"] if p["title"] is not None else p["proposed_title"],
                " ".join(json.loads(p["tags"] if p["tags"] is not None else p["proposed_tags"])),
                p["description"],
                p["course"],
            ),
        )

    def _register(self, db, manifest):
        m = manifest
        db.execute(
            """INSERT OR IGNORE INTO pages
            (id,event_id,created,course,original,corrected,corners,fingerprint,duplicate_of,metrics)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                m["id"],
                m["event_id"],
                m["created"],
                m["course"],
                m["original"],
                m["corrected"],
                json.dumps(m["corners"]),
                m["fingerprint"],
                m["duplicate_of"],
                json.dumps(m["metrics"]),
            ),
        )
        exists = db.execute("SELECT id FROM pages WHERE event_id=?", (m["event_id"],)).fetchone()
        if exists["id"] != m["id"]:
            return exists["id"]
        db.execute(
            "INSERT OR IGNORE INTO jobs(page_id,generation,status,queued) VALUES(?,0,?,?)",
            (m["id"], "queued" if self.inference_enabled else "disabled", time.time()),
        )
        self._index(db, m["id"])
        return m["id"]

    def save(self, original, corrected, course, metrics, corners=None, event_id=None, fault=None):
        start = time.monotonic()
        event_id = event_id or str(uuid.uuid4())
        with self.connection() as db:
            previous = db.execute("SELECT id FROM pages WHERE event_id=?", (event_id,)).fetchone()
            if previous:
                return self.get(previous["id"])
            fp = fingerprint(corrected)
            duplicate = next(
                (
                    r["id"]
                    for r in db.execute(
                        "SELECT id,fingerprint FROM pages ORDER BY created DESC LIMIT 500"
                    )
                    if sum(a != b for a, b in zip(fp, r["fingerprint"])) <= 5
                ),
                None,
            )
        page_id = str(uuid.uuid4())
        relative = Path("pages") / page_id
        staging = self.root / "staging" / page_id
        staging.mkdir()
        manifest = {
            "id": page_id,
            "event_id": event_id,
            "created": datetime.now(timezone.utc).isoformat(),
            "course": course,
            "original": str(relative / "original.png"),
            "corrected": str(relative / "corrected.png"),
            "corners": corners or [],
            "fingerprint": fp,
            "duplicate_of": duplicate,
            "metrics": metrics.copy(),
            "checksums": {},
        }
        for name, image in (("original.png", original), ("corrected.png", corrected)):
            data = encode(image)
            durable_write(staging / name, data)
            manifest["checksums"][name] = hashlib.sha256(data).hexdigest()
            if fault:
                fault(name)
        durable_write(staging / "manifest.json", json.dumps(manifest).encode())
        sync_dir(staging)
        if fault:
            fault("manifest")
        os.rename(staging, self.root / relative)
        sync_dir(self.root / "pages")
        sync_dir(self.root / "staging")
        if fault:
            fault("rename")
        with self.connection() as db:
            registered = self._register(db, manifest)
            if fault:
                fault("transaction")
        if fault:
            fault("commit")
        elapsed = (time.monotonic() - start) * 1000
        metrics = {
            **metrics,
            "storage_ms": elapsed,
            "capture_to_save_ms": metrics.get("capture_ms", 0)
            + metrics.get("preprocess_ms", 0)
            + elapsed,
        }
        with self.connection() as db:
            db.execute("UPDATE pages SET metrics=? WHERE id=?", (json.dumps(metrics), registered))
        event("saved", page_id=registered, **metrics)
        return self.get(registered)

    def recover(self):
        recovered, incomplete = [], []
        for parent in ("staging", "pages"):
            for directory in (self.root / parent).iterdir():
                if not directory.is_dir():
                    continue
                with self.connection() as db:
                    if db.execute("SELECT 1 FROM pages WHERE id=?", (directory.name,)).fetchone():
                        continue
                try:
                    manifest = json.loads((directory / "manifest.json").read_text())
                    if (
                        manifest["id"] != directory.name
                        or str(uuid.UUID(directory.name)) != directory.name
                    ):
                        raise ValueError("invalid manifest identity")
                    for name in ("original.png", "corrected.png"):
                        if manifest[name.removesuffix(".png")] != str(
                            Path("pages") / directory.name / name
                        ):
                            raise ValueError("invalid manifest path")
                        if (
                            hashlib.sha256((directory / name).read_bytes()).hexdigest()
                            != manifest["checksums"][name]
                        ):
                            raise ValueError("checksum mismatch")
                    if parent == "staging":
                        os.rename(directory, self.root / "pages" / directory.name)
                        sync_dir(self.root / "pages")
                        sync_dir(self.root / "staging")
                    with self.connection() as db:
                        self._register(db, manifest)
                    recovered.append(directory.name)
                except (ValueError, KeyError, OSError):
                    incomplete.append(directory.name)
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET status=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END, "
                "error='Interrupted before completion' WHERE status='running'"
            )
        event("recovery", recovered=recovered, incomplete=incomplete)
        return {"recovered": recovered, "incomplete": incomplete}

    def get(self, page_id):
        with self.connection() as db:
            row = db.execute(
                """SELECT p.*,j.status AS inference_status,j.error AS inference_error
                FROM pages p JOIN jobs j ON j.page_id=p.id WHERE p.id=?""",
                (page_id,),
            ).fetchone()
        if row is None:
            raise KeyError(page_id)
        result = dict(row)
        for key in ("tags", "proposed_tags", "corners", "metrics", "inference_metrics"):
            if result[key] is not None:
                result[key] = json.loads(result[key])
        result["effective_title"] = (
            result["title"] if result["title"] is not None else result["proposed_title"]
        )
        result["effective_tags"] = (
            result["tags"] if result["tags"] is not None else result["proposed_tags"]
        )
        return result

    def search(self, query="", course=None, after=None, before=None, limit=100):
        terms = fts_query(query)
        where, args = [], []
        join = ""
        order = "p.created DESC"
        if query.strip() and not terms:
            return []
        if terms:
            join = "JOIN search_index ON search_index.page_id=p.id"
            where.append("search_index MATCH ?")
            args.append(terms)
            order = "bm25(search_index,0,4,3,1,1), p.created DESC"
        for clause, value in (
            ("p.course=?", course),
            ("substr(p.created,1,10)>=?", after),
            ("substr(p.created,1,10)<=?", before),
        ):
            if value:
                where.append(clause)
                args.append(value)
        sql = f"SELECT p.id FROM pages p {join}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order} LIMIT ?"
        with self.connection() as db:
            ids = [r["id"] for r in db.execute(sql, (*args, min(limit, 1000)))]
        return [self.get(page_id) for page_id in ids]

    def edit(self, page_id, title, tags, description, course):
        self.get(page_id)
        meta = Metadata(title=title, tags=tags).checked()
        with self.connection() as db:
            db.execute(
                "UPDATE pages SET title=?,tags=?,description=?,course=?,reviewed=1 WHERE id=?",
                (meta.title, json.dumps(meta.tags), description, course, page_id),
            )
            self._index(db, page_id)
        return self.get(page_id)

    def recrop(self, page_id, corners):
        page = self.get(page_id)
        image = cv2.imread(str(self.root / page["original"]))
        corrected = warp(image, corners)
        relative = Path("pages") / page_id / f"crop-{uuid.uuid4()}.png"
        durable_write(self.root / relative, encode(corrected))
        sync_dir((self.root / relative).parent)
        with self.connection() as db:
            db.execute(
                "UPDATE pages SET corrected=?,corners=?,generation=generation+1,"
                "proposed_title='',proposed_tags='[]',inference_metrics='{}' WHERE id=?",
                (str(relative), json.dumps(corners), page_id),
            )
            generation = db.execute(
                "SELECT generation FROM pages WHERE id=?", (page_id,)
            ).fetchone()[0]
            db.execute(
                "UPDATE jobs SET generation=?,status=?,queued=?,attempts=0,error=NULL WHERE page_id=?",
                (
                    generation,
                    "queued" if self.inference_enabled else "disabled",
                    time.time(),
                    page_id,
                ),
            )
            self._index(db, page_id)
        return self.get(page_id)

    def claim(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY queued LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = time.time()
            db.execute(
                "UPDATE jobs SET status='running',started=?,attempts=attempts+1 WHERE page_id=?",
                (now, row["page_id"]),
            )
            job = dict(row)
            job["queue_ms"] = (now - row["queued"]) * 1000
            return job

    def finish(self, job, metadata=None, metrics=None, error=None):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE page_id=?", (job["page_id"],)).fetchone()
            if row["generation"] != job["generation"] or row["status"] != "running":
                return False
            db.execute(
                "UPDATE jobs SET status=?,finished=?,error=? WHERE page_id=?",
                ("failed" if error else "completed", time.time(), error, job["page_id"]),
            )
            if metadata is not None:
                metadata.checked()
                db.execute(
                    "UPDATE pages SET proposed_title=?,proposed_tags=?,inference_metrics=? WHERE id=?",
                    (
                        metadata.title,
                        json.dumps(metadata.tags),
                        json.dumps(metrics or {}),
                        job["page_id"],
                    ),
                )
                self._index(db, job["page_id"])
        return True

    def retry(self, page_id):
        self.get(page_id)
        if not self.inference_enabled:
            raise ValueError("Inference is disabled in configuration")
        with self.connection() as db:
            row = db.execute("SELECT status FROM jobs WHERE page_id=?", (page_id,)).fetchone()
            if row["status"] == "running":
                raise ValueError("Inference is already running")
            db.execute(
                "UPDATE jobs SET status='queued',queued=?,error=NULL,attempts=0 WHERE page_id=?",
                (time.time(), page_id),
            )
        return self.get(page_id)
