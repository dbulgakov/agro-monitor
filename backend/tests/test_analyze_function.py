import json
import uuid
import pytest
import os
import azure.functions as func
from unittest.mock import Mock, patch, ANY, call # Use unittest.mock or install pytest-mock

# Change import to be relative to project root
from functions.AnalyzeFunction.main import main
from functions.shared_code.schemas import StartAnalysisResponse, ErrorResponse, JobStatus # Added JobStatus for consistency
from functions.shared_code import helpers # Import helpers to check its state if needed

# --- Test Data (reuse from schema tests or define specific) ---
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

# Fixture from conftest.py provides mocked clients automatically
# We might need the specific mock_blob_client for assertions
@pytest.fixture
def blob_client_from_conftest(mock_blob_service_client_factory):
    # Helper fixture to extract just the blob client mock from the factory fixture
    _, mock_blob_client = mock_blob_service_client_factory
    return mock_blob_client

# --- Test Cases ---

@patch('functions.AnalyzeFunction.main.update_job_status') # Patch where used
@patch('functions.AnalyzeFunction.main.uuid.uuid4')
@patch('functions.AnalyzeFunction.main.QueueClient.from_connection_string')
def test_analyze_success(mock_queue_client_from_conn_str, mock_uuid4, mock_update_status, blob_client_from_conftest):
    """Test successful analysis request."""
    # Arrange
    # Read constants set by fixture in conftest.py
    conn_str = os.getenv("AzureWebJobsStorage")
    queue_name = os.getenv("ANALYSIS_QUEUE_NAME")
    reports_container = os.getenv("REPORTS_CONTAINER_NAME")

    # Mock uuid to return a predictable value
    test_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
    mock_uuid4.return_value = test_uuid

    # Mock QueueClient and its methods
    mock_queue_client = Mock()
    mock_queue_client_from_conn_str.return_value = mock_queue_client # Simpler mock setup

    # Create a mock HttpRequest
    req = func.HttpRequest(
        method='POST',
        body=json.dumps(VALID_PAYLOAD_DICT).encode('utf-8'),
        url='/api/analyze',
        headers={'Content-Type': 'application/json'}
    )

    # Act
    response = main(req)

    # Assert
    assert response.status_code == 202
    assert response.mimetype == 'application/json'

    # Check response body
    response_body = json.loads(response.get_body())
    assert "jobId" in response_body
    assert response_body["jobId"] == str(test_uuid)
    StartAnalysisResponse.model_validate(response_body) # Validate response schema

    # Check that update_job_status was called correctly for PENDING
    mock_update_status.assert_called_once_with(
        str(test_uuid),
        JobStatus.PENDING,
        0,
        "Analysis request received and queued."
    )

    # Check that QueueClient was called correctly
    mock_queue_client_from_conn_str.assert_called_once_with(
        conn_str=conn_str,
        queue_name=queue_name,
        message_encode_policy=ANY
    )

    # Check that send_message was called with correct data
    mock_queue_client.send_message.assert_called_once()
    sent_message_str = mock_queue_client.send_message.call_args[0][0]
    sent_message_dict = json.loads(sent_message_str)
    assert sent_message_dict["jobId"] == str(test_uuid)
    assert sent_message_dict["payload"] == VALID_PAYLOAD_DICT # Pydantic ensures consistency here

    # Check that blob client methods were NOT called
    blob_client_from_conftest.upload_blob.assert_not_called()
    blob_client_from_conftest.set_blob_metadata.assert_not_called()

def test_analyze_invalid_json(blob_client_from_conftest):
    """Test request with invalid JSON body."""
    # Arrange
    req = func.HttpRequest(
        method='POST',
        body=b'{invalid json',
        url='/api/analyze',
        headers={'Content-Type': 'application/json'}
    )

    # Act
    response = main(req)

    # Assert
    assert response.status_code == 400
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    assert "message" in response_body
    assert "Please pass a valid JSON object in the request body" in response_body["message"]
    ErrorResponse.model_validate(response_body) # Validate error schema
    # mock_update_status.assert_not_called() # Status should not be updated for invalid JSON
    # Assert blob client methods were NOT called, as status update shouldn't happen
    blob_client_from_conftest.upload_blob.assert_not_called()
    blob_client_from_conftest.set_blob_metadata.assert_not_called()

def test_analyze_validation_error(blob_client_from_conftest):
    """Test request with data that fails Pydantic validation."""
    # Arrange
    invalid_payload = VALID_PAYLOAD_DICT.copy()
    invalid_payload["ndvi_threshold"] = 1.5 # Invalid value

    req = func.HttpRequest(
        method='POST',
        body=json.dumps(invalid_payload).encode('utf-8'),
        url='/api/analyze',
        headers={'Content-Type': 'application/json'}
    )

    # Act
    response = main(req)

    # Assert
    assert response.status_code == 400
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    assert "message" in response_body
    assert "Invalid request body" in response_body["message"]
    assert "details" in response_body
    assert isinstance(response_body["details"], list)
    assert len(response_body["details"]) > 0
    assert "ndvi_threshold" in str(response_body["details"]) # Check that the field name is in details
    ErrorResponse.model_validate(response_body)
    # mock_update_status.assert_not_called() # Status should not be updated for validation errors
    # Assert blob client methods were NOT called
    blob_client_from_conftest.upload_blob.assert_not_called()
    blob_client_from_conftest.set_blob_metadata.assert_not_called()

@patch('functions.AnalyzeFunction.main.update_job_status') # Patch where used
@patch('functions.AnalyzeFunction.main.uuid.uuid4')
@patch('functions.AnalyzeFunction.main.QueueClient.from_connection_string')
def test_analyze_queue_send_error(mock_queue_client_from_conn_str, mock_uuid4, mock_update_status, blob_client_from_conftest):
    """Test scenario where sending message to queue fails."""
     # Arrange
    test_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
    mock_uuid4.return_value = test_uuid

    # Mock QueueClient to raise error on send_message
    mock_queue_client = Mock()
    mock_queue_client.send_message.side_effect = Exception("Queue connection failed")
    mock_queue_client_from_conn_str.return_value = mock_queue_client # Simpler mock setup

    # Note: We don't need blob_client_from_conftest if patching update_job_status directly

    req = func.HttpRequest(
        method='POST',
        body=json.dumps(VALID_PAYLOAD_DICT).encode('utf-8'),
        url='/api/analyze',
        headers={'Content-Type': 'application/json'}
    )

    # Act
    response = main(req)

    # Assert
    # The function now returns 500 on queue send failure after attempting FAILED status update
    assert response.status_code == 500
    response_body = json.loads(response.get_body())
    assert response_body["message"] == "Failed to queue analysis job."
    ErrorResponse.model_validate(response_body)

    # Check that update_job_status was called twice: PENDING then FAILED
    assert mock_update_status.call_count == 2

    # Check the PENDING status update call (first call)
    mock_update_status.assert_has_calls([
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

    # Check the FAILED status update call message more specifically (second call)
    failed_call_args = mock_update_status.call_args_list[1][0] # Get positional args of second call
    assert "Failed to queue job" in failed_call_args[3]
    assert "Queue connection failed" in failed_call_args[3]

def test_analyze_missing_connection_string(monkeypatch):
    """Test scenario where AzureWebJobsStorage is not set *after* initial setup."""
    # Arrange
    # Need to remove the env var *after* the autouse fixture has run
    # Use a separate fixture or direct manipulation within the test
    monkeypatch.delenv("AzureWebJobsStorage", raising=False)

    # We need to reload the modules that read the env var at import time
    # *after* deleting it. Crucially, reload helpers first.
    import functions.shared_code.helpers
    import importlib
    importlib.reload(functions.shared_code.helpers)
    import functions.AnalyzeFunction.main
    importlib.reload(functions.AnalyzeFunction.main)

    req = func.HttpRequest(
        method='POST',
        body=json.dumps(VALID_PAYLOAD_DICT).encode('utf-8'),
        url='/api/analyze',
        headers={'Content-Type': 'application/json'}
    )

    # Act
    response = main(req)

    # Assert
    assert response.status_code == 500
    response_body = json.loads(response.get_body())
    assert "message" in response_body
    # The exact message might depend on where the check fails first
    # It could be in get_blob_service_client or QueueClient creation
    # Check for a general configuration error message
    assert "configuration error" in response_body["message"].lower()
    ErrorResponse.model_validate(response_body)
