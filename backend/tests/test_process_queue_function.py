import json
import io
import pytest
import asyncio # Import asyncio
import numpy as np
import azure.functions as func
import rasterio # Add import
import matplotlib.pyplot as plt # Import plt
# Import AsyncMock
from unittest.mock import Mock, patch, ANY, call, MagicMock, AsyncMock
# Remove ThreadPoolExecutor

# Change import to be relative to project root
from functions.ProcessQueueFunction.main import main
# Import necessary schemas and JobStatus
from functions.shared_code.schemas import StartAnalysisPayload, JobStatus, ReportData
from functions.shared_code import helpers # Import the module to patch its functions

# --- Constants & Test Data ---
TEST_JOB_ID = "process-test-job-789"

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

QUEUE_MESSAGE_BODY = json.dumps({
    "jobId": TEST_JOB_ID,
    "payload": VALID_PAYLOAD_DICT
}).encode('utf-8')

# --- Fixtures ---
@pytest.fixture
def mock_queue_message():
    """Creates a mock QueueMessage."""
    return func.QueueMessage(id='test-msg-id', body=QUEUE_MESSAGE_BODY, pop_receipt='test-receipt')

# Mock the new async helper get_sentinel2_urls
@pytest.fixture
def mock_get_sentinel2_urls(mocker):
    """Mocks the async get_sentinel2_urls helper."""
    mock_func = mocker.patch(
        'functions.ProcessQueueFunction.main.get_sentinel2_urls',
        new_callable=AsyncMock
    )
    # Configure default success return value
    mock_func.return_value = {
        "red": "red_href_signed",
        "nir": "nir_href_signed",
        "visual": "rgb_href_signed"
        # Add date if main uses it:
        # "selected_date": "2024-03-15"
    }
    return mock_func

# Remove ThreadPoolExecutor mock fixture
# @pytest.fixture
# def mock_thread_pool(mocker):
#     ...

@pytest.fixture
def mock_shared_helpers_async(mocker):
    """Mocks async helper functions from shared_code.helpers.

    Patches the functions within the scope where they are imported/used
    in ProcessQueueFunction/main.py.
    """
    # Mock async helpers with AsyncMock, targeting where they are imported in main
    mock_update_status = mocker.patch('functions.ProcessQueueFunction.main.update_job_status', new_callable=AsyncMock)
    # upload_image_to_blob is imported directly in main, patch it there
    mock_upload_image = mocker.patch('functions.ProcessQueueFunction.main.upload_image_to_blob', new_callable=AsyncMock, return_value="mock_image_url")
    mock_upload_report = mocker.patch('functions.ProcessQueueFunction.main.upload_report_to_blob', new_callable=AsyncMock)
    mock_generate_recs = mocker.patch('functions.ProcessQueueFunction.main.generate_openai_recommendations', new_callable=AsyncMock, return_value="Mock AI recs")

    # Mock read_band and read_rgb where they are imported in main
    mock_read_band = mocker.patch('functions.ProcessQueueFunction.main.read_band', new_callable=AsyncMock)
    mock_read_rgb = mocker.patch('functions.ProcessQueueFunction.main.read_rgb', new_callable=AsyncMock)

    # Set default return values for band reads
    mock_read_band.return_value = np.array([[100, 110], [120, 130]], dtype=np.float32)
    mock_read_rgb.return_value = np.array([[[1,2,3]],[[4,5,6]]], dtype=np.float32) # Shape (H, W, C)? Check main usage

    # Mock normalize_image (still synchronous)
    mock_normalize = mocker.patch('functions.ProcessQueueFunction.main.normalize_image', side_effect=lambda job_id, img: img / 255.0)

    return {
        "update_status": mock_update_status,
        "upload_image": mock_upload_image,
        "upload_report": mock_upload_report,
        "generate_recs": mock_generate_recs,
        "read_band": mock_read_band,
        "read_rgb": mock_read_rgb,
        "normalize_image": mock_normalize
    }

@pytest.fixture
def mock_matplotlib(mocker):
    """Mocks matplotlib plotting."""
    mocker.patch('functions.ProcessQueueFunction.main.plt.style.use')
    mocker.patch('functions.ProcessQueueFunction.main.plt.subplots', return_value=(Mock(), Mock()))
    mocker.patch('functions.ProcessQueueFunction.main.plt.savefig')
    mocker.patch('functions.ProcessQueueFunction.main.plt.close')
    mocker.patch('functions.ProcessQueueFunction.main.plt.tight_layout')

@pytest.fixture
def mock_env_vars(mocker):
    """Mocks environment variables."""
    # Mock check_environment_variables to succeed
    mocker.patch('functions.ProcessQueueFunction.main.check_environment_variables', return_value=None)
    # Still mock getenv for other potential uses
    mocker.patch('functions.ProcessQueueFunction.main.os.getenv', side_effect=lambda key, default=None: f"test_{key}" or default)

# --- Test Cases (now async) ---

@pytest.mark.asyncio
async def test_process_queue_success(
    mock_queue_message,
    mock_get_sentinel2_urls, # Use new fixture
    # Removed mock_thread_pool
    mock_shared_helpers_async, # Use async fixture
    mock_matplotlib,
    mock_env_vars
):
    """Test successful processing of a queue message (async)."""
    # Arrange (mocks are arranged by fixtures)
    # Set specific return values if needed for clarity
    mock_shared_helpers_async["read_band"].return_value = np.array([[100, 110], [120, 130]], dtype=np.float32)
    mock_shared_helpers_async["read_rgb"].return_value = np.array([[[1,2,3]],[[4,5,6]]], dtype=np.float32)
    mock_shared_helpers_async["upload_image"].return_value = "mock_image_url"
    mock_shared_helpers_async["generate_recs"].return_value = "Mock AI recs"

    # Act
    await main(mock_queue_message) # Await the main async function

    # Assert
    # Check status updates (now awaited)
    # Note: placeholder date needs updating if possible
    expected_status_calls = [
        call(TEST_JOB_ID, JobStatus.PROCESSING, 5, "Starting analysis"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 10, "Searching for satellite images"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 20, "Downloading image data from Selected Date"), # Placeholder date
        call(TEST_JOB_ID, JobStatus.PROCESSING, 40, "Calculating NDVI and generating images"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 70, "Uploading images"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 85, "Generating recommendations"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 95, "Saving final report"),
        call(TEST_JOB_ID, JobStatus.COMPLETED, 100, "Analysis complete"),
    ]
    mock_shared_helpers_async["update_status"].assert_has_awaits(expected_status_calls)

    # Check get_sentinel2_urls call
    mock_get_sentinel2_urls.assert_awaited_once()

    # Check band reads (awaited)
    mock_shared_helpers_async["read_band"].assert_has_awaits([
        call(TEST_JOB_ID, "red_href_signed"),
        call(TEST_JOB_ID, "nir_href_signed")
    ])
    mock_shared_helpers_async["read_rgb"].assert_awaited_once_with(TEST_JOB_ID, "rgb_href_signed")

    # Check normalization (still sync)
    mock_shared_helpers_async["normalize_image"].assert_called_once()

    # Check plotting calls (still sync)
    assert plt.subplots.call_count == 3
    assert plt.savefig.call_count == 3
    assert plt.close.call_count == 3

    # Check image uploads (awaited)
    expected_upload_calls = [
        call(TEST_JOB_ID, ANY, "satellite_rgb.png"),
        call(TEST_JOB_ID, ANY, "ndvi.png"),
        call(TEST_JOB_ID, ANY, "stress_mask.png")
    ]
    mock_shared_helpers_async["upload_image"].assert_has_awaits(expected_upload_calls, any_order=True)
    assert mock_shared_helpers_async["upload_image"].await_count == 3

    # Check OpenAI call (awaited)
    mock_shared_helpers_async["generate_recs"].assert_awaited_once()

    # Check final report upload (awaited)
    mock_shared_helpers_async["upload_report"].assert_awaited_once()
    # Args are (job_id, report_data)
    final_report_data = mock_shared_helpers_async["upload_report"].await_args[0][1]
    assert isinstance(final_report_data, ReportData)
    assert final_report_data.jobId == TEST_JOB_ID
    assert final_report_data.status == JobStatus.COMPLETED
    # Check fields based on new structure in main.py
    assert final_report_data.recommendations == "Mock AI recs"
    assert final_report_data.mapUrls["satellite_rgb"] == "mock_image_url"

    # Check final status update is still COMPLETED
    mock_shared_helpers_async["update_status"].assert_awaited_with(TEST_JOB_ID, JobStatus.COMPLETED, 100, "Analysis complete")

@pytest.mark.asyncio
async def test_process_queue_invalid_payload(mock_queue_message, mock_shared_helpers_async, mock_env_vars):
    """Test processing with a payload that fails Pydantic validation (async)."""
    # Arrange - Modify the message body to be invalid
    invalid_body_dict = json.loads(QUEUE_MESSAGE_BODY.decode('utf-8'))
    invalid_body_dict["payload"]["frequency"] = "invalid_freq"
    # Use a new mock message for this test to avoid side effects
    invalid_msg = func.QueueMessage(id='invalid-msg-id', body=json.dumps(invalid_body_dict).encode('utf-8'), pop_receipt='test-receipt')

    # Act
    await main(invalid_msg)

    # Assert
    # Should update status to FAILED after validation error
    mock_shared_helpers_async["update_status"].assert_awaited_once_with(
        TEST_JOB_ID, JobStatus.FAILED, -1, ANY
    )
    # Check the error message contains validation info
    failure_call_args = mock_shared_helpers_async["update_status"].await_args[0]
    assert "Payload validation failed" in failure_call_args[3]
    assert "Input should be 'single' or 'weekly'" in failure_call_args[3] # Match Pydantic error

    # Ensure other async helpers were not called
    assert mock_shared_helpers_async["read_band"].await_count == 0
    assert mock_shared_helpers_async["upload_report"].await_count == 0

@pytest.mark.asyncio
async def test_process_queue_no_stac_items(mock_queue_message, mock_get_sentinel2_urls, mock_shared_helpers_async, mock_env_vars):
    """Test processing when STAC search returns no items (async)."""
    # Arrange
    # Make the get_sentinel2_urls helper raise FileNotFoundError
    mock_get_sentinel2_urls.side_effect = FileNotFoundError("Mocked: No suitable Sentinel-2 L2A data found.")

    # Act
    await main(mock_queue_message)

    # Assert
    # Check status updated to FAILED with appropriate message
    status_awaits = mock_shared_helpers_async["update_status"].await_args_list
    assert status_awaits[0] == call(TEST_JOB_ID, JobStatus.PROCESSING, 5, "Starting analysis")
    assert status_awaits[1] == call(TEST_JOB_ID, JobStatus.PROCESSING, 10, "Searching for satellite images")
    # Check the FAILED status update call
    assert status_awaits[2][0][1] == JobStatus.FAILED
    assert status_awaits[2][0][2] == 15 # Progress at failure point
    assert "Failed to find/get Sentinel-2 data" in status_awaits[2][0][3]
    assert "Mocked: No suitable Sentinel-2 L2A data found" in status_awaits[2][0][3]

    # Ensure subsequent helpers not awaited
    assert mock_shared_helpers_async["read_band"].await_count == 0
    assert mock_shared_helpers_async["generate_recs"].await_count == 0
    assert mock_shared_helpers_async["upload_report"].await_count == 0

@pytest.mark.asyncio
async def test_process_queue_band_read_fails(
    mock_queue_message,
    mock_get_sentinel2_urls,
    mock_shared_helpers_async,
    mock_env_vars,
    mocker
):
    """Test processing when reading a band fails (async)."""
    # Arrange
    read_error = rasterio.errors.RasterioIOError("Mocked Async Read error")
    # Make the mocked async read_band helper raise the error
    mock_shared_helpers_async["read_band"].side_effect = read_error

    # Act & Assert
    # Call main and expect it to raise the error after the except block's handling
    with pytest.raises(rasterio.errors.RasterioIOError, match="Mocked Async Read error"):
        await main(mock_queue_message)

    # Assert status was updated to FAILED *before* the exception was re-raised
    mock_shared_helpers_async["update_status"].assert_awaited_with(
        TEST_JOB_ID, JobStatus.FAILED, -1, ANY # Update progress expectation to -1 as per code logic
    )
    # Check the message contains the error details from the final exception handler
    failure_call_args = mock_shared_helpers_async["update_status"].await_args[0]
    assert "Processing failed" in failure_call_args[3] # Check for message from outer except block
    assert "RasterioIOError" in failure_call_args[3] # Check exception type is included
    assert "Mocked Async Read error" in failure_call_args[3]

    # Check the last status *before* failure was download start
    assert mock_shared_helpers_async["update_status"].await_args_list[-2] == call(TEST_JOB_ID, JobStatus.PROCESSING, 20, "Downloading image data from Selected Date")

    # Ensure subsequent steps didn't run
    assert mock_shared_helpers_async["upload_report"].await_count == 0

@pytest.mark.asyncio
async def test_process_queue_openai_fails(
    mock_queue_message,
    mock_get_sentinel2_urls,
    mock_shared_helpers_async,
    mock_matplotlib,
    mock_env_vars
):
    """Test processing continues and completes even if OpenAI fails (async)."""
    # Arrange
    # Make the mocked async generate_recs return a failure message
    mock_shared_helpers_async["generate_recs"].return_value = "Failed to generate AI recommendations due to an error."

    # Act
    await main(mock_queue_message)

    # Assert
    # Check it reached the final steps
    mock_shared_helpers_async["upload_report"].assert_awaited_once()
    final_report_data = mock_shared_helpers_async["upload_report"].await_args[0][1]
    assert final_report_data.status == JobStatus.COMPLETED
    assert "Failed to generate AI recommendations" in final_report_data.recommendations # Check failure message is in recommendations/summary field

    # Check final status update is still COMPLETED
    mock_shared_helpers_async["update_status"].assert_awaited_with(TEST_JOB_ID, JobStatus.COMPLETED, 100, "Analysis complete")

# TODO: Add more tests? (e.g., image upload failure, specific calculation errors)
