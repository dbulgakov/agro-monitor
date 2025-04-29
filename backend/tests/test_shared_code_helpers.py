import pytest
import os
import io
import json
import numpy as np
import openai
import rasterio
import azure.storage.blob
from unittest.mock import Mock, patch, MagicMock
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobProperties

# Change import to be relative to project root
from functions.shared_code import helpers
from functions.shared_code.schemas import JobStatus, ReportData

TEST_JOB_ID = "helper-test-job-456"

# --- Fixtures ---
@pytest.fixture
def mock_env_vars_helpers(monkeypatch):
    """Set environment variables for helper tests."""
    monkeypatch.setenv("AzureWebJobsStorage", "helper-test-conn-string")
    monkeypatch.setenv("REPORTS_CONTAINER_NAME", "helper-reports")
    monkeypatch.setenv("IMAGES_CONTAINER_NAME", "helper-images")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")

@pytest.fixture
def mock_blob_service_client_factory(mocker): # Using mocker fixture
    """Mocks the BlobServiceClient obtained via get_blob_service_client."""
    mock_service_client = mocker.Mock(spec=azure.storage.blob.BlobServiceClient)
    mock_blob_client = mocker.Mock(spec=azure.storage.blob.BlobClient)
    mock_blob_client.url = f"http://mockstorage/{TEST_JOB_ID}/mock_image.png" # Example URL

    # Configure default behaviors for blob client methods
    mock_blob_client.exists.return_value = False
    mock_blob_client.upload_blob = mocker.Mock()
    mock_blob_client.set_blob_metadata = mocker.Mock()
    # Mock get_blob_properties to return a BlobProperties object or raise ResourceNotFoundError
    mock_properties = BlobProperties(name=f"{TEST_JOB_ID}.json", container="helper-reports", metadata=None)
    mock_blob_client.get_blob_properties = mocker.Mock(return_value=mock_properties)

    mock_service_client.get_blob_client.return_value = mock_blob_client

    # Patch the helper function that returns the client
    mocker.patch('functions.shared_code.helpers.get_blob_service_client', return_value=mock_service_client)

    # Return the individual mocks needed for assertions in tests
    return mock_service_client, mock_blob_client

# --- Tests for update_job_status ---

def test_update_job_status_creates_blob_if_not_exists(mock_env_vars_helpers, mock_blob_service_client_factory):
    mock_service_client, mock_blob_client = mock_blob_service_client_factory
    mock_blob_client.exists.return_value = False # Ensure exists returns False

    helpers.update_job_status(TEST_JOB_ID, JobStatus.PROCESSING, 10, "Starting")

    mock_service_client.get_blob_client.assert_called_once_with(container="helper-reports", blob=f"{TEST_JOB_ID}.json")
    mock_blob_client.exists.assert_called_once()
    mock_blob_client.upload_blob.assert_called_once()
    # Check metadata passed during initial upload
    call_args, call_kwargs = mock_blob_client.upload_blob.call_args
    assert call_kwargs['metadata'] == {"jobStatus": "PROCESSING", "jobProgress": "10", "jobMessage": "Starting"}
    mock_blob_client.set_blob_metadata.assert_not_called()

def test_update_job_status_updates_metadata_if_exists(mock_env_vars_helpers, mock_blob_service_client_factory):
    mock_service_client, mock_blob_client = mock_blob_service_client_factory
    mock_blob_client.exists.return_value = True # Ensure exists returns True

    # Mock existing properties
    existing_props = BlobProperties(name=f"{TEST_JOB_ID}.json", container="helper-reports", metadata={"oldKey": "oldValue"})
    mock_blob_client.get_blob_properties.return_value = existing_props

    helpers.update_job_status(TEST_JOB_ID, JobStatus.COMPLETED, 100, "Finished")

    mock_service_client.get_blob_client.assert_called_once_with(container="helper-reports", blob=f"{TEST_JOB_ID}.json")
    mock_blob_client.exists.assert_called_once()
    mock_blob_client.upload_blob.assert_not_called()
    mock_blob_client.get_blob_properties.assert_called_once()
    mock_blob_client.set_blob_metadata.assert_called_once_with(
        metadata={"oldKey": "oldValue", "jobStatus": "COMPLETED", "jobProgress": "100", "jobMessage": "Finished"}
    )

def test_update_job_status_handles_long_message(mock_env_vars_helpers, mock_blob_service_client_factory):
    mock_service_client, mock_blob_client = mock_blob_service_client_factory
    mock_blob_client.exists.return_value = True
    long_message = "A" * 1100
    truncated_message = ("A" * 1020) + "..."

    helpers.update_job_status(TEST_JOB_ID, JobStatus.FAILED, 50, long_message)

    mock_blob_client.set_blob_metadata.assert_called_once()
    assert mock_blob_client.set_blob_metadata.call_args.kwargs['metadata']["jobMessage"] == truncated_message

# --- Tests for upload_image_to_blob ---

def test_upload_image_to_blob_success(mock_env_vars_helpers, mock_blob_service_client_factory):
    mock_service_client, mock_blob_client = mock_blob_service_client_factory
    image_data = io.BytesIO(b"fake image data")
    image_name = "test_image.png"

    result_url = helpers.upload_image_to_blob(TEST_JOB_ID, image_data, image_name)

    expected_blob_name = f"{TEST_JOB_ID}/{image_name}"
    mock_service_client.get_blob_client.assert_called_once_with(container="helper-images", blob=expected_blob_name)
    mock_blob_client.upload_blob.assert_called_once()
    # Check content settings
    call_args, call_kwargs = mock_blob_client.upload_blob.call_args
    assert call_kwargs['content_settings'].content_type == 'image/png'
    # Check data (after seek(0))
    image_data.seek(0)
    assert call_args[0].read() == image_data.read()
    assert result_url == mock_blob_client.url

def test_upload_image_to_blob_failure(mock_env_vars_helpers, mock_blob_service_client_factory):
    mock_service_client, mock_blob_client = mock_blob_service_client_factory
    mock_blob_client.upload_blob.side_effect = Exception("Upload failed")
    image_data = io.BytesIO(b"fake image data")
    image_name = "failed_image.png"

    result_url = helpers.upload_image_to_blob(TEST_JOB_ID, image_data, image_name)

    assert result_url is None
    mock_blob_client.upload_blob.assert_called_once()

# --- Tests for upload_report_to_blob ---

def test_upload_report_to_blob_success(mock_env_vars_helpers, mock_blob_service_client_factory):
    mock_service_client, mock_blob_client = mock_blob_service_client_factory
    report = ReportData(jobId=TEST_JOB_ID, status=JobStatus.COMPLETED, summary="Test Summary")
    existing_props = BlobProperties(name=f"{TEST_JOB_ID}.json", container="helper-reports", metadata={"jobStatus": "PROCESSING"})
    mock_blob_client.get_blob_properties.return_value = existing_props

    helpers.upload_report_to_blob(TEST_JOB_ID, report)

    mock_service_client.get_blob_client.assert_called_once_with(container="helper-reports", blob=f"{TEST_JOB_ID}.json")
    mock_blob_client.get_blob_properties.assert_called_once()
    mock_blob_client.upload_blob.assert_called_once()
    call_args, call_kwargs = mock_blob_client.upload_blob.call_args
    # Check JSON data
    assert json.loads(call_args[0]) == report.model_dump(exclude_none=True)
    # Check metadata includes final status
    assert call_kwargs['metadata'] == {"jobStatus": "COMPLETED", "jobProgress": "100"}

def test_upload_report_to_blob_not_found_initially(mock_env_vars_helpers, mock_blob_service_client_factory):
    mock_service_client, mock_blob_client = mock_blob_service_client_factory
    report = ReportData(jobId=TEST_JOB_ID, status=JobStatus.COMPLETED, summary="Test Summary")
    # Simulate properties raising not found
    mock_blob_client.get_blob_properties.side_effect = ResourceNotFoundError("Not found")

    helpers.upload_report_to_blob(TEST_JOB_ID, report)

    mock_blob_client.get_blob_properties.assert_called_once()
    mock_blob_client.upload_blob.assert_called_once()
    call_args, call_kwargs = mock_blob_client.upload_blob.call_args
    # Metadata should still be set correctly even if props failed
    assert call_kwargs['metadata'] == {"jobStatus": "COMPLETED", "jobProgress": "100"}

def test_upload_report_to_blob_upload_fails(mock_env_vars_helpers, mock_blob_service_client_factory):
    mock_service_client, mock_blob_client = mock_blob_service_client_factory
    report = ReportData(jobId=TEST_JOB_ID, status=JobStatus.COMPLETED, summary="Test Summary")
    mock_blob_client.upload_blob.side_effect = Exception("Blob upload failed")

    with pytest.raises(Exception, match="Blob upload failed"):
        helpers.upload_report_to_blob(TEST_JOB_ID, report)

# --- Tests for generate_openai_recommendations ---

@patch('functions.shared_code.helpers.openai_client') # Patch the global client instance
def test_generate_openai_recommendations_success(mock_openai_client, mock_env_vars_helpers):
    # Arrange
    mock_completion = Mock()
    mock_completion.choices = [Mock(message=Mock(content=" Test Recommendation "))]
    mock_openai_client.chat.completions.create.return_value = mock_completion

    ndvi_data = np.array([[0.8, 0.7], [0.2, 0.9]])
    stress_mask = np.array([[False, False], [True, False]])
    crop_type = "Barley"

    # Act
    recommendation = helpers.generate_openai_recommendations(TEST_JOB_ID, ndvi_data, stress_mask, crop_type)

    # Assert
    assert recommendation == "Test Recommendation"
    mock_openai_client.chat.completions.create.assert_called_once()
    # Check prompt contents (optional, can be brittle)
    call_args, call_kwargs = mock_openai_client.chat.completions.create.call_args
    prompt = call_kwargs['messages'][0]['content']
    assert "Barley" in prompt
    assert "Average NDVI: 0.650" in prompt # (0.8+0.7+0.2+0.9)/4 = 0.65
    assert "area showing stress: 25.0%" in prompt # 1 out of 4 pixels

@patch('functions.shared_code.helpers.openai_client')
def test_generate_openai_recommendations_api_error(mock_openai_client, mock_env_vars_helpers):
    # Use generic Exception
    mock_openai_client.chat.completions.create.side_effect = Exception("Service unavailable")
    ndvi_data = np.array([[0.8]])
    stress_mask = np.array([[False]])
    crop_type = "Test"
    recommendation = helpers.generate_openai_recommendations(TEST_JOB_ID, ndvi_data, stress_mask, crop_type)
    assert "Failed to generate AI recommendations" in recommendation

def test_generate_openai_recommendations_no_key(monkeypatch):
    # Arrange - mock the global client to be None by removing key
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Need to reload helpers for the client to be None *during the test*
    # This can be tricky. Easier might be patching helpers.openai_client directly.
    with patch('functions.shared_code.helpers.openai_client', None):
        ndvi_data = np.array([[0.8]])
        stress_mask = np.array([[False]])
        crop_type = "Test"
        recommendation = helpers.generate_openai_recommendations(TEST_JOB_ID, ndvi_data, stress_mask, crop_type)
        assert "AI recommendations disabled" in recommendation

# --- Tests for read_band/read_rgb ---

@patch('functions.shared_code.helpers.rasterio.open')
def test_read_band_success(mock_rasterio_open):
    mock_src = MagicMock()
    # Create a mock numpy array for the read result
    mock_band_data = np.array([[1, 2], [3, 4]], dtype="float32")
    mock_src.read.return_value = mock_band_data
    # Configure the context manager
    mock_rasterio_open.return_value.__enter__.return_value = mock_src

    result = helpers.read_band(TEST_JOB_ID, "mock_url_band")

    mock_rasterio_open.assert_called_once_with("mock_url_band")
    mock_src.read.assert_called_once_with(1)
    np.testing.assert_array_equal(result, mock_band_data)

@patch('functions.shared_code.helpers.rasterio.open')
def test_read_rgb_success(mock_rasterio_open):
    mock_src = MagicMock()
    # Corrected mock data shape (3 bands, 2 height, 1 width)
    mock_rgb_data_bands = np.array([[[1]],[[2]], [[3]],[[4]], [[5]],[[6]]], dtype="float32").reshape(3, 2, 1)
    mock_src.read.return_value = mock_rgb_data_bands
    mock_rasterio_open.return_value.__enter__.return_value = mock_src

    result = helpers.read_rgb(TEST_JOB_ID, "mock_url_rgb")

    mock_rasterio_open.assert_called_once_with("mock_url_rgb")
    mock_src.read.assert_called_once_with([1, 2, 3])
    # Assert shape
    assert result.shape == (2, 1, 3) # Height=2, Width=1, Bands=3
    expected_transposed = np.transpose(mock_rgb_data_bands, (1, 2, 0))
    np.testing.assert_array_equal(result, expected_transposed)

@patch('functions.shared_code.helpers.rasterio.open')
def test_read_band_error(mock_rasterio_open):
    mock_rasterio_open.side_effect = rasterio.RasterioIOError("Cannot open file")
    with pytest.raises(rasterio.RasterioIOError):
        helpers.read_band(TEST_JOB_ID, "bad_url")

# --- Tests for normalize_image ---

def test_normalize_image_standard():
    img = np.array([[0, 50, 100], [150, 200, 255]], dtype="float32")
    result = helpers.normalize_image(TEST_JOB_ID, img, lower=2, upper=98)
    assert result.min() >= 0.0
    assert result.max() <= 1.0
    # Fix np.isclose usage
    vmin, vmax = np.percentile(img[~np.isnan(img)], 2), np.percentile(img[~np.isnan(img)], 98)
    assert np.all(np.isclose(result[0, 0], 0.0))
    # Handle potential division by zero if vmin == vmax, although test data avoids it
    denominator = vmax - vmin if (vmax - vmin) > 1e-9 else 1.0
    assert np.all(np.isclose(result[0, 1], np.clip((50 - vmin) / denominator, 0, 1)))
    assert np.all(np.isclose(result[1, 1], np.clip((200 - vmin) / denominator, 0, 1)))
    assert np.all(np.isclose(result[1, 2], 1.0))

def test_normalize_image_with_nan():
    img = np.array([[0, 50, np.nan], [150, 200, 255]], dtype="float32")
    result = helpers.normalize_image(TEST_JOB_ID, img)
    assert np.isnan(result[0, 2]) # NaN should propagate
    assert not np.isnan(result[0, 0])

def test_normalize_image_empty_or_all_nan():
    img_nan = np.array([[np.nan, np.nan], [np.nan, np.nan]], dtype="float32")
    result_nan = helpers.normalize_image(TEST_JOB_ID, img_nan)
    assert np.all(result_nan == 0)

    img_empty = np.array([], dtype="float32")
    # This case might actually raise an error in percentile, test if needed
    # Or ensure valid_pixels check handles it - it should.
    result_empty = helpers.normalize_image(TEST_JOB_ID, img_empty)
    assert result_empty.size == 0

def test_normalize_image_flat():
    img = np.full((5, 5), 100.0, dtype="float32")
    result = helpers.normalize_image(TEST_JOB_ID, img)
    # Should clamp to 0 or 1 depending on the single value relative to 0.5
    # Since 100 > 0.5, expect 1s
    assert np.all(result == 1.0)

    img_zero = np.zeros((5, 5), dtype="float32")
    result_zero = helpers.normalize_image(TEST_JOB_ID, img_zero)
    assert np.all(result_zero == 0.0)
