"""Tests for background job handlers: POST /path/submit + GET /jobs/{job_id}."""

import time

import pytest
from fastapi.testclient import TestClient

from hush.serve import HushApp
from hush.serve.jobs import JobStatus, JobStore


class TestJobStore:
    @pytest.mark.asyncio
    async def test_create_job(self):
        store = JobStore()
        job = await store.create("/test", {"x": 1})
        assert job.status == JobStatus.PENDING
        assert job.endpoint_path == "/test"
        assert job.inputs == {"x": 1}

    @pytest.mark.asyncio
    async def test_get_job(self):
        store = JobStore()
        job = await store.create("/test", {"x": 1})
        fetched = await store.get(job.id)
        assert fetched is not None
        assert fetched.id == job.id

    @pytest.mark.asyncio
    async def test_get_nonexistent_job(self):
        store = JobStore()
        fetched = await store.get("nonexistent")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_update_job(self):
        store = JobStore()
        job = await store.create("/test", {"x": 1})
        await store.update(job.id, status=JobStatus.RUNNING)
        updated = await store.get(job.id)
        assert updated.status == JobStatus.RUNNING

    @pytest.mark.asyncio
    async def test_complete_job(self):
        store = JobStore()
        job = await store.create("/test", {"x": 1})
        await store.update(
            job.id,
            status=JobStatus.COMPLETED,
            result={"result": 42},
            completed_at=time.time(),
        )
        updated = await store.get(job.id)
        assert updated.status == JobStatus.COMPLETED
        assert updated.result == {"result": 42}
        assert updated.completed_at is not None

    @pytest.mark.asyncio
    async def test_fail_job(self):
        store = JobStore()
        job = await store.create("/test", {"x": 1})
        await store.update(
            job.id,
            status=JobStatus.FAILED,
            error="something went wrong",
            completed_at=time.time(),
        )
        updated = await store.get(job.id)
        assert updated.status == JobStatus.FAILED
        assert updated.error == "something went wrong"


class TestJobHandler:
    def test_submit_job(self, slow_graph):
        app = HushApp()
        app.endpoint("/slow", graph=slow_graph, jobs=True)
        client = TestClient(app.fastapi)
        resp = client.post("/slow/submit", json={"x": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data

    def test_submit_disabled(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph, jobs=False)
        client = TestClient(app.fastapi)
        resp = client.post("/double/submit", json={"x": 1})
        assert resp.status_code in (404, 405)

    def test_job_status_not_found(self, slow_graph):
        app = HushApp()
        app.endpoint("/slow", graph=slow_graph, jobs=True)
        client = TestClient(app.fastapi)
        resp = client.get("/jobs/nonexistent-id")
        assert resp.status_code == 404

    def test_job_lifecycle(self, double_graph):
        """Submit a fast job and poll until completion."""
        app = HushApp()
        app.endpoint("/double", graph=double_graph, jobs=True)
        client = TestClient(app.fastapi)

        # Submit
        resp = client.post("/double/submit", json={"x": 5})
        job_id = resp.json()["job_id"]

        # Poll until done (max 5 attempts)
        for _ in range(10):
            status_resp = client.get(f"/jobs/{job_id}")
            assert status_resp.status_code == 200
            status = status_resp.json()
            if status["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)

        assert status["status"] == "completed"
        assert status["result"]["result"] == 10
