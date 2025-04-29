import json
import asyncio
import pytest
import azure.functions as func
import azure.storage.blob.aio
from unittest.mock import Mock, patch, AsyncMock # Import AsyncMock
from azure.core.exceptions import ResourceNotFoundError

from functions.ProgressFunction.main import main, progress_generator
from functions.shared_code.schemas import ErrorResponse, ProgressUpdate, JobStatus

TEST_JOB_ID = "test-job-progress-123"

# --- Fixtures ---
@pytest.fixture
def mock_env_vars(monkeypatch):
    monkeypatch.setenv("AzureWebJobsStorage", "test-conn-string-async")
    monkeypatch.setenv("REPORTS_CONTAINER_NAME", "test-reports-async")
    monkeypatch.setenv("PROGRESS_CHECK_INTERVAL_SECONDS", "0.01") # Speed up tests

@pytest.fixture
def mock_async_blob_service_client():
    """Fixture to mock the async BlobServiceClient and its subordinates."""
    mock_service_client = AsyncMock(spec=azure.storage.blob.aio.BlobServiceClient)
    mock_blob_client = AsyncMock(spec=azure.storage.blob.aio.BlobClient)

    # Configure async context manager for BlobServiceClient
    mock_service_client.__aenter__.return_value = mock_service_client
    mock_service_client.__aexit__.return_value = None
    mock_service_client.get_blob_client.return_value = mock_blob_client

    # Default behavior (can be overridden)
    mock_blob_client.get_blob_properties = AsyncMock()

    # Patch the constructor
    with patch('functions.ProgressFunction.main.BlobServiceClient.from_connection_string') as mock_constructor:
        mock_constructor.return_value = mock_service_client
        yield mock_service_client, mock_blob_client

# --- Helper to create HttpRequest ---
def create_request(job_id=TEST_JOB_ID):
    return func.HttpRequest(
        method='GET',
        url=f'/api/progress/{job_id}',
        body=None,
        route_params={'jobId': job_id} if job_id else {},
    )

# --- Helper to collect SSE events from generator ---
async def collect_sse_events(generator, max_events=5):
    events = []
    try:
        async for event in generator:
            events.append(event)
            if len(events) >= max_events:
                break
    except Exception as e:
        print(f"Exception during SSE collection: {e}") # For debugging test failures
        # Depending on test, might re-raise or just return collected events
    return events

# --- Test Cases ---

@pytest.mark.asyncio
async def test_progress_generator_flow(mock_env_vars, mock_async_blob_service_client):
    """Test the normal flow: PENDING -> PROCESSING -> COMPLETED."""
    mock_service_client, mock_blob_client = mock_async_blob_service_client

    # Arrange: Simulate blob properties changing over time
    async def mock_get_properties(*args, **kwargs):
        call_count = mock_blob_client.get_blob_properties.call_count
        if call_count == 1:
            # Simulate blob not found initially
            raise ResourceNotFoundError("Blob not found")
        elif call_count == 2:
            # Simulate PROCESSING
            return Mock(metadata={
                "jobStatus": JobStatus.PROCESSING.value,
                "jobProgress": "50",
                "jobMessage": "Working..."
            })
        elif call_count == 3:
            # Simulate COMPLETED
            return Mock(metadata={
                "jobStatus": JobStatus.COMPLETED.value,
                "jobProgress": "100"
            })
        else:
            # Keep returning completed to allow loop termination
            return Mock(metadata={
                "jobStatus": JobStatus.COMPLETED.value,
                "jobProgress": "100"
            })

    mock_blob_client.get_blob_properties.side_effect = mock_get_properties

    # Act
    generator = progress_generator(TEST_JOB_ID)
    # Collect 3 events: PENDING (progress), PROCESSING (progress), COMPLETED (complete)
    events = await collect_sse_events(generator, max_events=3)

    # Assert
    assert len(events) == 3

    # Check PENDING event (event 0)
    assert "event: progress" in events[0]
    data0 = json.loads(events[0].split("data: ")[1])
    ProgressUpdate.model_validate(data0)
    assert data0["status"] == JobStatus.PENDING.value
    assert data0["progress"] == 0

    # Check PROCESSING event (event 1)
    assert "event: progress" in events[1]
    data1 = json.loads(events[1].split("data: ")[1])
    ProgressUpdate.model_validate(data1)
    assert data1["status"] == JobStatus.PROCESSING.value
    assert data1["progress"] == 50
    assert data1["message"] == "Working..."

    # Check final COMPLETED event (sent as event: complete - event 2)
    assert "event: complete" in events[2]
    data2 = json.loads(events[2].split("data: ")[1])
    ProgressUpdate.model_validate(data2)
    assert data2["status"] == JobStatus.COMPLETED.value
    assert data2["progress"] == 100

    # Assert the underlying mock was called enough times
    assert mock_blob_client.get_blob_properties.call_count >= 3

@pytest.mark.asyncio
async def test_progress_generator_failed(mock_env_vars, mock_async_blob_service_client):
    """Test the flow ending in FAILED status."""
    mock_service_client, mock_blob_client = mock_async_blob_service_client

    # Arrange: Simulate blob properties changing to FAILED
    async def mock_get_properties(*args, **kwargs):
        call_count = mock_blob_client.get_blob_properties.call_count
        if call_count == 1: raise ResourceNotFoundError()
        elif call_count == 2: return Mock(metadata={"jobStatus": JobStatus.PROCESSING.value, "jobProgress": "30"})
        else: return Mock(metadata={"jobStatus": JobStatus.FAILED.value, "jobProgress": "-1", "jobMessage": "Error occurred"})
    mock_blob_client.get_blob_properties.side_effect = mock_get_properties

    # Act
    generator = progress_generator(TEST_JOB_ID)
    events = await collect_sse_events(generator, max_events=3)

    # Assert
    assert len(events) == 3
    assert "event: progress" in events[0] and JobStatus.PENDING.value in events[0]
    assert "event: progress" in events[1] and JobStatus.PROCESSING.value in events[1]
    assert "event: complete" in events[2] # Terminal event is 'complete'
    data2 = json.loads(events[2].split("data: ")[1])
    ProgressUpdate.model_validate(data2)
    assert data2["status"] == JobStatus.FAILED.value
    assert data2["message"] == "Error occurred"

@pytest.mark.asyncio
async def test_progress_generator_check_error(mock_env_vars, mock_async_blob_service_client):
    """Test error during blob property check."""
    mock_service_client, mock_blob_client = mock_async_blob_service_client

    # Arrange: Simulate blob check failing after initial PENDING
    async def mock_get_properties(*args, **kwargs):
        if mock_blob_client.get_blob_properties.call_count == 1:
            raise ResourceNotFoundError()
        else:
            raise ConnectionError("Blob storage connection failed")
    mock_blob_client.get_blob_properties.side_effect = mock_get_properties

    # Act
    generator = progress_generator(TEST_JOB_ID)
    events = await collect_sse_events(generator, max_events=2)

    # Assert
    assert len(events) == 2
    assert "event: progress" in events[0] # Initial PENDING
    assert "event: error" in events[1]
    data1 = json.loads(events[1].split("data: ")[1])
    ErrorResponse.model_validate(data1)
    assert "Error checking job progress" in data1["message"]

@pytest.mark.asyncio
@patch('functions.ProgressFunction.main.func.HttpResponse') # Mock HttpResponse
@patch('functions.ProgressFunction.main.progress_generator') # Mock generator too
async def test_progress_main_success(mock_progress_generator, mock_http_response, mock_env_vars, mock_async_blob_service_client):
    """Test the main HTTP handler function initiates SSE stream correctly."""
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    req = create_request()
    # mock_progress_generator is already mocked by the decorator

    response = await main(req) # Add await

    # Assert: Check HttpResponse was called with correct args
    mock_http_response.assert_called_once()
    call_args = mock_http_response.call_args[1] # Get kwargs
    assert call_args['status_code'] == 200
    assert call_args['mimetype'] == 'text/event-stream'
    assert 'Content-Type' in call_args['headers']
    assert call_args['headers']['Content-Type'] == 'text/event-stream'
    # Check that the body is the result of calling the (mocked) generator
    mock_progress_generator.assert_called_once_with(TEST_JOB_ID)
    assert call_args['body'] == mock_progress_generator.return_value

@pytest.mark.asyncio # Keep decorator for potential async fixtures
@patch('functions.ProgressFunction.main.func.HttpResponse') # Mock HttpResponse here too
async def test_progress_main_no_jobid(mock_http_response, mock_env_vars):
    """Test the main handler when jobId is missing."""
    req = create_request(job_id=None)
    response = await main(req) # Add await

    # Assert HttpResponse called with 400 status and JSON body
    mock_http_response.assert_called_once()
    call_args_pos = mock_http_response.call_args[0] # Get positional args
    call_args_kw = mock_http_response.call_args[1] # Get keyword args
    assert call_args_kw['status_code'] == 400
    assert call_args_kw['mimetype'] == 'application/json'
    body_json = json.loads(call_args_pos[0]) # Body is the first positional arg
    assert "Please provide a jobId in the path" in body_json["message"]
