import asyncio
import json
import numpy as np
import pytest
from fastapi import status
from unittest.mock import patch
import aiohttp

from shared_code.schemas import JobStatus, ReportData, ProgressUpdate
from shared_code.helpers.job_status import update_job_status, get_job_status
from process_analysis_job.main import process_analysis
from .test_utils import DummyMsg

@pytest.fixture(autouse=True)
def stub_external_services(monkeypatch):
    # Stub fetch_band_urls used inside process_analysis
    async def fake_fetch_band_urls(job_id, payload):
        await asyncio.sleep(0.1) # Simulate network delay
        return {"nir": "dummy_url", "red": "dummy_url"}
    monkeypatch.setattr(
        "process_analysis_job.main.fetch_band_urls",
        fake_fetch_band_urls,
    )

    # Stub NDVI computation
    async def fake_read_and_compute_ndvi(job_id, urls):
        await asyncio.sleep(0.1) # Simulate computation delay
        return np.zeros((5, 5)), None
    monkeypatch.setattr(
        "process_analysis_job.main.read_and_compute_ndvi",
        fake_read_and_compute_ndvi,
    )

    # Stub OpenAI recommendation generator
    async def fake_generate_openai_recommendations(job_id, ndvi, mask, crop_type):
        await asyncio.sleep(0.1) # Simulate API call delay
        return "Mocked recommendation"
    monkeypatch.setattr(
        "process_analysis_job.main.generate_openai_recommendations",
        fake_generate_openai_recommendations,
    )

async def test_queue_processing_end_to_end(
    client,
    analysis_queue_client,
    reports_container_client,
    images_container_client,
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
    assert job_id is not None

    # 2. Get message from queue
    messages = []
    for _ in range(5):
        messages = [m async for m in analysis_queue_client.receive_messages(max_messages=1, visibility_timeout=5)]
        if messages:
            break
        await asyncio.sleep(0.5)
    assert messages, "Expected a message in the analysis queue after retries"
    message = messages[0]
    raw_message = message.content

    # 3. Start processing job in the background
    dummy_msg = DummyMsg(raw_message)
    processing_task = asyncio.create_task(process_analysis(dummy_msg, blob_service_client))

    # 4. Start SSE connection and collect status updates concurrently
    status_updates = []
    event_type = None
    try:
        async with client.stream("GET", f"/api/progress/{job_id}") as response:
            response.raise_for_status() # Raises exception for 4xx/5xx
            assert "text/event-stream" in response.headers["content-type"]
            
            async for line in response.aiter_lines():
                if line:
                    line = line.strip()
                    if line.startswith('event:'):
                        event_type = line[6:].strip()
                    elif line.startswith('data:'):
                        data = json.loads(line[5:])
                        if event_type in ['progress', 'complete']:
                            status_updates.append(data)
                            if event_type == 'complete':
                                break
                        event_type = None # Reset after processing data line
    finally:
        # Ensure the background task is awaited even if SSE fails
        await processing_task 

    # 5. Wait briefly for any final blob writes if needed (optional)
    # await asyncio.sleep(1) 

    # 6. Verify status updates were received via SSE
    assert len(status_updates) > 0, "Should receive at least one status update via SSE"
    # Check for PENDING first, as it might be the first status sent
    assert any(update.get('status') == JobStatus.PENDING.value for update in status_updates), "Should receive PENDING status"
    assert any(update.get('status') == JobStatus.PROCESSING.value for update in status_updates), "Should receive PROCESSING status"
    assert any(update.get('status') == JobStatus.COMPLETED.value for update in status_updates), "Should receive COMPLETED status"
    
    # 7. Verify final status update in blob storage
    status_blob_client = reports_container_client.get_blob_client(f"{job_id}/status.json")
    assert await status_blob_client.exists(), "Status blob should exist"
    status_content = await (await status_blob_client.download_blob()).readall()
    status_data = ProgressUpdate.model_validate_json(status_content)
    assert status_data.jobId == job_id
    assert status_data.status == JobStatus.COMPLETED
    assert status_data.progress == 100
    assert status_data.message == "Analysis complete"

    # 8. Verify report blob content
    report_blob_client = reports_container_client.get_blob_client(f"{job_id}/report.json")
    assert await report_blob_client.exists(), "Report blob should exist"
    report_content = await (await report_blob_client.download_blob()).readall()
    report_data = ReportData.model_validate_json(report_content)
    assert report_data.jobId == job_id
    assert report_data.status == JobStatus.COMPLETED
    assert report_data.requestPayload.model_dump(exclude_none=True) == test_payload
    assert report_data.ndviStatistics is not None
    assert report_data.recommendations == "Mocked recommendation"
    assert report_data.mapUrls is not None
    assert "ndvi" in report_data.mapUrls
    assert report_data.mapUrls["ndvi"] is not None

    # 9. Verify image blob existence and properties
    ndvi_image_blob_client = images_container_client.get_blob_client(f"{job_id}/ndvi_map.png")
    assert await ndvi_image_blob_client.exists(), "NDVI image blob should exist"
    ndvi_props = await ndvi_image_blob_client.get_blob_properties()
    assert ndvi_props.size > 0
    assert ndvi_props.content_settings.content_type == "image/png"

    # 10. Verify message is deleted from queue
    await analysis_queue_client.delete_message(message)
    await asyncio.sleep(1)
    messages_after = [m async for m in analysis_queue_client.receive_messages(max_messages=1)]
    assert not messages_after, "Message should be deleted from the queue"

    # 11. Retrieve report via API
    report_resp = await client.get(f"/api/report/{job_id}")
    assert report_resp.status_code == status.HTTP_200_OK
    api_data = report_resp.json()
    assert api_data["status"] == JobStatus.COMPLETED.value
    assert api_data["jobId"] == job_id
    assert "recommendations" in api_data
