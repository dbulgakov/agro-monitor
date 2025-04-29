import json
import uuid
import pytest
import azure.functions as func
from unittest.mock import Mock, patch # Use unittest.mock or install pytest-mock

# Import the main function from the specific function directory
from functions.AnalyzeFunction.main import main
from functions.shared_code.schemas import StartAnalysisResponse, ErrorResponse

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

# --- Fixtures (Optional but helpful) ---
@pytest.fixture
def mock_env_vars(monkeypatch):
    """Fixture to set environment variables for tests."""
    monkeypatch.setenv("AzureWebJobsStorage", "test-connection-string")
    monkeypatch.setenv("ANALYSIS_QUEUE_NAME", "test-analysis-queue")

# --- Test Cases ---

@patch('functions.AnalyzeFunction.main.uuid.uuid4')
@patch('functions.AnalyzeFunction.main.QueueClient.from_connection_string')
def test_analyze_success(mock_queue_client_from_conn_str, mock_uuid4, mock_env_vars):
    """Test successful analysis request."""
    # Arrange
    # Mock uuid to return a predictable value
    test_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
    mock_uuid4.return_value = test_uuid

    # Mock QueueClient and its methods
    mock_queue_client = Mock()
    mock_queue_client_from_conn_str.return_value = mock_queue_client

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
    response_body = response.get_json()
    assert "jobId" in response_body
    assert response_body["jobId"] == str(test_uuid)
    StartAnalysisResponse.model_validate(response_body) # Validate response schema

    # Check that QueueClient was called correctly
    mock_queue_client_from_conn_str.assert_called_once_with(
        conn_str="test-connection-string",
        queue_name="test-analysis-queue",
        message_encode_policy=mock_queue_client_from_conn_str.call_args.kwargs['message_encode_policy'] # Check policy type if needed
    )

    # Check that send_message was called with correct data
    mock_queue_client.send_message.assert_called_once()
    sent_message_str = mock_queue_client.send_message.call_args[0][0]
    sent_message_dict = json.loads(sent_message_str)
    assert sent_message_dict["jobId"] == str(test_uuid)
    assert sent_message_dict["payload"] == VALID_PAYLOAD_DICT # Pydantic ensures consistency here

def test_analyze_invalid_json(mock_env_vars):
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
    response_body = response.get_json()
    assert "message" in response_body
    assert "valid JSON" in response_body["message"]
    ErrorResponse.model_validate(response_body) # Validate error schema

def test_analyze_validation_error(mock_env_vars):
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
    response_body = response.get_json()
    assert "message" in response_body
    assert "Invalid request body" in response_body["message"]
    assert "details" in response_body
    assert isinstance(response_body["details"], list)
    assert len(response_body["details"]) > 0
    assert "ndvi_threshold" in str(response_body["details"])
    ErrorResponse.model_validate(response_body)

@patch('functions.AnalyzeFunction.main.QueueClient.from_connection_string')
def test_analyze_queue_send_error(mock_queue_client_from_conn_str, mock_env_vars):
    """Test scenario where sending message to queue fails."""
    # Arrange
    mock_queue_client = Mock()
    mock_queue_client.send_message.side_effect = Exception("Queue connection failed")
    mock_queue_client_from_conn_str.return_value = mock_queue_client

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
    assert response.mimetype == 'application/json'
    response_body = response.get_json()
    assert "message" in response_body
    assert "Failed to queue analysis job" in response_body["message"]
    ErrorResponse.model_validate(response_body)

def test_analyze_missing_connection_string(monkeypatch):
    """Test scenario where AzureWebJobsStorage is not set."""
    # Arrange
    monkeypatch.delenv("AzureWebJobsStorage", raising=False)
    monkeypatch.setenv("ANALYSIS_QUEUE_NAME", "test-analysis-queue")

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
    assert response.mimetype == 'application/json'
    response_body = response.get_json()
    assert "message" in response_body
    assert "Internal server configuration error" in response_body["message"]
    ErrorResponse.model_validate(response_body)
