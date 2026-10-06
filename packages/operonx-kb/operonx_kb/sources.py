"""Where documents come from besides a folder: S3 and Google Drive (track5 P7).

Each is a job source like operonx's ``DirSource``: ``items()`` yields one ingest
item per file, ``{"path", "key", "name", "metadata"}``, so the same ``ingest_flow``
(a ``Job``) or ``KnowledgeBase.add`` reads it. The file is downloaded to a local
``cache`` folder first — an item never carries the bytes, so neither the job's
records nor its trace do — and its ``key`` is stable across runs
(``s3://bucket/key``, ``gdrive:<file id>``), so a job resumes and an unchanged file
is skipped by its hash::

    job = Job("ingest_s3", graph=ingest_flow, source=S3Source("docs-bucket", prefix="hr/"),
              key="key", inputs={"collection": "handbook", ...})

The cloud client is created from the default credentials (boto3's chain; a Google
service account file) unless one is passed in. Needs the ``s3`` or ``drive`` extra.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Dict, Iterator, Optional, Sequence

from operonx_kb.errors import MissingExtraError

__all__ = ["S3Source", "DriveSource"]


def _cache_dir(cache: Optional[str], kind: str) -> Path:
    path = Path(cache) if cache else Path(tempfile.gettempdir()) / f"operonx-kb-{kind}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _local(cache: Path, key: str, name: str) -> Path:
    """A cache path per source key, keeping the file's extension (the parser sniffs it)."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return cache / f"{digest}{PurePosixPath(name).suffix.lower()}"


class S3Source:
    """Every object under ``prefix`` in ``bucket`` (S3 or any S3-compatible store).

    Args:
        bucket: The bucket.
        prefix: Only keys starting with this.
        suffixes: Only keys ending with one of these (``(".pdf", ".docx")``); default all.
        cache: Where objects are downloaded (default: a temp folder).
        client: A boto3 S3 client (default: ``boto3.client("s3", endpoint_url=...)``).
        endpoint_url: For an S3-compatible store (MinIO, R2).
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        suffixes: Optional[Sequence[str]] = None,
        cache: Optional[str] = None,
        client: Any = None,
        endpoint_url: Optional[str] = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self.suffixes = tuple(s.lower() for s in suffixes) if suffixes else None
        self.cache = _cache_dir(cache, "s3")
        self._client = client
        self.endpoint_url = endpoint_url

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise MissingExtraError("Ingest from S3 (S3Source)", "s3", exc) from exc
            self._client = boto3.client("s3", endpoint_url=self.endpoint_url)
        return self._client

    def _objects(self) -> Iterator[Dict[str, Any]]:
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": self.prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kwargs)
            for obj in page.get("Contents") or []:
                key = obj["Key"]
                if key.endswith("/") or (self.suffixes and not key.lower().endswith(self.suffixes)):
                    continue
                yield obj
            if not page.get("IsTruncated"):
                return
            token = page.get("NextContinuationToken")

    def _fetch(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        key = obj["Key"]
        source_key = f"s3://{self.bucket}/{key}"
        name = PurePosixPath(key).name
        path = _local(self.cache, source_key, name)
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        path.write_bytes(body.read())
        return {
            "path": str(path),
            "key": source_key,
            "name": name,
            "metadata": {"source": "s3", "etag": str(obj.get("ETag", "")).strip('"')},
        }

    async def items(self) -> AsyncIterator[Dict[str, Any]]:
        objects = await asyncio.to_thread(lambda: sorted(self._objects(), key=lambda o: o["Key"]))
        for obj in objects:
            yield await asyncio.to_thread(self._fetch, obj)

    def __repr__(self) -> str:
        return f"s3({self.bucket}/{self.prefix})"


#: Google's own formats, exported to one our parsers read.
EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
}  # fmt: skip


class DriveSource:
    """Every file in a Google Drive folder (and its sub-folders with ``recursive``).

    Google Docs, Slides and Sheets are exported to .docx, .pptx and .xlsx; other
    files are downloaded as they are. Trashed files are skipped.

    Args:
        folder_id: The folder's id (the last part of its URL).
        recursive: Walk sub-folders.
        cache: Where files are downloaded (default: a temp folder).
        service: A Drive v3 service (``googleapiclient.discovery.build("drive", "v3")``);
            default: built from ``credentials_file`` (a service account's JSON key).
        credentials_file: The service account key the default service uses.
    """

    FOLDER = "application/vnd.google-apps.folder"
    FIELDS = "nextPageToken, files(id, name, mimeType, modifiedTime, md5Checksum)"

    def __init__(
        self,
        folder_id: str,
        *,
        recursive: bool = False,
        cache: Optional[str] = None,
        service: Any = None,
        credentials_file: Optional[str] = None,
    ) -> None:
        self.folder_id = folder_id
        self.recursive = recursive
        self.cache = _cache_dir(cache, "drive")
        self._service = service
        self.credentials_file = credentials_file

    @property
    def service(self) -> Any:
        if self._service is None:
            try:
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
            except ImportError as exc:
                raise MissingExtraError(
                    "Ingest from Google Drive (DriveSource)", "drive", exc
                ) from exc
            scopes = ["https://www.googleapis.com/auth/drive.readonly"]
            creds = (
                service_account.Credentials.from_service_account_file(
                    self.credentials_file, scopes=scopes
                )
                if self.credentials_file
                else None
            )
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _files(self, folder: str) -> Iterator[Dict[str, Any]]:
        token = None
        while True:
            page = (
                self.service.files()
                .list(
                    q=f"'{folder}' in parents and trashed = false",
                    fields=self.FIELDS,
                    pageToken=token,
                    pageSize=1000,
                )  # fmt: skip
                .execute()
            )
            for f in page.get("files") or []:
                if f["mimeType"] == self.FOLDER:
                    if self.recursive:
                        yield from self._files(f["id"])
                    continue
                yield f
            token = page.get("nextPageToken")
            if not token:
                return

    def _fetch(self, f: Dict[str, Any]) -> Dict[str, Any]:
        files = self.service.files()
        exported = EXPORTS.get(f["mimeType"])
        name = f["name"]
        if exported:
            mime, ext = exported
            data = files.export(fileId=f["id"], mimeType=mime).execute()
            name = name if name.lower().endswith(ext) else name + ext
        else:
            data = files.get_media(fileId=f["id"]).execute()
        key = f"gdrive:{f['id']}"
        path = _local(self.cache, key, name)
        path.write_bytes(data if isinstance(data, bytes) else bytes(data))
        return {
            "path": str(path),
            "key": key,
            "name": name,
            "title": PurePosixPath(f["name"]).stem,
            "metadata": {"source": "gdrive", "modified": f.get("modifiedTime", "")},
        }

    async def items(self) -> AsyncIterator[Dict[str, Any]]:
        found = await asyncio.to_thread(lambda: sorted(self._files(self.folder_id),
                                                       key=lambda f: (f["name"], f["id"])))  # fmt: skip
        for f in found:
            yield await asyncio.to_thread(self._fetch, f)

    def __repr__(self) -> str:
        return f"gdrive({self.folder_id})"
