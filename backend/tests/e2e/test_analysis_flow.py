import asyncio
import json
import uuid

import pytest
from httpx import AsyncClient
from fastapi import status
from unittest.mock import patch

from shared_code.schemas import JobStatus
from shared_code.helpers.job_status import update_job_status, get_job_status
from shared_code.queue_handler import process_analysis
from .test_utils import DummyMsg


async def test_analyze_invalid_payload(client):
    response = await client.post("/api/analyze", json={"bad": "data"})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_progress_for_nonexistent_job(client: AsyncClient):
    job_id = "nonexistent-job-" + str(uuid.uuid4())

    response = await client.get(f"/api/progress/{job_id}")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert "progress" in data
    assert "statusMessage" in data
    assert "isComplete" in data
    assert data["progress"] == 0
    assert data["statusMessage"] == "Початок аналізу"
    assert data["isComplete"] is False


async def test_report_for_nonexistent_job(client):
    response = await client.get("/api/report/nonexistent-job")
    assert response.status_code == 404


@patch("shared_code.queue_handler.fetch_band_urls")
async def test_job_failure_simulation(
    mock_fetch_urls,
    client,
    analysis_queue_client,
    reports_container_client,
    images_container_client,
    blob_service_client
):
    mock_fetch_urls.side_effect = Exception("Simulated Sentinel API error")

    test_payload = {
        "area": {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[0,0],[1,0],[1,1],[0,1],[0,0]]]}
        },
        "date_range": "2024-01-01/2024-01-05",
        "crop_type": "corn",
        "frequency": "single",
        "ndvi_threshold": 0.2,
        "max_cloud_cover": 10
    }
    response = await client.post("/api/analyze", json=test_payload)
    assert response.status_code == status.HTTP_202_ACCEPTED
    job_id = response.json()["jobId"]

    messages = []
    for _ in range(5):
        messages = [m async for m in analysis_queue_client.receive_messages(max_messages=1, visibility_timeout=5)]
        if messages:
            break
        await asyncio.sleep(0.5)
    assert messages, "Expected message in queue for failure test"
    message = messages[0]
    raw_message = message.content

    dummy_msg = DummyMsg(raw_message)
    await process_analysis(dummy_msg, blob_service_client)

    await asyncio.sleep(1)
    status_update = await get_job_status(blob_service_client, job_id)
    assert status_update is not None, "Status blob should exist after failure"
    assert status_update.status == JobStatus.FAILED
    assert status_update.progress == -1
    assert "Simulated Sentinel API error" in status_update.message

    report_response = await client.get(f"/api/report/{job_id}")
    assert report_response.status_code in {200, 202}
    response_data = report_response.json()
    if "detail" in response_data:
        assert response_data["detail"]["status"] == JobStatus.FAILED.value
    else:
        assert response_data["status"] == JobStatus.FAILED.value

    await analysis_queue_client.delete_message(message)
