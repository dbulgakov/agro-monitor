import asyncio
import json
import numpy as np
import pytest
from fastapi import status
from functions.lib.schemas import JobStatus
from functions.process_analysis_job.main import process_analysis

class DummyMsg:
    def __init__(self, content: str):
        self._content = content

    def get_json(self):
        return json.loads(self._content)

@pytest.fixture(autouse=True)
def stub_external_services(monkeypatch):
    # Stub fetch_band_urls used inside process_analysis
    async def fake_fetch_band_urls(job_id, payload):
        return {"nir": "dummy_url", "red": "dummy_url"}
    monkeypatch.setattr(
        "functions.process_analysis_job.main.fetch_band_urls",
        fake_fetch_band_urls,
    )

    # Stub NDVI computation
    async def fake_read_and_compute_ndvi(job_id, urls):
        return np.zeros((5, 5)), None
    monkeypatch.setattr(
        "functions.process_analysis_job.main.read_and_compute_ndvi",
        fake_read_and_compute_ndvi,
    )

    # Stub OpenAI recommendation generator
    async def fake_generate_openai_recommendations(job_id, ndvi, mask, crop_type):
        return ["Mocked recommendation"]
    monkeypatch.setattr(
        "functions.lib.helpers.openai_helpers.generate_openai_recommendations",
        fake_generate_openai_recommendations,
    )

async def test_queue_processing_end_to_end(
    client,
    analysis_queue_client,
    reports_container_client,
    reports_container_name,
    blob_service_client,
):
    # 1. Initiate analysis
    test_payload = {
        "area": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[30, 50], [30.1, 50], [30.1, 50.1], [30, 50.1], [30, 50]]]
            },
        },
        "date_range": "2023-07-01/2023-07-31",
        "crop_type": "wheat",
        "frequency": "single",
        "ndvi_threshold": 0.3,
        "max_cloud_cover": 80,
    }
    response = await client.post("/api/analyze", json=test_payload)
    assert response.status_code == status.HTTP_202_ACCEPTED
    job_id = response.json()["jobId"]

    # 2. Get message from queue
    messages = [m async for m in analysis_queue_client.receive_messages(max_messages=1)]
    assert messages, "Expected a message in the analysis queue"
    raw_message = messages[0].content

    # 3. Process the job
    dummy_msg = DummyMsg(raw_message)
    await process_analysis(dummy_msg)

    # 4. Wait briefly for blob write
    await asyncio.sleep(1)

    # 5. Check if blob report was created
    blob_client = reports_container_client.get_blob_client(f"{job_id}/report.json")
    props = await blob_client.get_blob_properties()
    assert props is not None

    # 6. Retrieve report via API
    report_resp = await client.get(f"/api/report/{job_id}")
    assert report_resp.status_code == status.HTTP_200_OK
    data = report_resp.json()
    assert data["status"] == JobStatus.COMPLETED.value
    assert "recommendations" in data
