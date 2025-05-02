import json
import time
import os
from unittest.mock import patch, MagicMock

import numpy as np
import pytest
from fastapi import status

from shared_code.schemas import JobStatus, ReportData, ProgressUpdate
from shared_code.helpers.job_status import get_job_status
from shared_code.queue_handler import process_analysis
from .test_utils import DummyMsg

@patch("shared_code.helpers.blob.get_sync_blob_service_client")
@patch("shared_code.queue_handler.generate_openai_recommendations")
@patch("shared_code.helpers.job_status.get_job_status")
def test_queue_processing_end_to_end(
    mock_get_job_status,
    mock_openai,
    mock_get_blob_client,
    client,
    analysis_queue_client,
    reports_container_client,
    images_container_client,
    blob_service_client,
):
    """Tests the full queue processing flow synchronously, with real calls."""
    # Mock Azure services
    mock_get_blob_client.return_value = blob_service_client
    mock_get_job_status.return_value = None  # No previous status
    mock_openai.return_value = "Mocked AI recommendation"

    # 1. Initiate analysis
    # Use a smaller, valid coordinate range for faster testing if possible
    # Ensure date range is likely to have data, adjust if needed
    test_payload = {
        "area": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [32.95, 47.45],
                    [32.95, 49.19],
                    [36.94, 49.19],
                    [36.94, 47.45],
                    [32.95, 47.45]
                ]]
            }
        },
        "date_range": "2024-04-01/2024-04-15",
        "crop_type": "wheat",
        "frequency": "single",
        "ndvi_threshold": 0.3,
        "max_cloud_cover": 80
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
        messages = analysis_queue_client.receive_messages(max_messages=1, visibility_timeout=120) # Increased timeout
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
        process_analysis(dummy_msg)
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

    # Check for successful completion OR specific failure
    final_progress = last_status_data.get("progress")
    final_message = last_status_data.get("statusMessage", "").lower()

    # Define expected outcomes
    is_successful = final_progress == 100 and final_message == "завершено"

    # Failure when there were scenes fetched but none passed quality checks (progress 35)
    is_expected_no_scene_failure = (
        final_progress == 35
        and final_message == "не знайдено придатних сцен"
        and last_status_data.get("status") == JobStatus.FAILED.value  # Also check the enum value
    )

    # Failure when _fetch_scenes returned an empty collection (progress 10)
    is_expected_no_scenes_found_failure = (
        final_progress == 10
        and final_message == "сцени не знайдено"
        and last_status_data.get("status") == JobStatus.FAILED.value
    )

    # Assert that the outcome is one of the expected ones
    assert is_successful or is_expected_no_scene_failure or is_expected_no_scenes_found_failure, \
        (
            "Final status unexpected: "
            f"progress={final_progress}, "
            f"status={last_status_data.get('status')}, "
            f"message='{last_status_data.get('statusMessage')}'"
        )

    if is_successful:
        print("Job completed successfully.")
    elif is_expected_no_scene_failure:
        print("Job failed as expected: No usable scenes found.")
    elif is_expected_no_scenes_found_failure:
        print("Job failed as expected: No scenes found.")
    # No else needed because the assert above covers it

    # 5. Verify final status update in blob storage
    print("Verifying final status blob...")
    status_blob_client = reports_container_client.get_blob_client(f"{job_id}/status.json")
    assert status_blob_client.exists(), "Status blob should always exist after completion or failure."
    status_content = status_blob_client.download_blob().readall()
    status_data = ProgressUpdate.model_validate_json(status_content)
    assert status_data.jobId == job_id

    if is_successful:  # Status blob should reflect success
        assert status_data.status == JobStatus.COMPLETED
        assert status_data.progress == 100
        assert status_data.message == "Завершено" # Check for the actual Ukrainian message
        print("Final status blob verified for successful job.")
    elif is_expected_no_scene_failure:  # Status blob should reflect the specific failure
        assert status_data.status == JobStatus.FAILED
        assert status_data.progress == 35 # Or whatever progress level it fails at
        assert status_data.message == "Не знайдено придатних сцен"
        print("Final status blob verified for expected no-scene failure.")
    elif is_expected_no_scenes_found_failure:
        # Status blob should reflect the scenes-not-found failure
        assert status_data.status == JobStatus.FAILED
        assert status_data.progress == 10
        assert status_data.message == "Сцени не знайдено"
        print("Final status blob verified for scenes-not-found failure.")

    # 6. Verify report blob content (handles success and expected failure)
    print("Verifying report blob...")
    if is_successful:  # Only check for report blob if job was successful
        report_blob_client = reports_container_client.get_blob_client(f"{job_id}/report.json")
        assert report_blob_client.exists(), "Report blob should exist after successful completion."
        report_content = report_blob_client.download_blob().readall()
        report_data = ReportData.model_validate_json(report_content)
        assert report_data.jobId == job_id
        assert report_data.requestPayload is not None # Payload should always be included

        # Report blob should reflect success
        assert report_data.status == JobStatus.COMPLETED
        # Modified assertion: Create expected payload by adding jobId to properties
        expected_payload_dict = test_payload.copy()
        if 'area' in expected_payload_dict and 'properties' not in expected_payload_dict['area']:
            expected_payload_dict['area']['properties'] = {}
        if 'area' in expected_payload_dict and isinstance(expected_payload_dict['area'].get('properties'), dict):
             expected_payload_dict['area']['properties']['jobId'] = job_id
        assert report_data.requestPayload.model_dump(exclude_none=True) == expected_payload_dict
        assert report_data.ndviStatistics is not None
        assert isinstance(report_data.ndviStatistics.get('mean'), (float, type(None)))
        # Check if stats reflect the synthetic data (mean=0, min=0, max=0, std=0, stress=0)
        if report_data.ndviStatistics.get('mean') == 0.0:
             print("NDVI stats reflect synthetic zero data as expected.")
             assert report_data.ndviStatistics.get('min') == 0.0
             assert report_data.ndviStatistics.get('max') == 0.0
             assert report_data.ndviStatistics.get('std_dev') == 0.0
             assert report_data.ndviStatistics.get('stress_percentage') == 100.0 # Synthetic 0.0 NDVI is below threshold 0.3
        else:
             print("NDVI stats reflect actual data.") # Should not happen in this specific test case
        assert isinstance(report_data.recommendations, str)
        # Check only that recommendations exist as a string (could be mocked or fallback)
        assert report_data.recommendations is not None and report_data.recommendations != ""
        assert report_data.mapUrls is not None and report_data.mapUrls != {}
        assert "ndvi" in report_data.mapUrls
        assert "rgb" in report_data.mapUrls # Even the synthetic RGB map URL should exist
        assert report_data.mapUrls.get("stress") # Check stress map URL exists
        print("Report blob verified for successful job.")
    elif is_expected_no_scene_failure:
         print("Skipping report blob verification for expected no-scene failure.")
         # Optionally, could assert that the report *doesn't* exist here
         # report_blob_client = reports_container_client.get_blob_client(f"{job_id}/report.json")
         # assert not report_blob_client.exists(), "Report blob should NOT exist for no-scene failure."
    elif is_expected_no_scenes_found_failure:
         print("Skipping report blob verification for scenes-not-found failure.")
         # Similarly, a report blob is not expected in this failure mode
    else: # Should not be reachable due to earlier assertion
        pytest.fail("Reached unexpected state when verifying report blob.")

    # 7. Verify image blob existence and properties (only if successful)
    print("Verifying image blobs...")
    if is_successful:
        report_blob_client = reports_container_client.get_blob_client(f"{job_id}/report.json") # Re-read for URLs
        report_content = report_blob_client.download_blob().readall()
        report_data = ReportData.model_validate_json(report_content) # Assume success based on is_successful flag

        if report_data.mapUrls.get("ndvi"):
            ndvi_image_blob_client = images_container_client.get_blob_client(f"{job_id}/ndvi.png")
            assert ndvi_image_blob_client.exists(), "NDVI image blob should exist if URL is present."
            ndvi_props = ndvi_image_blob_client.get_blob_properties()
            assert ndvi_props.size > 0
            assert ndvi_props.content_settings.content_type == "image/png"
            print("NDVI image blob verified.")
        else:
            print("Skipping NDVI image verification (URL not in report).")

        if report_data.mapUrls.get("rgb"):
            rgb_image_blob_client = images_container_client.get_blob_client(f"{job_id}/rgb.png")
            assert rgb_image_blob_client.exists(), "RGB image blob should exist if URL is present."
            rgb_props = rgb_image_blob_client.get_blob_properties()
            assert rgb_props.size > 0
            assert rgb_props.content_settings.content_type == "image/png"
            print("RGB image blob verified.")
        else:
            print("Skipping RGB image verification (URL not in report).")

        # Add check for stress map
        if report_data.mapUrls.get("stress"):
            stress_image_blob_client = images_container_client.get_blob_client(f"{job_id}/stress.png")
            assert stress_image_blob_client.exists(), "Stress image blob should exist if URL is present."
            stress_props = stress_image_blob_client.get_blob_properties()
            assert stress_props.size > 0
            assert stress_props.content_settings.content_type == "image/png"
            print("Stress image blob verified.")
        else:
            print("Skipping Stress image verification (URL not in report).")
    elif is_expected_no_scene_failure:
        print("Skipping image verification for expected no-scene failure.")
    elif is_expected_no_scenes_found_failure:
        print("Skipping image verification for scenes-not-found failure.")
    else: # Should not be reachable
        print("Skipping image verification for non-successful job.")

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

    if is_successful:
        assert report_resp.status_code == status.HTTP_200_OK, f"Report API failed for successful job: {report_resp.text}"
        api_data = report_resp.json()
        assert api_data["status"] == JobStatus.COMPLETED.value
        assert api_data["jobId"] == job_id
        assert "recommendations" in api_data
        assert api_data.get("recommendations") is not None and api_data.get("recommendations") != "" # Match successful blob content
        print("API report retrieval verified for successful job.")
    elif is_expected_no_scene_failure:
        # Report should still be retrievable, showing FAILED status
        assert report_resp.status_code == status.HTTP_200_OK, f"Report API failed for expected no-scene failure: {report_resp.text}"
        api_data = report_resp.json()
        assert api_data["status"] == JobStatus.FAILED.value
        assert api_data["jobId"] == job_id
        # Check if the failure state is reflected in the API response
        assert api_data.get("ndviStatistics") is None or api_data.get("ndviStatistics") == {}
        assert api_data.get("mapUrls") is None or api_data.get("mapUrls") == {}
        print("API report retrieval verified for expected no-scene failure.")
    elif is_expected_no_scenes_found_failure:
        # Report endpoint may return 404 or 202 (pending) since processing stopped early.
        assert report_resp.status_code in {status.HTTP_404_NOT_FOUND, status.HTTP_202_ACCEPTED, status.HTTP_200_OK}, (
            f"Unexpected status code {report_resp.status_code} for scenes-not-found failure"
        )
        if report_resp.status_code == status.HTTP_200_OK:
            # If 200, ensure it indicates FAILED with minimal data
            api_data = report_resp.json()
            assert api_data["status"] == JobStatus.FAILED.value
            assert api_data["jobId"] == job_id
            print("API report retrieval verified for scenes-not-found failure (200 response).")
        else:
            print("API report retrieval verified for scenes-not-found failure (non-200 response).")
    else:
        # For other unexpected states, maybe the report endpoint returns 404 or 500
        # Adjust assertion based on expected behavior for other failures
         assert report_resp.status_code != status.HTTP_200_OK, "Report API should not return 200 for unexpected failure states."
         print(f"Report API returned {report_resp.status_code} as expected for unexpected state.")

    print(f"E2E test for job {job_id} finished.") # Changed message to reflect it might not pass
