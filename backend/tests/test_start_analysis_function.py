import json
import uuid
import pytest
import os
import azure.functions as func
from unittest.mock import Mock, patch, ANY, call, AsyncMock, MagicMock
import importlib # For reloading modules in tests

# Change import to be relative to project root
from functions.StartAnalysisFunction.main import main
from functions.shared_code.schemas import StartAnalysisResponse, ErrorResponse, JobStatus
from functions.shared_code import helpers

# --- Test Data ---
VALID_POLYGON_GEOM = {
    "type": "Polygon",
    "coordinates": [[ [34.4, 49.5], [34.6, 49.5], [34.6, 49.7], [34.4, 49.7], [34.4, 49.5] ]]
}
VALID_FEATURE_POLYGON = {"type": "Feature", "geometry": VALID_POLYGON_GEOM, "properties": {}}
VALID_PAYLOAD_DICT = {
    "area": VALID_FEATURE_POLYGON,
    "frequency": "single",
    "ndvi_threshold": 0.3,
    "date_range": "2024-01-01/2024-05-01",
    "max_cloud_cover": 25,
    "crop_type": "Wheat"
}

# --- Fixtures for StartAnalysisFunction ---
@pytest.fixture
def mock_env_vars_start_analysis(monkeypatch):
    """Set environment variables specific to StartAnalysisFunction tests."""
    monkeypatch.setenv("AzureWebJobsStorage", "start-test-conn-string")
    monkeypatch.setenv("ANALYSIS_QUEUE_NAME", "test-analysis-queue")
    # These are needed by check_environment_variables in main
    monkeypatch.setenv("REPORTS_CONTAINER_NAME", "test-reports")
    monkeypatch.setenv("IMAGES_CONTAINER_NAME", "test-images")

# --- Test Cases (now async) ---

@pytest.mark.asyncio
# Patch the async helper directly
@patch('functions.StartAnalysisFunction.main.update_job_status', new_callable=AsyncMock)
@patch('functions.StartAnalysisFunction.main.uuid.uuid4')
# Patch the QueueClient factory method
@patch('functions.StartAnalysisFunction.main.QueueClient.from_connection_string')
async def test_analyze_success(mock_queue_client_factory, mock_uuid4, mock_update_status, mock_env_vars_start_analysis):
    """Test successful analysis request."""
    # Arrange
    conn_str = os.getenv("AzureWebJobsStorage")
    queue_name = os.getenv("ANALYSIS_QUEUE_NAME")

    test_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
    mock_uuid4.return_value = test_uuid

    # Mock the async QueueClient and its async methods/context manager
    mock_queue_client = AsyncMock()
    mock_queue_client_factory.return_value = mock_queue_client

    req = func.HttpRequest(
        method='POST',
        body=json.dumps(VALID_PAYLOAD_DICT).encode('utf-8'),
        url='/api/analyze',
        headers={'Content-Type': 'application/json'}
    )

    # Act
    response = await main(req) # Await the async main function

    # Assert
    assert response.status_code == 202
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    assert response_body["jobId"] == str(test_uuid)
    StartAnalysisResponse.model_validate(response_body)

    # Check update_job_status was awaited correctly for PENDING status
    mock_update_status.assert_awaited_once_with(
        str(test_uuid),
        JobStatus.PENDING,
        0,
        "Analysis request received and queued."
    )

    # Check QueueClient factory call
    mock_queue_client_factory.assert_called_once_with(
        conn_str=conn_str,
        queue_name=queue_name
        # Check default policy if needed, removed explicit policy from main
    )

    # Check that __aenter__ was called on the client (context manager entry)
    mock_queue_client.__aenter__.assert_awaited_once()

    # Check send_message was awaited with correct data
    mock_queue_client.send_message.assert_awaited_once()
    # Message content is str, encoded by default by QueueClient
    sent_message_str = mock_queue_client.send_message.await_args[0][0]
    sent_message_dict = json.loads(sent_message_str)
    assert sent_message_dict["jobId"] == str(test_uuid)
    assert sent_message_dict["payload"] == VALID_PAYLOAD_DICT

    # Check that __aexit__ was called on the client (context manager exit)
    mock_queue_client.__aexit__.assert_awaited_once()

@pytest.mark.asyncio
# No need to mock helpers if execution stops early
async def test_analyze_invalid_json(mock_env_vars_start_analysis):
    """Test request with invalid JSON body."""
    req = func.HttpRequest(
        method='POST',
        body=b'{invalid json',
        url='/api/analyze',
        headers={'Content-Type': 'application/json'}
    )
    response = await main(req)
    assert response.status_code == 400
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    assert "Please pass a valid JSON object in the request body" in response_body["message"]
    ErrorResponse.model_validate(response_body)

@pytest.mark.asyncio
# No need to mock helpers if execution stops early
async def test_analyze_validation_error(mock_env_vars_start_analysis):
    """Test request with data that fails Pydantic validation."""
    invalid_payload = VALID_PAYLOAD_DICT.copy()
    invalid_payload["ndvi_threshold"] = 1.5 # Invalid value
    req = func.HttpRequest(
        method='POST',
        body=json.dumps(invalid_payload).encode('utf-8'),
        url='/api/analyze',
        headers={'Content-Type': 'application/json'}
    )
    response = await main(req)
    assert response.status_code == 400
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    assert "Invalid request body" in response_body["message"]
    assert "details" in response_body
    assert isinstance(response_body["details"], list)
    assert len(response_body["details"]) > 0
    assert "ndvi_threshold" in str(response_body["details"])
    ErrorResponse.model_validate(response_body)

@pytest.mark.asyncio
# Patch async helper
@patch('functions.StartAnalysisFunction.main.update_job_status', new_callable=AsyncMock)
@patch('functions.StartAnalysisFunction.main.uuid.uuid4')
@patch('functions.StartAnalysisFunction.main.QueueClient.from_connection_string')
async def test_analyze_queue_send_error(mock_queue_client_factory, mock_uuid4, mock_update_status, mock_env_vars_start_analysis):
    """Test scenario where sending message to queue fails."""
    test_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
    mock_uuid4.return_value = test_uuid

    # Mock QueueClient and make send_message async method fail
    mock_queue_client = AsyncMock()
    mock_queue_client.send_message.side_effect = Exception("Queue connection failed")
    mock_queue_client_factory.return_value = mock_queue_client

    req = func.HttpRequest(
        method='POST',
        body=json.dumps(VALID_PAYLOAD_DICT).encode('utf-8'),
        url='/api/analyze',
        headers={'Content-Type': 'application/json'}
    )

    response = await main(req)

    assert response.status_code == 500
    response_body = json.loads(response.get_body())
    assert response_body["message"] == "Failed to queue analysis job."
    ErrorResponse.model_validate(response_body)

    # Check update_job_status awaited twice: PENDING then FAILED
    assert mock_update_status.await_count == 2
    # Use assert_has_awaits for checking multiple calls
    mock_update_status.assert_has_awaits([
        call(
            str(test_uuid),
            JobStatus.PENDING,
            0,
            "Analysis request received and queued."
        ),
        call(
            str(test_uuid),
            JobStatus.FAILED,
            -1,
            ANY # Check message contains error details
        )
    ])

    # Check the FAILED status update call message more specifically
    failed_call_args = mock_update_status.await_args_list[1][0]
    assert "Failed to queue job" in failed_call_args[3]
    assert "Queue connection failed" in failed_call_args[3]

@pytest.mark.asyncio
async def test_analyze_missing_connection_string(monkeypatch):
    """Test scenario where AzureWebJobsStorage is not set."""
    # Arrange: Delete the critical env var
    # Note: This needs to happen before the main function runs and calls check_environment_variables
    monkeypatch.delenv("AzureWebJobsStorage", raising=False)

    # Reload main module to re-evaluate env vars at import/function scope if necessary
    # This might not be strictly needed if check_environment_variables uses os.getenv directly
    # importlib.reload(functions.StartAnalysisFunction.main)

    req = func.HttpRequest(
        method='POST',
        body=json.dumps(VALID_PAYLOAD_DICT).encode('utf-8'),
        url='/api/analyze',
        headers={'Content-Type': 'application/json'}
    )

    # Act: Need to wrap the call if using mocker fixture for env vars
    # But here monkeypatch is used directly in the test
    response = await main(req)

    # Assert: Check for 503 Service Unavailable
    assert response.status_code == 503
    response_body = json.loads(response.get_body())
    assert "Internal server configuration error" in response_body['message']
