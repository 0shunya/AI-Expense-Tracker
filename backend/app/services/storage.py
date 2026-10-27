"""Storage for receipt originals.

* ``database`` (default): bytes live in the ``stored_files`` table. Survives redeploys on hosts whose
  filesystem is ephemeral (Render, Railway) with no extra service or bill. Fine for a demo-scale app;
  a high-volume deployment should move to object storage.
* ``local``: files under UPLOAD_DIR. Convenient for development; only persistent with a mounted disk.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import StoredFile


class StorageError(Exception):
    pass


class Storage(Protocol):
    name: str

    def save(self, db: Session, data: bytes, content_type: str) -> str: ...
    def load(self, db: Session, key: str) -> bytes: ...
    def delete(self, db: Session, key: str) -> None: ...


class DatabaseStorage:
    name = "database"

    def save(self, db: Session, data: bytes, content_type: str) -> str:
        key = uuid.uuid4().hex
        db.add(StoredFile(key=key, content=data, content_type=content_type, size_bytes=len(data)))
        return key

    def load(self, db: Session, key: str) -> bytes:
        row = db.get(StoredFile, key)
        if row is None:
            raise StorageError("Stored file not found.")
        return row.content

    def delete(self, db: Session, key: str) -> None:
        row = db.get(StoredFile, key)
        if row is not None:
            db.delete(row)


class LocalStorage:
    name = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if path.parent != self.root:
            raise StorageError("Invalid storage key.")
        return path

    def save(self, db: Session, data: bytes, content_type: str) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        key = uuid.uuid4().hex
        self._path(key).write_bytes(data)
        return key

    def load(self, db: Session, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("Stored file not found.") from exc

    def delete(self, db: Session, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def get_storage(name: str | None = None) -> Storage:
    settings = get_settings()
    name = name or settings.storage_backend
    if name == "local":
        return LocalStorage(settings.upload_dir)
    return DatabaseStorage()
