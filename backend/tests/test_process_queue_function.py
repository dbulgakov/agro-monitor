import json
import io
import pytest
import numpy as np
import azure.functions as func
import rasterio # Add import
from unittest.mock import Mock, patch, ANY, call, MagicMock
from concurrent.futures import ThreadPoolExecutor # Import to patch

# Change import to be relative to project root
from functions.ProcessQueueFunction.main import main
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

@pytest.fixture
def mock_stac_search(mocker):
    """Mocks pystac_client.Client and search results."""
    mock_client = mocker.Mock()
    mock_search = mocker.Mock()
    mock_item = mocker.Mock( # Simulate a STAC item
        id="S2A_MSIL2A_20240315T...",
        datetime=mocker.Mock(strftime=lambda fmt: "2024-03-15"),
        properties={'eo:cloud_cover': 5.5},
        assets={
            "B04": mocker.Mock(href="red_href_unsigned"),
            "B08": mocker.Mock(href="nir_href_unsigned"),
            "visual": mocker.Mock(href="rgb_href_unsigned")
        }
    )
    mock_search.items.return_value = [mock_item]
    mock_client.search.return_value = mock_search
    mocker.patch('functions.ProcessQueueFunction.main.Client.open', return_value=mock_client)
    return mock_client, mock_search

@pytest.fixture
def mock_planetary_computer(mocker):
    """Mocks planetary_computer.sign."""
    def mock_sign(url):
        return f"{url}_signed"
    mocker.patch('functions.ProcessQueueFunction.main.planetary_computer.sign', side_effect=mock_sign)

@pytest.fixture
def mock_thread_pool(mocker):
    """Mocks ThreadPoolExecutor to run tasks sequentially."""
    def sequential_submit(fn, *args, **kwargs):
        future = Mock()
        try:
            result = fn(*args, **kwargs)
            future.result.return_value = result
        except Exception as e:
            future.result.side_effect = e # Propagate exception from mocked function
        return future

    mock_executor = MagicMock(spec=ThreadPoolExecutor)
    mock_executor.submit.side_effect = sequential_submit
    mock_executor.__enter__.return_value = mock_executor
    mocker.patch('functions.ProcessQueueFunction.main.ThreadPoolExecutor', return_value=mock_executor)
    return mock_executor

@pytest.fixture
def mock_shared_helpers(mocker):
    """Mocks all helper functions from shared_code.helpers."""
    mock_update_status = mocker.patch('functions.shared_code.helpers.update_job_status')
    mock_upload_image = mocker.patch('functions.shared_code.helpers.upload_image_to_blob', return_value="mock_image_url")
    mock_upload_report = mocker.patch('functions.shared_code.helpers.upload_report_to_blob')
    mock_generate_recs = mocker.patch('functions.shared_code.helpers.generate_openai_recommendations', return_value="Mock AI recs")

    # --- Mock rasterio.open directly --- #
    # This ensures that when helpers.read_band/read_rgb call rasterio.open,
    # they get our mock instead of trying to access a real file.
    mock_rasterio_src = MagicMock()
    # Configure return values for band/rgb reads
    # Make read return different things based on args if needed, or use side_effect
    def mock_rasterio_read(*args, **kwargs):
        # Differentiate based on number of bands requested (1 for band, 3 for rgb)
        bands = args[0] if args else [1] # Default to band 1 if no args
        if isinstance(bands, int) or len(bands) == 1:
            return np.array([[100, 110], [120, 130]], dtype=np.float32) # Mock band data
        elif len(bands) == 3:
             # Corrected shape (3 bands, 2 height, 1 width)
            return np.array([[[1]],[[2]], [[3]],[[4]], [[5]],[[6]]], dtype="float32").reshape(3, 2, 1)
        else:
             raise ValueError(f"Unexpected band request in mock: {bands}")

    mock_rasterio_src.read.side_effect = mock_rasterio_read
    # Patch rasterio.open within the helpers module scope
    mock_ropen = mocker.patch('functions.shared_code.helpers.rasterio.open')
    mock_ropen.return_value.__enter__.return_value = mock_rasterio_src
    # --- End rasterio mock --- #

    # We no longer need to mock read_band/read_rgb as rasterio.open is mocked
    # mock_read_band = mocker.patch('functions.shared_code.helpers.read_band', return_value=np.array([[100, 110], [120, 130]], dtype=np.float32))
    # mock_read_rgb = mocker.patch('functions.shared_code.helpers.read_rgb', return_value=np.array([[[1,2,3],[4,5,6]],[[7,8,9],[10,11,12]]], dtype=np.float32))
    mock_normalize = mocker.patch('functions.shared_code.helpers.normalize_image', side_effect=lambda job_id, img: img / 255.0)

    return {
        "update_status": mock_update_status,
        "upload_image": mock_upload_image,
        "upload_report": mock_upload_report,
        "generate_recs": mock_generate_recs,
        "rasterio_open": mock_ropen, # Return the mock if needed for asserts
        # "read_band": mock_read_band, # No longer mocking directly
        # "read_rgb": mock_read_rgb,   # No longer mocking directly
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
    mocker.patch('functions.ProcessQueueFunction.main.os.getenv', return_value="test_env")
    return mocker.patch('functions.ProcessQueueFunction.main.os.getenv')

# --- Test Cases ---

def test_process_queue_success(
    mock_queue_message,
    mock_stac_search,
    mock_planetary_computer,
    mock_thread_pool,
    mock_shared_helpers,
    mock_matplotlib,
    mock_env_vars
):
    """Test successful processing of a queue message."""
    # Arrange (mocks are arranged by fixtures)

    # Act
    main(mock_queue_message) # Call the main function

    # Assert
    # Check status updates
    expected_status_calls = [
        call(TEST_JOB_ID, JobStatus.PROCESSING, 5, "Starting analysis"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 10, "Searching for satellite images"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 20, "Downloading image data from 2024-03-15"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 40, "Calculating NDVI and generating images"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 70, "Uploading images"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 85, "Generating recommendations"),
        call(TEST_JOB_ID, JobStatus.PROCESSING, 95, "Saving final report"),
        call(TEST_JOB_ID, JobStatus.COMPLETED, 100, "Analysis complete"),
    ]
    mock_shared_helpers["update_status"].assert_has_calls(expected_status_calls)

    # Check STAC search call
    mock_stac_search[0].search.assert_called_once()

    # Check rasterio.open was called (implicitly via read_band/read_rgb)
    assert mock_shared_helpers["rasterio_open"].call_count == 3 # Once for each band read attempt

    # Check normalization
    mock_shared_helpers["normalize_image"].assert_called_once()

    # Check plotting calls (basic checks)
    assert mock_matplotlib # Check fixture was used
    assert functions.ProcessQueueFunction.main.plt.subplots.call_count == 3
    assert functions.ProcessQueueFunction.main.plt.savefig.call_count == 3
    assert functions.ProcessQueueFunction.main.plt.close.call_count == 3

    # Check image uploads (via ThreadPoolExecutor mock)
    mock_shared_helpers["upload_image"].assert_any_call(TEST_JOB_ID, ANY, "satellite_rgb.png")
    mock_shared_helpers["upload_image"].assert_any_call(TEST_JOB_ID, ANY, "ndvi.png")
    mock_shared_helpers["upload_image"].assert_any_call(TEST_JOB_ID, ANY, "stress_mask.png")
    assert mock_shared_helpers["upload_image"].call_count == 3

    # Check OpenAI call
    mock_shared_helpers["generate_recs"].assert_called_once()

    # Check final report upload
    mock_shared_helpers["upload_report"].assert_called_once()
    final_report_data = mock_shared_helpers["upload_report"].call_args[0][1]
    assert isinstance(final_report_data, ReportData)
    assert final_report_data.jobId == TEST_JOB_ID
    assert final_report_data.status == JobStatus.COMPLETED
    assert final_report_data.summary == "Mock AI recs"
    assert final_report_data.snapshotImageUrl == "mock_image_url"
    assert final_report_data.processing_details["image_id"] == "S2A_MSIL2A_20240315T..."

def test_process_queue_invalid_payload(mock_queue_message, mock_shared_helpers, mock_env_vars):
    """Test processing with a payload that fails Pydantic validation inside the queue function."""
    # Arrange - Modify the message body to be invalid AFTER initial parsing
    invalid_body_dict = json.loads(QUEUE_MESSAGE_BODY.decode('utf-8'))
    invalid_body_dict["payload"]["frequency"] = "invalid_freq"
    mock_queue_message.get_body = lambda: json.dumps(invalid_body_dict).encode('utf-8')

    # Act
    main(mock_queue_message)

    # Assert
    # Should fail validation and update status to FAILED early
    mock_shared_helpers["update_status"].assert_called_once_with(
        TEST_JOB_ID, JobStatus.FAILED, 0, ANY # Check message contains error details
    )
    # Ensure rasterio was not opened
    assert mock_shared_helpers["rasterio_open"].call_count == 0

def test_process_queue_no_stac_items(mock_queue_message, mock_stac_search, mock_shared_helpers, mock_env_vars):
    """Test processing when STAC search returns no items."""
    # Arrange
    mock_stac_search[1].items.return_value = [] # Simulate empty search results

    # Act
    main(mock_queue_message)

    # Assert
    # Check status updated to FAILED with appropriate message
    status_calls = mock_shared_helpers["update_status"].call_args_list
    assert status_calls[0] == call(TEST_JOB_ID, JobStatus.PROCESSING, 5, "Starting analysis")
    assert status_calls[1] == call(TEST_JOB_ID, JobStatus.PROCESSING, 10, "Searching for satellite images")
    # Check the FAILED status update call
    assert status_calls[2][0][1] == JobStatus.FAILED
    assert status_calls[2][0][2] == 15 # Progress at failure point
    assert "No suitable Sentinel-2 images found" in status_calls[2][0][3]

    # Ensure rasterio was not opened
    assert mock_shared_helpers["rasterio_open"].call_count == 0

    assert mock_shared_helpers["generate_recs"].call_count == 0
    assert mock_shared_helpers["upload_report"].call_count == 0

def test_process_queue_band_read_fails(mock_queue_message, mock_stac_search, mock_planetary_computer, mock_thread_pool, mock_shared_helpers, mock_env_vars):
    """Test processing when reading a band (mocked via rasterio.open) fails."""
    # Arrange
    read_error = rasterio.errors.RasterioIOError("Mocked Read error")
    # Mock rasterio.open to fail on the first call
    mock_shared_helpers["rasterio_open"].side_effect = [read_error]

    # Act & Assert
    with pytest.raises(rasterio.errors.RasterioIOError, match="Mocked Read error"):
        main(mock_queue_message)

    # Check status updated to FAILED after the exception
    mock_shared_helpers["update_status"].assert_called_with(
        TEST_JOB_ID, JobStatus.FAILED, -1, ANY
    )
    failure_call_args = mock_shared_helpers["update_status"].call_args[0]
    assert "RasterioIOError" in failure_call_args[3]
    assert "Mocked Read error" in failure_call_args[3]

    # Check the last status *before* failure was download
    assert mock_shared_helpers["update_status"].call_args_list[-2] == call(TEST_JOB_ID, JobStatus.PROCESSING, 20, ANY)

    assert mock_shared_helpers["upload_report"].call_count == 0

def test_process_queue_openai_fails(mock_queue_message, mock_stac_search, mock_planetary_computer, mock_thread_pool, mock_shared_helpers, mock_matplotlib, mock_env_vars):
    """Test processing continues and completes even if OpenAI fails."""
    # Arrange
    mock_shared_helpers["generate_recs"].return_value = "Failed to generate AI recommendations due to an error."

    # Act
    main(mock_queue_message)

    # Assert
    # Check it reached the final steps
    mock_shared_helpers["upload_report"].assert_called_once()
    final_report_data = mock_shared_helpers["upload_report"].call_args[0][1]
    assert final_report_data.status == JobStatus.COMPLETED
    assert "Failed to generate AI recommendations" in final_report_data.summary # Check failure message is in summary

    # Check final status update is still COMPLETED
    mock_shared_helpers["update_status"].assert_called_with(TEST_JOB_ID, JobStatus.COMPLETED, 100, "Analysis complete")

# TODO: Add more tests? (e.g., image upload failure, specific calculation errors)
