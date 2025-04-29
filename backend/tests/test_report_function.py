import json
import pytest
import azure.functions as func
from unittest.mock import Mock, patch
import os
from azure.storage.blob import BlobProperties, BlobClient, BlobServiceClient

from functions.ReportFunction import main as report_function_main
from functions.shared_code.schemas import ReportData, ErrorResponse, JobStatus

TEST_JOB_ID = "test-job-123"
REPORTS_CONTAINER_NAME = os.getenv("REPORTS_CONTAINER_NAME", "test-reports")

# --- Fixtures ---
@pytest.fixture
def mock_env_vars(monkeypatch):
    monkeypatch.setenv("AzureWebJobsStorage", "test-conn-string")
    monkeypatch.setenv("REPORTS_CONTAINER_NAME", REPORTS_CONTAINER_NAME)

@pytest.fixture
def mock_blob_service_client(mocker):
    """Fixture to mock the BlobServiceClient and its subordinate clients/methods."""
    mock_service_client = mocker.Mock(spec=BlobServiceClient)
    mock_blob_client = mocker.Mock(spec=BlobClient)

    # Chain the mocks
    mock_service_client.get_blob_client.return_value = mock_blob_client
    # Note: In the code we use get_blob_client directly from service_client
    # mock_service_client.get_container_client.return_value = mock_container_client
    # mock_container_client.get_blob_client.return_value = mock_blob_client

    # Default behavior (can be overridden in tests)
    mock_blob_client.exists.return_value = True
    mock_properties = BlobProperties(name=f"{TEST_JOB_ID}.json", container=REPORTS_CONTAINER_NAME, metadata={})
    mock_blob_client.get_blob_properties.return_value = mock_properties
    mock_download = Mock()
    # Define default content locally here if needed, or configure in tests
    default_report_content = {
        "jobId": TEST_JOB_ID,
        "status": "COMPLETED",
        "recommendations": "Default AI recommendations.",
        "reportTimestamp": "2024-01-01T00:00:00Z",
        # Add other required fields with default values if needed by ReportData
        "requestPayload": None,
        "ndviStatistics": None,
        "mapUrls": None,
        "errorMessage": None
    }
    mock_download.readall.return_value = json.dumps(default_report_content).encode('utf-8')
    mock_blob_client.download_blob.return_value = mock_download

    # Patch the constructor
    with patch('functions.ReportFunction.main.BlobServiceClient.from_connection_string') as mock_constructor:
        mock_constructor.return_value = mock_service_client
        yield mock_service_client, mock_blob_client # Yield mocks for test access

# --- Helper to create HttpRequest ---
def create_request(job_id=TEST_JOB_ID):
    return func.HttpRequest(
        method='GET',
        url=f'/api/report?jobId={job_id}',
        params={"jobId": job_id},
        body=None, # Add body=None for GET requests
    )

# --- Test Cases ---

def test_get_report_success_completed(mock_env_vars, mock_blob_service_client):
    """Test getting a completed report successfully."""
    mock_service_client, mock_blob_client = mock_blob_service_client

    # Arrange
    mock_properties = BlobProperties(name=f"{TEST_JOB_ID}.json", container=REPORTS_CONTAINER_NAME, metadata={
        "jobStatus": JobStatus.COMPLETED.value,
        "jobProgress": "100"
    })
    mock_blob_client.get_blob_properties.return_value = mock_properties
    mock_blob_client.exists.return_value = True

    # Define expected content based on the mock in the fixture
    expected_recommendations = "Default AI recommendations."
    expected_timestamp = "2024-01-01T00:00:00Z"

    req = create_request()

    # Act
    response = report_function_main.main(req)

    # Assert
    assert response.status_code == 200
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    validated_report = ReportData.model_validate(response_body)
    assert validated_report.jobId == TEST_JOB_ID
    assert validated_report.status == JobStatus.COMPLETED
    # Assert against the expected values defined above
    assert validated_report.recommendations == expected_recommendations
    assert validated_report.reportTimestamp == expected_timestamp
    # Optional: Add assertions for other fields if needed and if defaults are known
    # assert validated_report.ndviStatistics is not None
    # assert validated_report.mapUrls is not None
    # assert validated_report.requestPayload is not None

    mock_service_client.get_blob_client.assert_called_once_with(container=REPORTS_CONTAINER_NAME, blob=f"{TEST_JOB_ID}.json")
    mock_blob_client.exists.assert_called_once()
    mock_blob_client.get_blob_properties.assert_called_once()
    mock_blob_client.download_blob.assert_called_once()

@pytest.mark.parametrize(
    "metadata_status, expected_status_enum",
    [
        (JobStatus.PENDING.value, JobStatus.PENDING),
        (JobStatus.PROCESSING.value, JobStatus.PROCESSING),
        (JobStatus.FAILED.value, JobStatus.FAILED),
        ("INVALID_STATUS", JobStatus.PENDING),
        (None, JobStatus.PENDING)
    ]
)
def test_get_report_not_completed(mock_env_vars, mock_blob_service_client, metadata_status, expected_status_enum):
    """Test getting a report that is not yet completed (PENDING, PROCESSING, FAILED)."""
    mock_service_client, mock_blob_client = mock_blob_service_client

    # Arrange
    mock_blob_client.exists.return_value = True
    # Add message for failed case
    message = "Job failed due to error." if expected_status_enum == JobStatus.FAILED else None
    metadata = {"jobStatus": metadata_status, "jobProgress": "50", "jobMessage": message} if metadata_status else {}
    mock_properties = BlobProperties(name=f"{TEST_JOB_ID}.json", container=REPORTS_CONTAINER_NAME, metadata=metadata)
    mock_blob_client.get_blob_properties.return_value = mock_properties

    req = create_request()

    # Act
    response = report_function_main.main(req)

    # Assert
    # Use 202 for PENDING/PROCESSING, 200 for FAILED
    expected_http_status = 202 if expected_status_enum in [JobStatus.PENDING, JobStatus.PROCESSING] else 200
    assert response.status_code == expected_http_status
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    validated_partial_report = ReportData.model_validate(response_body)
    assert validated_partial_report.jobId == TEST_JOB_ID
    assert validated_partial_report.status == expected_status_enum
    assert validated_partial_report.recommendations is None
    assert validated_partial_report.reportTimestamp is None
    assert validated_partial_report.ndviStatistics is None
    assert validated_partial_report.mapUrls is None
    assert validated_partial_report.requestPayload is None
    # Check error message only if failed
    if expected_status_enum == JobStatus.FAILED:
        assert validated_partial_report.errorMessage == message
    else:
        assert validated_partial_report.errorMessage is None

    mock_blob_client.exists.assert_called_once()
    mock_blob_client.get_blob_properties.assert_called_once()
    mock_blob_client.download_blob.assert_not_called()

def test_get_report_not_found(mock_env_vars, mock_blob_service_client):
    """Test getting a report where the blob does not exist."""
    mock_service_client, mock_blob_client = mock_blob_service_client

    # Arrange
    mock_blob_client.exists.return_value = False
    req = create_request()

    # Act
    response = report_function_main.main(req)

    # Assert
    assert response.status_code == 404
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    assert "not found" in response_body['message'].lower()

def test_get_report_metadata_read_fails(mock_env_vars, mock_blob_service_client):
    """Test scenario where checking blob existence fails (e.g., storage error)."""
    mock_service_client, mock_blob_client = mock_blob_service_client
    mock_blob_client.exists.side_effect = Exception("Storage connection error")

    req = create_request()
    response = report_function_main.main(req)

    assert response.status_code == 500
    response_body = json.loads(response.get_body())
    assert "Failed to retrieve report status or content" in response_body['message']
    assert "Storage connection error" in response_body['details']

def test_get_report_properties_read_fails(mock_env_vars, mock_blob_service_client):
    """Test scenario where blob exists but reading properties fails."""
    mock_service_client, mock_blob_client = mock_blob_service_client
    mock_blob_client.exists.return_value = True
    mock_blob_client.get_blob_properties.side_effect = Exception("Failed to read props")

    req = create_request()
    response = report_function_main.main(req)

    assert response.status_code == 500
    response_body = json.loads(response.get_body())
    assert "Failed to retrieve report status or content" in response_body['message']
    assert "Failed to read props" in response_body['details']
    mock_blob_client.download_blob.assert_not_called()

def test_get_report_completed_download_fails(mock_env_vars, mock_blob_service_client):
    """Test case where status is COMPLETED but blob download fails."""
    mock_service_client, mock_blob_client = mock_blob_service_client
    mock_blob_client.exists.return_value = True
    mock_properties = BlobProperties(name=f"{TEST_JOB_ID}.json", container=REPORTS_CONTAINER_NAME, metadata={
        "jobStatus": JobStatus.COMPLETED.value,
        "jobProgress": "100"
    })
    mock_blob_client.get_blob_properties.return_value = mock_properties
    mock_blob_client.download_blob.side_effect = Exception("Download error")

    req = create_request()
    response = report_function_main.main(req)

    assert response.status_code == 500
    response_body = json.loads(response.get_body())
    assert "Failed to download report content" in response_body['message']
    assert "Download error" in response_body['details']

def test_get_report_completed_invalid_content(mock_env_vars, mock_blob_service_client):
    """Test case where report is COMPLETED but content fails validation."""
    mock_service_client, mock_blob_client = mock_blob_service_client
    invalid_report_content = {
        "jobId": TEST_JOB_ID,
        "status": "COMPLETED",
        "recommendations": 123, # Invalid type for recommendations
    }
    mock_blob_client.exists.return_value = True
    mock_properties = BlobProperties(name=f"{TEST_JOB_ID}.json", container=REPORTS_CONTAINER_NAME, metadata={
        "jobStatus": JobStatus.COMPLETED.value,
        "jobProgress": "100"
    })
    mock_blob_client.get_blob_properties.return_value = mock_properties
    mock_download = Mock()
    mock_download.readall.return_value = json.dumps(invalid_report_content).encode('utf-8')
    mock_blob_client.download_blob.return_value = mock_download

    req = create_request()
    response = report_function_main.main(req)

    assert response.status_code == 500
    response_body = json.loads(response.get_body())
    assert "Failed to validate report content" in response_body['message']
    # Check field name in error details instead of generic message
    assert response_body['details'][0]["loc"][0] == "recommendations"

def test_get_report_no_job_id(mock_env_vars):
    """Test request without jobId query parameter."""
    req = create_request(job_id=None) # Create request without jobId
    response = report_function_main.main(req)
    assert response.status_code == 400
    response_body = json.loads(response.get_body())
    # Assert the new error message
    assert "jobId parameter is required" in response_body['message']

def test_get_report_missing_connection_string(monkeypatch):
    """Test scenario where AzureWebJobsStorage is not set."""
    # Arrange
    monkeypatch.delenv("AzureWebJobsStorage", raising=False)
    monkeypatch.setenv("REPORTS_CONTAINER_NAME", "test-reports")
    req = create_request()

    # Act
    response = report_function_main.main(req)

    # Assert
    assert response.status_code == 503
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    assert "Internal server configuration error" in response_body["message"]
    ErrorResponse.model_validate(response_body)
