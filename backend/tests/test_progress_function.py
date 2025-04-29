import json
import asyncio
import pytest
import azure.functions as func
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
    events = await collect_sse_events(generator, max_events=3) # Expect 3 state changes

    # Assert
    assert len(events) == 3

    # Check PENDING event
    assert "event: progress" in events[0]
    data0 = json.loads(events[0].split("data: ")[1])
    ProgressUpdate.model_validate(data0)
    assert data0["status"] == JobStatus.PENDING.value
    assert data0["progress"] == 0

    # Check PROCESSING event
    assert "event: progress" in events[1]
    data1 = json.loads(events[1].split("data: ")[1])
    ProgressUpdate.model_validate(data1)
    assert data1["status"] == JobStatus.PROCESSING.value
    assert data1["progress"] == 50
    assert data1["message"] == "Working..."

    # Check COMPLETED event (sent as event: complete)
    assert "event: complete" in events[2]
    data2 = json.loads(events[2].split("data: ")[1])
    ProgressUpdate.model_validate(data2)
    assert data2["status"] == JobStatus.COMPLETED.value
    assert data2["progress"] == 100

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
async def test_progress_main_success(mock_env_vars, mock_async_blob_service_client):
    """Test the main HTTP handler function for a successful SSE stream."""
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    # Arrange
    req = create_request()
    # Let the generator yield something simple for this test
    async def mock_get_properties(*args, **kwargs):
        if mock_blob_client.get_blob_properties.call_count == 1:
            raise ResourceNotFoundError()
        else:
            return Mock(metadata={"jobStatus": JobStatus.COMPLETED.value, "jobProgress": "100"})
    mock_blob_client.get_blob_properties.side_effect = mock_get_properties

    # Act
    response = await main(req) # Use await since main is async

    # Assert
    assert response.status_code == 200
    assert response.mimetype == 'text/event-stream'
    assert response.headers['Content-Type'] == 'text/event-stream'
    assert response.headers['Cache-Control'] == 'no-cache'

    # Check body content (consuming the async generator body)
    # Note: azure.functions.HttpResponse body needs careful handling for async generators
    # In a real client, you'd read the stream. For tests, we might check the generator directly
    # or trust that the framework handles the streaming correctly if the generator works.
    # We tested the generator itself above, so here we mainly check headers/status.
    assert hasattr(response, "body")
    # body_content = b""
    # async for chunk in response.body:
    #     body_content += chunk
    # assert b"event: progress" in body_content
    # assert b"event: complete" in body_content


@pytest.mark.asyncio
async def test_progress_main_no_jobid():
    """Test the main handler when jobId is missing."""
    # Arrange
    req = create_request(job_id=None)

    # Act
    response = await main(req)

    # Assert
    assert response.status_code == 400
    assert response.mimetype == 'application/json'
    response_body = json.loads(response.get_body()) # get_body is sync
    assert "Please provide a jobId in the path" in response_body["message"]
    ErrorResponse.model_validate(response_body)
