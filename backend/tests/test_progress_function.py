import json
import asyncio
import pytest
import azure.functions as func
# Import the correct classes for spec
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient, BlobClient as AsyncBlobClient
from unittest.mock import Mock, patch, AsyncMock # Import AsyncMock
from azure.core.exceptions import ResourceNotFoundError
import pytest_asyncio
import time

from functions.ProgressFunction.main import main, progress_generator
from functions.shared_code.schemas import ErrorResponse, ProgressUpdate, JobStatus

TEST_JOB_ID = "test-job-progress-123"

# --- Fixtures ---
@pytest.fixture
def mock_env_vars(monkeypatch):
    monkeypatch.setenv("AzureWebJobsStorage", "test-conn-string-async")
    monkeypatch.setenv("REPORTS_CONTAINER_NAME", "test-reports-async")
    monkeypatch.setenv("PROGRESS_CHECK_INTERVAL_SECONDS", "0.01") # Speed up tests

@pytest_asyncio.fixture
async def mock_async_blob_service_client(mocker):
    """Mocks the async BlobServiceClient and its methods used by the generator."""
    # Use the imported async classes for spec
    mock_service_client = AsyncMock(spec=AsyncBlobServiceClient)
    mock_blob_client = AsyncMock(spec=AsyncBlobClient)
    mock_downloader = AsyncMock()

    # Correctly mock the async download process
    async def mock_readall():
        # Default content or dynamically set in tests
        default_status = ProgressUpdate(jobId=TEST_JOB_ID, status=JobStatus.PROCESSING, progress=50, message="Mocked readall", timestamp=time.time())
        return json.dumps(default_status.model_dump()).encode('utf-8')
    mock_downloader.readall = mock_readall # Assign the async function directly

    async def mock_download_blob(**kwargs):
        return mock_downloader
    mock_blob_client.download_blob = mock_download_blob

    async def mock_exists():
        # Default to True, can be overridden in tests
        return True
    mock_blob_client.exists = mock_exists

    mock_service_client.get_blob_client.return_value = mock_blob_client
    
    # Patch 'azure.storage.blob.aio.BlobServiceClient.from_connection_string'
    # Needs to be async if used with 'async with'
    mock_from_conn_str = AsyncMock(return_value=mock_service_client)

    with patch('azure.storage.blob.BlobServiceClient.from_connection_string', return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_service_client), __aexit__=AsyncMock(return_value=None))) as mock_fcs:
        yield mock_service_client, mock_blob_client, mock_downloader

# --- Helper to create HttpRequest ---
def create_request(job_id=TEST_JOB_ID):
    route_params = {"jobId": job_id} if job_id else {}
    return func.HttpRequest(
        method='GET',
        url=f'/api/progress/{job_id}' if job_id else '/api/progress',
        route_params=route_params,
        body=None
    )

# --- Helper to collect SSE events from generator ---
async def collect_sse_events(generator, max_events=5, timeout=5):
    events = []
    start_time = time.time()
    try:
        async for event in generator:
            events.append(event)
            if len(events) >= max_events:
                break
            if time.time() - start_time > timeout:
                print(f"SSE collection timed out after {timeout}s")
                break
    except Exception as e:
        print(f"Error during SSE collection: {e}")
    return events

# --- Test Cases ---

@pytest.mark.asyncio
async def test_progress_generator_flow(mock_env_vars, mock_async_blob_service_client):
    """Test the normal flow: PENDING -> PROCESSING -> COMPLETED."""
    mock_service_client, mock_blob_client, mock_downloader = mock_async_blob_service_client

    # Arrange: Simulate blob content changing over time
    call_count_exists = 0
    call_count_download = 0

    async def mock_exists_side_effect():
        nonlocal call_count_exists
        call_count_exists += 1
        if call_count_exists == 1:
            return False # Not found initially
        return True
    mock_blob_client.exists = mock_exists_side_effect

    async def mock_readall_side_effect():
        nonlocal call_count_download
        call_count_download += 1
        if call_count_download == 1:
            # First real check after exists returns True
            status = ProgressUpdate(jobId=TEST_JOB_ID, status=JobStatus.PROCESSING, progress=50, message="Working...", timestamp=time.time()).model_dump()
        elif call_count_download == 2:
             # Second check
            status = ProgressUpdate(jobId=TEST_JOB_ID, status=JobStatus.COMPLETED, progress=100, timestamp=time.time()).model_dump()
        else:
            # Keep returning completed
            status = ProgressUpdate(jobId=TEST_JOB_ID, status=JobStatus.COMPLETED, progress=100, timestamp=time.time()).model_dump()
        return json.dumps(status).encode('utf-8')
    mock_downloader.readall = mock_readall_side_effect # Assign the async side effect

    # Act
    generator = progress_generator(TEST_JOB_ID)
    # Expect 3 events: Initial PENDING (progress), PROCESSING (progress), COMPLETED (complete)
    events = await collect_sse_events(generator, max_events=3)

    # Assert
    assert len(events) == 3
    # Check event types and data
    event1 = json.loads(events[0].split('data: ')[1].strip())
    event2 = json.loads(events[1].split('data: ')[1].strip())
    event3 = json.loads(events[2].split('data: ')[1].strip())
    
    assert events[0].startswith('event: progress')
    assert event1['status'] == JobStatus.PENDING.value
    
    assert events[1].startswith('event: progress')
    assert event2['status'] == JobStatus.PROCESSING.value
    assert event2['progress'] == 50
    
    assert events[2].startswith('event: complete')
    assert event3['status'] == JobStatus.COMPLETED.value
    assert event3['progress'] == 100

@pytest.mark.asyncio
async def test_progress_generator_failed(mock_env_vars, mock_async_blob_service_client):
    """Test the flow ending in FAILED status."""
    mock_service_client, mock_blob_client, mock_downloader = mock_async_blob_service_client

    # Arrange: Simulate blob content changing to FAILED
    call_count_exists = 0
    call_count_download = 0
    async def mock_exists_side_effect():
        nonlocal call_count_exists
        call_count_exists += 1
        return call_count_exists > 1 # Not found on first check
    mock_blob_client.exists = mock_exists_side_effect

    async def mock_readall_side_effect():
        nonlocal call_count_download
        call_count_download += 1
        if call_count_download == 1:
            status = ProgressUpdate(jobId=TEST_JOB_ID, status=JobStatus.PROCESSING, progress=30, timestamp=time.time()).model_dump()
        else:
            status = ProgressUpdate(jobId=TEST_JOB_ID, status=JobStatus.FAILED, progress=-1, message="Error occurred", timestamp=time.time()).model_dump()
        return json.dumps(status).encode('utf-8')
    mock_downloader.readall = mock_readall_side_effect

    # Act
    generator = progress_generator(TEST_JOB_ID)
    # Expect 3 events: Initial PENDING (progress), PROCESSING (progress), FAILED (complete)
    events = await collect_sse_events(generator, max_events=3)

    # Assert
    assert len(events) == 3
    event1 = json.loads(events[0].split('data: ')[1].strip())
    event2 = json.loads(events[1].split('data: ')[1].strip())
    event3 = json.loads(events[2].split('data: ')[1].strip())

    assert events[0].startswith('event: progress')
    assert event1['status'] == JobStatus.PENDING.value

    assert events[1].startswith('event: progress')
    assert event2['status'] == JobStatus.PROCESSING.value
    assert event2['progress'] == 30

    assert events[2].startswith('event: complete')
    assert event3['status'] == JobStatus.FAILED.value
    assert event3['message'] == "Error occurred"

@pytest.mark.asyncio
async def test_progress_generator_check_error(mock_env_vars, mock_async_blob_service_client):
    """Test error during blob download/read after initial PENDING."""
    mock_service_client, mock_blob_client, mock_downloader = mock_async_blob_service_client

    # Arrange: Simulate blob check failing after initial PENDING
    call_count_exists = 0
    async def mock_exists_side_effect():
        nonlocal call_count_exists
        call_count_exists += 1
        if call_count_exists == 1:
             return False # Not found first
        elif call_count_exists == 2:
             return True # Found second time
        else:
            # Simulate error on subsequent check
             raise ConnectionError("Blob storage connection failed") 
    mock_blob_client.exists = mock_exists_side_effect

    # Make download work the first time it's called
    async def mock_readall_side_effect():
         status = ProgressUpdate(jobId=TEST_JOB_ID, status=JobStatus.PROCESSING, progress=10, timestamp=time.time()).model_dump()
         return json.dumps(status).encode('utf-8')
    mock_downloader.readall = mock_readall_side_effect

    # Act
    generator = progress_generator(TEST_JOB_ID)
    # Expect 3 events: Initial PENDING, PROCESSING, then Error
    events = await collect_sse_events(generator, max_events=3)

    # Assert
    assert len(events) == 3 
    event1 = json.loads(events[0].split('data: ')[1].strip())
    event2 = json.loads(events[1].split('data: ')[1].strip())
    event3 = json.loads(events[2].split('data: ')[1].strip())

    assert events[0].startswith('event: progress')
    assert event1['status'] == JobStatus.PENDING.value
    
    assert events[1].startswith('event: progress')
    assert event2['status'] == JobStatus.PROCESSING.value

    assert events[2].startswith('event: error')
    assert "Error streaming job progress" in event3['message']
    assert "ConnectionError" in event3['message']

@pytest.mark.asyncio
@patch('functions.ProgressFunction.main.func.HttpResponse') # Mock HttpResponse
@patch('functions.ProgressFunction.main.progress_generator') # Mock generator too
async def test_progress_main_success(mock_progress_generator, mock_http_response, mock_env_vars, mock_async_blob_service_client):
    """Test the main HTTP handler function initiates SSE stream correctly."""
    mock_service_client, mock_blob_client, mock_downloader = mock_async_blob_service_client
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
