import json
import uuid
import time # Import time for sleep

import pytest
# Remove httpx import if not used directly, TestClient is now used
# from httpx import AsyncClient
from fastapi import status
from unittest.mock import patch

from shared_code.schemas import JobStatus
# Use sync helpers
from shared_code.helpers.job_status import get_job_status
from shared_code.queue_handler import process_analysis
from .test_utils import DummyMsg

# Tests are now synchronous
def test_analyze_invalid_payload(client):
    response = client.post("/api/analyze", json={"bad": "data"})
    assert response.status_code == 422

# Remove anyio marker, make test sync
def test_progress_for_nonexistent_job(client):
    job_id = "nonexistent-job-" + str(uuid.uuid4())
    response = client.get(f"/api/progress/{job_id}")
    # The progress endpoint should return 200 even for non-existent jobs,
    # indicating a default pending state.
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "progress" in data
    assert "statusMessage" in data
    assert "isComplete" in data
    assert data["progress"] == 0
    assert data["statusMessage"] == "Початок аналізу" # Or similar default message
    assert data["isComplete"] is False

def test_report_for_nonexistent_job(client):
    response = client.get("/api/report/nonexistent-job")
    # The report endpoint should return 404 if the status blob doesn't exist
    assert response.status_code == 404

# Keep the patch for simulating failure, but make the test sync
@patch("shared_code.queue_handler.fetch_band_urls")
def test_job_failure_simulation(
    mock_fetch_urls,
    client,
    analysis_queue_client, # Sync queue client
    reports_container_client, # Sync blob container client
    blob_service_client # Sync blob service client
):
    # Configure the mock to raise an error when called
    mock_fetch_urls.side_effect = RuntimeError("Simulated Sentinel API error")

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
    # Use sync client call
    response = client.post("/api/analyze", json=test_payload)
    assert response.status_code == status.HTTP_202_ACCEPTED
    job_id = response.json()["jobId"]

    # Get message from queue (sync)
    message = None
    for _ in range(5):
        messages = list(analysis_queue_client.receive_messages(max_messages=1, visibility_timeout=5))
        if messages:
            message = messages[0]
            break
        time.sleep(1)
    assert message, "Expected message in queue for failure test"
    raw_message = message.content

    dummy_msg = DummyMsg(raw_message)
    process_analysis(dummy_msg)

 
    time.sleep(1)
    status_update = get_job_status(blob_service_client, job_id)
    assert status_update is not None, "Status blob should exist after failure"
    assert status_update.status == JobStatus.FAILED
    assert status_update.progress == -1
    # Check if the error message from the exception is in the status message
    assert "Simulated Sentinel API error" in status_update.message

    # Check report endpoint (sync)
    report_response = client.get(f"/api/report/{job_id}")
    # Depending on the report endpoint logic for failed jobs, it might return 200/202 with FAILED status or 404/500.
    # Assuming it returns 2xx with FAILED status based on previous logic.
    assert report_response.status_code in {status.HTTP_200_OK, status.HTTP_202_ACCEPTED}
    response_data = report_response.json()
    # The actual structure might be nested under "detail" for HTTPExceptions
    if "detail" in response_data and isinstance(response_data["detail"], dict):
        assert response_data["detail"].get("status") == JobStatus.FAILED.value
    elif "status" in response_data:
        assert response_data["status"] == JobStatus.FAILED.value
    else:
        pytest.fail(f"Unexpected response structure for failed job report: {response_data}")

    # Delete message (sync)
    analysis_queue_client.delete_message(message)
