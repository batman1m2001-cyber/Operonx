"""S3 and Google Drive as ingest sources (operonx_kb.sources), through the real ingest job.

The cloud clients are fakes with the calls the sources make (list, get / export):
what is tested is paging, filtering, export of Google formats, stable keys, and that
an item carries a path, never the bytes."""

import asyncio
import io

from operonx_kb.sources import DriveSource, S3Source

POLICY = b"# Leave policy\n\nEvery employee has twelve days of annual leave per year.\n"
TRAVEL = b"# Travel policy\n\nTaxi fares on business trips are refunded up to fifty euros.\n"


def run(coro):
    return asyncio.run(coro)


class FakeS3:
    """list_objects_v2 in pages of one, get_object."""

    def __init__(self, objects):
        self.objects = objects
        self.gets = []

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        at = int(ContinuationToken or 0)
        page = keys[at : at + 1]
        more = at + 1 < len(keys)
        return {"Contents": [{"Key": k, "ETag": f'"e{i}"'} for i, k in enumerate(page)],
                "IsTruncated": more, "NextContinuationToken": str(at + 1) if more else None}  # fmt: skip

    def get_object(self, Bucket, Key):
        self.gets.append(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


class FakeDrive:
    """files().list / get_media / export(...).execute()."""

    def __init__(self, tree):
        self.tree = tree  # folder id -> [file dicts with "data"]

    def files(self):
        return self

    def list(self, q, fields, pageToken=None, pageSize=1000):
        folder = q.split("'")[1]
        self._result = {
            "files": [
                {k: v for k, v in f.items() if k != "data"} for f in self.tree.get(folder, [])
            ]
        }
        return self

    def get_media(self, fileId):
        self._result = self._data(fileId)
        return self

    def export(self, fileId, mimeType):
        self.exported = mimeType
        self._result = self._data(fileId)
        return self

    def _data(self, file_id):
        return next(f["data"] for fs in self.tree.values() for f in fs if f["id"] == file_id)

    def execute(self):
        return self._result


def items(source):
    async def collect():
        return [i async for i in source]

    return run(collect())


def test_s3_pages_filters_and_keys_each_object_stably(tmp_path):
    s3 = FakeS3(
        {"hr/policy.md": POLICY, "hr/travel.md": TRAVEL, "hr/img.png": b"x", "it/x.md": b"#"}
    )
    got = items(S3Source("docs", prefix="hr/", suffixes=[".md"], cache=str(tmp_path), client=s3))
    assert [i["key"] for i in got] == ["s3://docs/hr/policy.md", "s3://docs/hr/travel.md"]
    assert all("data" not in i for i in got)  # a path, never the bytes
    assert open(got[0]["path"], "rb").read() == POLICY and got[0]["path"].endswith(".md")
    assert s3.gets == ["hr/policy.md", "hr/travel.md"]


def test_drive_walks_folders_and_exports_google_docs(tmp_path):
    tree = {
        "root": [
            {"id": "f1", "name": "policy.md", "mimeType": "text/markdown", "data": POLICY},
            {"id": "sub", "name": "travel", "mimeType": DriveSource.FOLDER},
            {
                "id": "d1",
                "name": "Notes",
                "mimeType": "application/vnd.google-apps.document",
                "data": b"PK",
            },
        ],
        "sub": [{"id": "f2", "name": "travel.md", "mimeType": "text/markdown", "data": TRAVEL}],
    }
    flat = items(DriveSource("root", cache=str(tmp_path), service=FakeDrive(tree)))
    assert [i["key"] for i in flat] == ["gdrive:d1", "gdrive:f1"]  # sub-folder not walked
    deep = items(DriveSource("root", recursive=True, cache=str(tmp_path), service=FakeDrive(tree)))
    assert sorted(i["key"] for i in deep) == ["gdrive:d1", "gdrive:f1", "gdrive:f2"]
    notes = next(i for i in deep if i["key"] == "gdrive:d1")
    assert notes["name"] == "Notes.docx" and notes["path"].endswith(".docx")


def test_an_s3_bucket_ingests_as_a_job_and_a_rerun_skips(kbx, tmp_path):
    from operonx.app.jobs import Job

    from operonx_kb.graphs import ingest_flow

    s3 = FakeS3({"hr/policy.md": POLICY, "hr/travel.md": TRAVEL})

    def job(name):
        return Job(name, graph=ingest_flow, items=S3Source("docs", prefix="hr/", cache=str(tmp_path / "c"), client=s3),
                   key="key", record_dir=str(tmp_path / "jobs"),
                   inputs={"collection": "docs", "catalog": "kb_catalog:main", "blobs": "kb_blob:main"})  # fmt: skip

    first = run(job("ingest_s3").run())
    assert first.status == "ok"
    assert sorted(r["action"] for r in first.results.values()) == ["new", "new"]
    assert sorted(d.key for d in kbx.documents("docs")) == [
        "s3://docs/hr/policy.md",
        "s3://docs/hr/travel.md",
    ]
    out = run(kbx.search("docs", "annual leave", k=1))
    assert out["hits"][0]["key"] == "s3://docs/hr/policy.md"
    again = run(job("ingest_s3_again").run())
    assert again.status == "ok"
    assert [r["action"] for r in again.results.values()] == ["skip", "skip"]
