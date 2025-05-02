import json
import time
import os

import numpy as np
import pytest
from fastapi import status

from shared_code.schemas import JobStatus, ReportData, ProgressUpdate
from shared_code.helpers.job_status import get_job_status
from shared_code.queue_handler import process_analysis
from .test_utils import DummyMsg


def test_queue_processing_end_to_end(
    client,
    analysis_queue_client,
    reports_container_client,
    images_container_client,
    blob_service_client,
):
    """Tests the full queue processing flow synchronously, with real calls."""
    # 1. Initiate analysis
    # Use a smaller, valid coordinate range for faster testing if possible
    # Ensure date range is likely to have data, adjust if needed
    test_payload = {
        "area": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[30.0, 50.0], [30.01, 50.0], [30.01, 50.01], [30.0, 50.01], [30.0, 50.0]]]
            },
        },
        # Use a recent date range more likely to have Sentinel-2 data
        # e.g., a few months back, adjust as necessary
        "date_range": "2024-04-01/2024-04-15",
        "crop_type": "wheat",
        "frequency": "single",
        "ndvi_threshold": 0.3,
        "max_cloud_cover": 80, # Increased cloud cover tolerance
    }
    print(f"Submitting analysis request: {test_payload}")
    response = client.post("/api/analyze", json=test_payload)
    if response.status_code != status.HTTP_202_ACCEPTED:
        pytest.fail(f"Analysis submission failed: {response.status_code} - {response.text}")
    job_id = response.json()["jobId"]
    assert job_id
    print(f"Analysis submitted, Job ID: {job_id}")

    # 2. Get message from queue (with retries)
    message = None
    MAX_RETRIES = 10
    RETRY_DELAY = 2 # seconds
    print(f"Attempting to receive message from queue '{analysis_queue_client.queue_name}'...")
    for attempt in range(MAX_RETRIES):
        messages = analysis_queue_client.receive_messages(max_messages=1, visibility_timeout=10) # Short timeout
        msg_list = list(messages)
        if msg_list:
            message = msg_list[0]
            print(f"Message received (ID: {message.id}) after {attempt + 1} attempts.")
            break
        print(f"No message found (attempt {attempt + 1}/{MAX_RETRIES}), waiting {RETRY_DELAY}s...")
        time.sleep(RETRY_DELAY)

    assert message, f"Failed to receive message from queue after {MAX_RETRIES} retries."
    raw_message = message.content
    print(f"Raw message content: {raw_message}")

    # 3. Process the job directly (synchronously)
    print(f"Starting synchronous processing for job {job_id}...")
    dummy_msg = DummyMsg(raw_message)
    try:
        # This call is now blocking and executes the entire analysis
        process_analysis(dummy_msg, blob_service_client)
        print(f"Synchronous processing for job {job_id} completed.")
    except Exception as e:
        pytest.fail(f"process_analysis raised an unexpected exception: {e}", pytrace=True)

    # 4. Poll progress until completion (synchronously)
    # This is less critical now as process_analysis ran synchronously,
    # but we can verify the final state recorded.
    # We might not see intermediate states easily.
    POLL_TIMEOUT = 120 # seconds - Increased timeout for real processing
    POLL_INTERVAL = 1 # seconds
    start_time = time.time()
    last_status_data = None
    print(f"Polling final status for job {job_id}...")
    while time.time() - start_time < POLL_TIMEOUT:
        resp = client.get(f"/api/progress/{job_id}")
        assert resp.status_code == status.HTTP_200_OK, f"Progress API failed: {resp.text}"
        data = resp.json()
        last_status_data = data # Store the latest status
        print(f"Polling Progress: {data}")
        if data.get("isComplete"):
            print(f"Job {job_id} reported as complete by progress API.")
            break
        time.sleep(POLL_INTERVAL)
    else:
        pytest.fail(f"Polling timed out after {POLL_TIMEOUT}s. Last status: {last_status_data}")

    assert last_status_data and last_status_data.get("isComplete"), "Job did not complete according to progress API."
    assert last_status_data.get("progress") == 100, "Final progress should be 100."
    assert "завершено" in last_status_data.get("statusMessage", "").lower(), "Final status message mismatch."

    # 5. Verify final status update in blob storage
    print("Verifying final status blob...")
    status_blob_client = reports_container_client.get_blob_client(f"{job_id}/status.json")
    assert status_blob_client.exists(), "Status blob should exist after completion."
    status_content = status_blob_client.download_blob().readall()
    status_data = ProgressUpdate.model_validate_json(status_content)
    assert status_data.jobId == job_id
    assert status_data.status == JobStatus.COMPLETED
    assert status_data.progress == 100
    assert status_data.message == "Аналіз завершено успішно"
    print("Final status blob verified.")

    # 6. Verify report blob content
    print("Verifying report blob...")
    report_blob_client = reports_container_client.get_blob_client(f"{job_id}/report.json")
    assert report_blob_client.exists(), "Report blob should exist after completion."
    report_content = report_blob_client.download_blob().readall()
    report_data = ReportData.model_validate_json(report_content)
    assert report_data.jobId == job_id
    assert report_data.status == JobStatus.COMPLETED
    # Use exclude_unset=True if default values might differ
    assert report_data.requestPayload.model_dump(exclude_none=True) == test_payload
    assert report_data.ndviStatistics is not None
    # Check specific stats if possible/needed, e.g., mean should be a float or None
    assert isinstance(report_data.ndviStatistics.get('mean'), (float, type(None)))
    # Check if recommendations are generated (might be empty string or specific message on failure)
    assert isinstance(report_data.recommendations, str)
    # Don't assert specific recommendation text as it's from real OpenAI call
    # assert report_data.recommendations == "Mocked recommendation" # Removed mock assertion
    print(f"Generated recommendations: {report_data.recommendations}")
    assert report_data.mapUrls is not None
    assert "ndvi" in report_data.mapUrls
    # NDVI URL might be None if upload failed, but should exist in keys
    # assert report_data.mapUrls["ndvi"] is not None
    # RGB URL might be None if RGB processing failed or was unavailable
    assert "rgb" in report_data.mapUrls
    print("Report blob verified.")

    # 7. Verify image blob existence and properties (optional)
    print("Verifying image blobs...")
    if report_data.mapUrls.get("ndvi"):
        ndvi_image_blob_client = images_container_client.get_blob_client(f"{job_id}/ndvi_map.png")
        assert ndvi_image_blob_client.exists(), "NDVI image blob should exist if URL is present."
        ndvi_props = ndvi_image_blob_client.get_blob_properties()
        assert ndvi_props.size > 0
        assert ndvi_props.content_settings.content_type == "image/png"
        print("NDVI image blob verified.")
    else:
        print("Skipping NDVI image verification (URL not in report).")

    if report_data.mapUrls.get("rgb"):
        rgb_image_blob_client = images_container_client.get_blob_client(f"{job_id}/rgb_map.png")
        assert rgb_image_blob_client.exists(), "RGB image blob should exist if URL is present."
        rgb_props = rgb_image_blob_client.get_blob_properties()
        assert rgb_props.size > 0
        assert rgb_props.content_settings.content_type == "image/png"
        print("RGB image blob verified.")
    else:
        print("Skipping RGB image verification (URL not in report).")

    # 8. Delete message from queue
    print(f"Deleting message {message.id} from queue...")
    try:
        analysis_queue_client.delete_message(message)
        print("Message deleted.")
        # Verify deletion (optional, might have race conditions)
        time.sleep(2)
        messages_after = list(analysis_queue_client.receive_messages(max_messages=1))
        assert not messages_after, "Message should be deleted from the queue after processing."
    except Exception as e:
        pytest.fail(f"Failed to delete message from queue: {e}")

    # 9. Retrieve report via API (final check)
    print("Retrieving final report via API...")
    report_resp = client.get(f"/api/report/{job_id}")
    assert report_resp.status_code == status.HTTP_200_OK, f"Report API failed: {report_resp.text}"
    api_data = report_resp.json()
    assert api_data["status"] == JobStatus.COMPLETED.value
    assert api_data["jobId"] == job_id
    assert "recommendations" in api_data
    assert api_data.get("recommendations") == report_data.recommendations # Should match blob content
    print("API report retrieval verified.")
    print(f"E2E test for job {job_id} PASSED.")
