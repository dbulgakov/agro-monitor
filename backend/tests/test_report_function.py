import json
import pytest
import azure.functions as func
from unittest.mock import Mock, patch
from azure.core.exceptions import ResourceNotFoundError
from pydantic import ValidationError

from functions.ReportFunction.main import main
from functions.shared_code.schemas import ReportData, ErrorResponse, JobStatus

TEST_JOB_ID = "test-job-123"

# --- Fixtures ---
@pytest.fixture
def mock_env_vars(monkeypatch):
    monkeypatch.setenv("AzureWebJobsStorage", "test-conn-string")
    monkeypatch.setenv("REPORTS_CONTAINER_NAME", "test-reports")

@pytest.fixture
def mock_blob_service_client():
    """Fixture to mock the BlobServiceClient and its subordinate clients/methods."""
    mock_service_client = Mock()
    mock_container_client = Mock()
    mock_blob_client = Mock()

    # Chain the mocks
    mock_service_client.get_blob_client.return_value = mock_blob_client
    # Note: In the code we use get_blob_client directly from service_client
    # mock_service_client.get_container_client.return_value = mock_container_client
    # mock_container_client.get_blob_client.return_value = mock_blob_client

    # Default behavior (can be overridden in tests)
    mock_blob_client.exists.return_value = True
    mock_blob_client.get_blob_properties.return_value = Mock(metadata={})
    mock_blob_client.download_blob.return_value = Mock(readall=lambda: b'{}')

    # Patch the constructor
    with patch('functions.ReportFunction.main.BlobServiceClient.from_connection_string') as mock_constructor:
        mock_constructor.return_value = mock_service_client
        yield mock_service_client, mock_blob_client # Yield mocks for test access

# --- Helper to create HttpRequest ---
def create_request(job_id=TEST_JOB_ID):
    return func.HttpRequest(
        method='GET',
        url=f'/api/report/{job_id}',
        body=None, # Add body=None for GET requests
        route_params={'jobId': job_id} if job_id else {},
    )

# --- Test Cases ---

def test_get_report_success_completed(mock_env_vars, mock_blob_service_client):
    """Test getting a completed report successfully."""
    mock_service_client, mock_blob_client = mock_blob_service_client

    # Arrange
    report_content = {
        "jobId": TEST_JOB_ID,
        "status": "COMPLETED",
        "summary": "AI summary",
        "snapshotImageUrl": "http://example.com/rgb.png",
        "ndviImageUrl": "http://example.com/ndvi.png",
        "stressZoneImageUrl": "http://example.com/stress.png"
    }
    mock_blob_client.exists.return_value = True
    mock_blob_client.get_blob_properties.return_value = Mock(metadata={
        "jobStatus": JobStatus.COMPLETED.value,
        "jobProgress": "100"
    })
    mock_blob_client.download_blob.return_value = Mock(readall=lambda: json.dumps(report_content).encode('utf-8'))

    req = create_request()

    # Act
    response = main(req)

    # Assert
    assert response.status_code == 200
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    validated_report = ReportData.model_validate(response_body)
    assert validated_report.jobId == TEST_JOB_ID
    assert validated_report.status == JobStatus.COMPLETED
    assert validated_report.summary == "AI summary"
    assert validated_report.snapshotImageUrl == "http://example.com/rgb.png"

    mock_service_client.get_blob_client.assert_called_once_with(container="test-reports", blob=f"{TEST_JOB_ID}.json")
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
    metadata = {"jobStatus": metadata_status, "jobProgress": "50"} if metadata_status else {}
    mock_blob_client.get_blob_properties.return_value = Mock(metadata=metadata)

    req = create_request()

    # Act
    response = main(req)

    # Assert
    assert response.status_code == 200 # Still returns 200, client checks status field
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    validated_partial_report = ReportData.model_validate(response_body)
    assert validated_partial_report.jobId == TEST_JOB_ID
    assert validated_partial_report.status == expected_status_enum
    assert validated_partial_report.summary is None # Other fields should be None
    assert validated_partial_report.snapshotImageUrl is None

    mock_blob_client.exists.assert_called_once()
    mock_blob_client.get_blob_properties.assert_called_once()
    mock_blob_client.download_blob.assert_not_called() # Should not download full report

def test_get_report_not_found(mock_env_vars, mock_blob_service_client):
    """Test getting a report where the blob does not exist."""
    mock_service_client, mock_blob_client = mock_blob_service_client

    # Arrange
    mock_blob_client.exists.return_value = False
    req = create_request()

    # Act
    response = main(req)

    # Assert
    assert response.status_code == 404
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    assert "message" in response_body
    assert f"Report not found for Job ID: {TEST_JOB_ID}" in response_body["message"]
    ErrorResponse.model_validate(response_body)

    mock_blob_client.exists.assert_called_once()
    mock_blob_client.get_blob_properties.assert_not_called()
    mock_blob_client.download_blob.assert_not_called()

def test_get_report_metadata_fails_but_download_succeeds(mock_env_vars, mock_blob_service_client):
    """Test scenario where metadata read fails, but download (of presumed completed) succeeds."""
    mock_service_client, mock_blob_client = mock_blob_service_client

    # Arrange
    report_content = {
        "jobId": TEST_JOB_ID,
        "status": "COMPLETED", # Status in body is COMPLETED
        "summary": "AI summary",
    }
    mock_blob_client.exists.return_value = True
    # Simulate failure during property read, but allow download
    mock_blob_client.get_blob_properties.side_effect = Exception("Failed to read props")
    mock_blob_client.download_blob.return_value = Mock(readall=lambda: json.dumps(report_content).encode('utf-8'))

    req = create_request()

    # Act
    response = main(req)

    # Assert
    # Because status check failed, it defaults to PENDING and returns partial state
    assert response.status_code == 202
    response_body = json.loads(response.get_body())
    validated_partial_report = ReportData.model_validate(response_body)
    assert validated_partial_report.jobId == TEST_JOB_ID
    assert validated_partial_report.status == JobStatus.PENDING
    assert validated_partial_report.summary is None
    mock_blob_client.download_blob.assert_not_called() # Does not download if metadata fails

def test_get_report_completed_invalid_content(mock_env_vars, mock_blob_service_client):
    """Test case where report is COMPLETED but content fails validation."""
    mock_service_client, mock_blob_client = mock_blob_service_client
    invalid_report_content = {
        "jobId": TEST_JOB_ID,
        "status": "COMPLETED",
        "summary": 123,
    }
    mock_blob_client.exists.return_value = True
    mock_blob_client.get_blob_properties.return_value = Mock(metadata={
        "jobStatus": JobStatus.COMPLETED.value,
        "jobProgress": "100"
    })
    mock_blob_client.download_blob.return_value = Mock(readall=lambda: json.dumps(invalid_report_content).encode('utf-8'))
    req = create_request()
    response = main(req)
    # Assert
    assert response.status_code == 500
    response_body = json.loads(response.get_body())
    assert "Failed to parse completed report data" in response_body["message"]
    assert "details" in response_body
    ErrorResponse.model_validate(response_body)

def test_get_report_no_jobid(mock_env_vars):
    """Test request without providing jobId in the path."""
    # Arrange
    req = create_request(job_id=None)

    # Act
    response = main(req)

    # Assert
    assert response.status_code == 400
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    assert "Please provide a job ID in the URL path" in response_body["message"]
    ErrorResponse.model_validate(response_body)

def test_get_report_missing_connection_string(monkeypatch):
    """Test scenario where AzureWebJobsStorage is not set."""
    # Arrange
    monkeypatch.delenv("AzureWebJobsStorage", raising=False)
    monkeypatch.setenv("REPORTS_CONTAINER_NAME", "test-reports")
    req = create_request()

    # Act
    response = main(req)

    # Assert
    assert response.status_code == 503
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body())
    assert "Internal server configuration error" in response_body["message"]
    ErrorResponse.model_validate(response_body)
