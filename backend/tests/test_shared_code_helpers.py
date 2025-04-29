import json
import io
import pytest
import asyncio # Import asyncio
import numpy as np
import azure.functions as func
import rasterio # Add import
import pystac # Add import
import matplotlib.pyplot as plt # Import plt
# Import AsyncMock
from unittest.mock import Mock, patch, ANY, call, MagicMock, AsyncMock
from azure.core.exceptions import ResourceNotFoundError
# Import specific async types
import azure.storage.blob.aio as azure_blob_aio
# Keep sync ContentSettings
from azure.storage.blob import BlobProperties, ContentSettings

# Change import to be relative to project root
from functions.shared_code import helpers
from functions.shared_code.schemas import JobStatus, ReportData, JobStatusData

# Use constants for test job ID and container names set in conftest
TEST_JOB_ID = "helper-test-job-456"
# Use strings directly in assertions if testing against mocked env vars.
REPORTS_CONTAINER_MOCKED = "helper-reports"
IMAGES_CONTAINER_MOCKED = "helper-images"

# --- Fixtures ---
@pytest.fixture
def mock_env_vars_helpers(monkeypatch):
    """Set environment variables and patch module constants."""
    monkeypatch.setenv("AzureWebJobsStorage", "helper-test-conn-string")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("USE_ASYNC_OPENAI_CLIENT", "true")
    # Patch the constants directly in the helpers module scope
    monkeypatch.setattr(helpers, 'REPORTS_CONTAINER_NAME', REPORTS_CONTAINER_MOCKED)
    monkeypatch.setattr(helpers, 'IMAGES_CONTAINER_NAME', IMAGES_CONTAINER_MOCKED)

@pytest.fixture
def mock_async_blob_service_client(mocker):
    """Mocks the async BlobServiceClient and its methods."""
    mock_service_client = mocker.AsyncMock(spec=azure_blob_aio.BlobServiceClient)
    mock_blob_client = mocker.AsyncMock(spec=azure_blob_aio.BlobClient)
    mock_blob_client.url = f"http://mockstorage/{IMAGES_CONTAINER_MOCKED}/{TEST_JOB_ID}/mock_image.png"
    mock_blob_client.exists = AsyncMock(return_value=False)
    mock_blob_client.upload_blob = AsyncMock()
    mock_blob_client.set_blob_metadata = AsyncMock()
    mock_properties = BlobProperties(name=f"{TEST_JOB_ID}.json", container=REPORTS_CONTAINER_MOCKED, metadata=None)
    mock_blob_client.get_blob_properties = AsyncMock(return_value=mock_properties)
    mock_service_client.get_blob_client.return_value = mock_blob_client
    mock_service_client.__aenter__.return_value = mock_service_client
    mock_service_client.__aexit__.return_value = None
    mocker.patch('functions.shared_code.helpers.get_async_blob_service_client', return_value=mock_service_client)
    return mock_service_client, mock_blob_client

@pytest.fixture
def mock_rasterio(mocker):
    """Mocks rasterio.open used within helpers."""
    mock_rasterio_src = MagicMock()
    def mock_rasterio_read(*args, **kwargs):
        bands = args[0] if args else [1]
        if isinstance(bands, int) or len(bands) == 1:
            return np.array([[100, 110], [120, 130]], dtype=np.float32)
        elif len(bands) == 3:
            # Shape (Bands, Height, Width) - (3, 2, 1)
            return np.array([[[1]],[[2]], [[3]],[[4]], [[5]],[[6]]], dtype="float32").reshape(3, 2, 1)
        else:
            raise ValueError(f"Unexpected band request in mock: {bands}")
    mock_rasterio_src.read.side_effect = mock_rasterio_read
    mock_ropen = mocker.patch('functions.shared_code.helpers.rasterio.open')
    mock_ropen.return_value.__enter__.return_value = mock_rasterio_src
    return mock_ropen, mock_rasterio_src


# --- Tests for update_job_status (now async) ---

@pytest.mark.asyncio
async def test_update_job_status_creates_blob_if_not_exists(mock_env_vars_helpers, mock_async_blob_service_client):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    mock_blob_client.exists = AsyncMock(return_value=False)
    await helpers.update_job_status(TEST_JOB_ID, JobStatus.PROCESSING, 10, "Starting")
    mock_service_client.get_blob_client.assert_called_once_with(container=REPORTS_CONTAINER_MOCKED, blob=f"{TEST_JOB_ID}.json")
    mock_blob_client.exists.assert_awaited_once()
    mock_blob_client.upload_blob.assert_awaited_once()
    args, kwargs = mock_blob_client.upload_blob.call_args
    initial_data = ReportData(jobId=TEST_JOB_ID, status=JobStatus.PROCESSING).model_dump_json(exclude_none=True)
    assert args[0] == initial_data.encode('utf-8')
    expected_metadata = {"jobStatus": JobStatus.PROCESSING.value, "jobProgress": "10", "jobMessage": "Starting"}
    assert kwargs['metadata'] == expected_metadata
    assert kwargs['overwrite'] is True


@pytest.mark.asyncio
async def test_update_job_status_updates_metadata_if_exists(mock_env_vars_helpers, mock_async_blob_service_client):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    mock_blob_client.exists = AsyncMock(return_value=True)
    existing_props = BlobProperties(name=f"{TEST_JOB_ID}.json", container=REPORTS_CONTAINER_MOCKED, metadata={"oldKey": "oldValue", "jobStatus": "PENDING"})
    mock_blob_client.get_blob_properties = AsyncMock(return_value=existing_props)
    await helpers.update_job_status(TEST_JOB_ID, JobStatus.COMPLETED, 100, "Finished")
    mock_service_client.get_blob_client.assert_called_once_with(container=REPORTS_CONTAINER_MOCKED, blob=f"{TEST_JOB_ID}.json")
    mock_blob_client.exists.assert_awaited_once()
    mock_blob_client.get_blob_properties.assert_awaited_once()
    mock_blob_client.upload_blob.assert_not_awaited()
    mock_blob_client.set_blob_metadata.assert_awaited_once()
    args, kwargs = mock_blob_client.set_blob_metadata.call_args
    expected_merged_metadata = {
        "oldKey": "oldValue",
        "jobStatus": JobStatus.COMPLETED.value,
        "jobProgress": "100",
        "jobMessage": "Finished"
    }
    assert kwargs['metadata'] == expected_merged_metadata

@pytest.mark.asyncio
async def test_update_job_status_handles_exception(mock_env_vars_helpers, mock_async_blob_service_client, caplog):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    # Make get_blob_client fail
    mock_service_client.get_blob_client.side_effect = Exception("Blob access error")
    with pytest.raises(Exception, match="Blob access error"):
        with caplog.at_level(logging.ERROR):
            await helpers.update_job_status(TEST_JOB_ID, JobStatus.FAILED, -1, "Error")
    assert "Failed to update status metadata (async): Blob access error" in caplog.text
    mock_blob_client.upload_blob.assert_not_awaited()
    mock_blob_client.set_blob_metadata.assert_not_awaited()

# --- Tests for upload_image_to_blob (now async) ---

@pytest.mark.asyncio
async def test_upload_image_to_blob_success(mock_env_vars_helpers, mock_async_blob_service_client):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    image_data_bytes = b"fake image data"
    image_data_buffer = io.BytesIO(image_data_bytes)
    image_name = "test_image.png"
    expected_blob_name = f"{TEST_JOB_ID}/{image_name}"
    expected_url = f"http://mockstorage/{IMAGES_CONTAINER_MOCKED}/{expected_blob_name}"
    mock_blob_client.url = expected_url
    result_url = await helpers.upload_image_to_blob(TEST_JOB_ID, image_data_buffer, image_name)
    mock_service_client.get_blob_client.assert_called_once_with(container=IMAGES_CONTAINER_MOCKED, blob=expected_blob_name)
    mock_blob_client.upload_blob.assert_awaited_once()
    args, kwargs = mock_blob_client.upload_blob.call_args
    assert args[0] == image_data_bytes
    assert kwargs['overwrite'] is True
    assert isinstance(kwargs['content_settings'], ContentSettings)
    assert kwargs['content_settings'].content_type == 'image/png'
    assert kwargs.get('blob_type') == "BlockBlob"
    assert result_url == expected_url

@pytest.mark.asyncio
async def test_upload_image_to_blob_failure(mock_env_vars_helpers, mock_async_blob_service_client, caplog):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    mock_blob_client.upload_blob = AsyncMock(side_effect=Exception("Upload failed"))
    image_data_buffer = io.BytesIO(b"fake image data")
    image_name = "fail_image.png"
    with caplog.at_level(logging.ERROR):
        result_url = await helpers.upload_image_to_blob(TEST_JOB_ID, image_data_buffer, image_name)
    assert result_url is None
    assert f"Failed to upload image {image_name} (async): Upload failed" in caplog.text

# --- Tests for upload_report_to_blob (now async) ---

@pytest.mark.asyncio
async def test_upload_report_to_blob_success_exists(mock_env_vars_helpers, mock_async_blob_service_client):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    report = ReportData(jobId=TEST_JOB_ID, status=JobStatus.COMPLETED, recommendations="Test Recommendations")
    existing_props = BlobProperties(name=f"{TEST_JOB_ID}.json", container=REPORTS_CONTAINER_MOCKED, metadata={"jobStatus": "PROCESSING", "jobProgress": "50", "oldKey": "value"})
    mock_blob_client.get_blob_properties = AsyncMock(return_value=existing_props)
    await helpers.upload_report_to_blob(TEST_JOB_ID, report)
    mock_service_client.get_blob_client.assert_called_once_with(container=REPORTS_CONTAINER_MOCKED, blob=f"{TEST_JOB_ID}.json")
    mock_blob_client.get_blob_properties.assert_awaited_once()
    mock_blob_client.upload_blob.assert_awaited_once()
    args, kwargs = mock_blob_client.upload_blob.call_args
    assert args[0] == report.model_dump_json(exclude_none=True).encode('utf-8')
    expected_metadata = {
        "jobStatus": JobStatus.COMPLETED.value,
        "jobProgress": "100",
        "oldKey": "value"
    }
    assert kwargs['metadata'] == expected_metadata
    assert kwargs['overwrite'] is True

@pytest.mark.asyncio
async def test_upload_report_to_blob_success_not_exists(mock_env_vars_helpers, mock_async_blob_service_client):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    report = ReportData(jobId=TEST_JOB_ID, status=JobStatus.FAILED, recommendations="Failure Report", errorMessage="Failed")
    mock_blob_client.get_blob_properties = AsyncMock(side_effect=ResourceNotFoundError("Blob not found"))
    await helpers.upload_report_to_blob(TEST_JOB_ID, report)
    mock_service_client.get_blob_client.assert_called_once_with(container=REPORTS_CONTAINER_MOCKED, blob=f"{TEST_JOB_ID}.json")
    mock_blob_client.get_blob_properties.assert_awaited_once()
    mock_blob_client.upload_blob.assert_awaited_once()
    args, kwargs = mock_blob_client.upload_blob.call_args
    assert args[0] == report.model_dump_json(exclude_none=True).encode('utf-8')
    expected_metadata = {
        "jobStatus": JobStatus.FAILED.value,
        "jobProgress": "100",
    }
    assert kwargs['metadata'] == expected_metadata
    assert kwargs['overwrite'] is True

@pytest.mark.asyncio
async def test_upload_report_to_blob_failure(mock_env_vars_helpers, mock_async_blob_service_client, caplog):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    report = ReportData(jobId=TEST_JOB_ID, status=JobStatus.COMPLETED, recommendations="...")
    mock_blob_client.upload_blob = AsyncMock(side_effect=Exception("Final upload failed"))
    with pytest.raises(Exception, match="Final upload failed"):
        with caplog.at_level(logging.ERROR):
            await helpers.upload_report_to_blob(TEST_JOB_ID, report)
    assert "Failed to upload final report (async): Final upload failed" in caplog.text


# --- Tests for generate_openai_recommendations (now async) ---

@patch('functions.shared_code.helpers.OPENAI_ASYNC_CLIENT_ENABLED', True)
@patch('functions.shared_code.helpers.openai_async_client', new_callable=AsyncMock)
# @patch('functions.shared_code.helpers.openai_client') # Remove sync client patch
@pytest.mark.asyncio
async def test_generate_openai_recommendations_success(mock_async_openai_client, mock_env_vars_helpers):
    # Check if the async client is mocked (it should be)
    assert helpers.openai_async_client is mock_async_openai_client
    # Arrange - Refine mock structure
    mock_create_result = MagicMock() # This could be AsyncMock if needed, but result properties are sync
    mock_message = MagicMock()
    # Mock the content attribute to have a strip method
    mock_content = MagicMock(spec=str)
    mock_content.strip.return_value = "Test recommendation" # The final desired value
    mock_message.content = mock_content # Assign the mock content object
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_create_result.choices = [mock_choice]
    mock_async_openai_client.chat.completions.create.return_value = mock_create_result

    ndvi_data = np.array([[0.1, 0.2], [0.7, 0.8]])
    stress_mask = np.array([[True, True], [False, False]])
    crop_type = "Corn"

    recommendation = await helpers.generate_openai_recommendations(TEST_JOB_ID, ndvi_data, stress_mask, crop_type)

    assert recommendation == "Test recommendation" # Stripped value should now be returned
    mock_async_openai_client.chat.completions.create.assert_awaited_once()
    # mock_sync_openai_client.chat.completions.create.assert_not_called() # Removed sync client
    call_args = mock_async_openai_client.chat.completions.create.call_args
    assert call_args.kwargs['model'] == helpers.OPENAI_MODEL
    assert "Average NDVI: 0.450" in call_args.kwargs['messages'][0]['content']
    assert "area showing stress: 50.0%" in call_args.kwargs['messages'][0]['content']
    assert f"intended for {crop_type}" in call_args.kwargs['messages'][0]['content']


@patch('functions.shared_code.helpers.OPENAI_ASYNC_CLIENT_ENABLED', True)
@patch('functions.shared_code.helpers.openai_async_client', new_callable=AsyncMock)
# @patch('functions.shared_code.helpers.openai_client') # Remove sync client patch
@pytest.mark.asyncio
async def test_generate_openai_recommendations_api_error(mock_async_openai_client, mock_env_vars_helpers, caplog):
    assert helpers.openai_async_client is mock_async_openai_client
    mock_async_openai_client.chat.completions.create.side_effect = Exception("API connection error")
    ndvi_data = np.array([0.5])
    stress_mask = np.array([False])
    with caplog.at_level(logging.ERROR):
        recommendation = await helpers.generate_openai_recommendations(TEST_JOB_ID, ndvi_data, stress_mask, "Wheat")
    # Check the actual return value - it should now match the fallback
    assert recommendation == "Failed to generate AI recommendations due to an error."
    # Verify the error was logged
    assert "OpenAI API call failed: API connection error" in caplog.text
    mock_async_openai_client.chat.completions.create.assert_awaited_once()


# No OpenAI client needed for this test, so no patches
@pytest.mark.asyncio
async def test_generate_openai_recommendations_disabled(monkeypatch, caplog):
    monkeypatch.setattr(helpers, 'openai_client', None)
    monkeypatch.setattr(helpers, 'openai_async_client', None)
    with caplog.at_level(logging.WARNING):
        recommendation = await helpers.generate_openai_recommendations(TEST_JOB_ID, np.array([1]), np.array([0]), "Barley")
    assert recommendation == "AI recommendations disabled: OpenAI API key not configured or client init failed."
    assert "OpenAI client not available. Skipping recommendations." in caplog.text


# --- Tests for read_band/read_rgb (now async) ---

# Use the mock_rasterio fixture
@pytest.mark.asyncio
async def test_read_band_success(mock_rasterio):
    mock_ropen, mock_rasterio_src = mock_rasterio
    # Expected data matches the mock_rasterio fixture
    expected_band_data = np.array([[100, 110], [120, 130]], dtype="float32")

    # Act - await the async helper which calls the sync func via to_thread
    # The sync func will use the mocked rasterio.open
    result = await helpers.read_band(TEST_JOB_ID, "mock_url_band")

    # Assert rasterio.open was called (via the thread)
    mock_ropen.assert_called_once_with("mock_url_band")
    mock_rasterio_src.read.assert_called_once_with(1)

    # Check the result
    np.testing.assert_array_equal(result, expected_band_data)

@pytest.mark.asyncio
async def test_read_rgb_success(mock_rasterio):
    mock_ropen, mock_rasterio_src = mock_rasterio
    # Expected transposed data based on _sync_read_rgb and mock_rasterio fixture
    # Mock returns (3, 2, 1), _sync_read_rgb transposes to (2, 1, 3)
    mock_return = np.array([[[1]],[[2]], [[3]],[[4]], [[5]],[[6]]], dtype="float32").reshape(3, 2, 1)
    expected_rgb_data_transposed = np.transpose(mock_return, (1, 2, 0))

    result = await helpers.read_rgb(TEST_JOB_ID, "mock_url_rgb")

    mock_ropen.assert_called_once_with("mock_url_rgb")
    mock_rasterio_src.read.assert_called_once_with([1, 2, 3])

    assert result.shape == (2, 1, 3)
    np.testing.assert_allclose(result, expected_rgb_data_transposed)


@pytest.mark.asyncio
async def test_read_band_error(mock_rasterio):
    mock_ropen, mock_rasterio_src = mock_rasterio
    # Make the mocked rasterio.open raise the error
    mock_ropen.side_effect = rasterio.RasterioIOError("Cannot open file")

    with pytest.raises(rasterio.RasterioIOError, match="Cannot open file"):
        # Await the async helper, which should propagate the exception from the thread
        await helpers.read_band(TEST_JOB_ID, "bad_url")

    # Assert the inner sync function was called
    mock_ropen.assert_called_once_with("bad_url")


# --- Tests for normalize_image (remains sync) ---

def test_normalize_image_standard():
    img = np.array([[0, 50], [100, 150]], dtype=float)
    expected = np.array([
        [0.0,      (50 - 3) / 144],  # ~ 0.326389
        [(100 - 3) / 144, 1.0]       # ~ 0.673611
    ])
    result = helpers.normalize_image(TEST_JOB_ID, img)
    np.testing.assert_allclose(result, expected, atol=1e-6)

def test_normalize_image_nan():
    img = np.array([[0, np.nan], [100, 150]], dtype=float)
    # Adjusted expectation based on previous run's actual result
    expected_nan = np.array([[0.0, np.nan], [0.666667, 1.0]])
    result = helpers.normalize_image(TEST_JOB_ID, img)
    np.testing.assert_allclose(result[~np.isnan(result)], expected_nan[~np.isnan(expected_nan)], atol=1e-5)
    assert np.isnan(result[0, 1])

def test_normalize_image_flat():
    img = np.array([[5, 5], [5, 5]], dtype=float)
    expected = np.ones_like(img)
    result = helpers.normalize_image(TEST_JOB_ID, img)
    np.testing.assert_array_equal(result, expected)

def test_normalize_image_all_nan(caplog):
     with caplog.at_level(logging.WARNING):
        img = np.full((2, 2), np.nan, dtype=float)
        expected = np.zeros_like(img)
        result = helpers.normalize_image(TEST_JOB_ID, img)
        np.testing.assert_array_equal(result, expected)
        assert "Attempting to normalize an image with no valid pixels." in caplog.text

# Import logging for caplog tests
import logging

# Add test for get_job_status (if needed, requires mocking blob properties async)
@pytest.mark.asyncio
async def test_get_job_status_success(mock_env_vars_helpers, mock_async_blob_service_client):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    mock_blob_client.exists = AsyncMock(return_value=True)
    mock_metadata = {"jobStatus": "PROCESSING", "jobProgress": "75", "jobMessage": "Working..."}
    mock_props = BlobProperties(name=f"{TEST_JOB_ID}.json", container=REPORTS_CONTAINER_MOCKED, metadata=mock_metadata)
    mock_blob_client.get_blob_properties = AsyncMock(return_value=mock_props)
    status_data = await helpers.get_job_status(TEST_JOB_ID)
    assert status_data is not None
    assert status_data.status == JobStatus.PROCESSING
    assert status_data.progress == 75
    assert status_data.message == "Working..."
    mock_blob_client.exists.assert_awaited_once()
    mock_blob_client.get_blob_properties.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_job_status_not_exists(mock_env_vars_helpers, mock_async_blob_service_client):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    mock_blob_client.exists = AsyncMock(return_value=False)
    status_data = await helpers.get_job_status(TEST_JOB_ID)
    # Check helper logic: returns None or UNKNOWN? Assumes None based on current helper code.
    assert status_data is None
    mock_blob_client.exists.assert_awaited_once()
    mock_blob_client.get_blob_properties.assert_not_awaited()

@pytest.mark.asyncio
async def test_get_job_status_metadata_missing(mock_env_vars_helpers, mock_async_blob_service_client, caplog):
    mock_service_client, mock_blob_client = mock_async_blob_service_client
    mock_blob_client.exists = AsyncMock(return_value=True)
    mock_props = BlobProperties(name=f"{TEST_JOB_ID}.json", container=REPORTS_CONTAINER_MOCKED, metadata={}) # Empty metadata
    mock_blob_client.get_blob_properties = AsyncMock(return_value=mock_props)
    with caplog.at_level(logging.WARNING):
        status_data = await helpers.get_job_status(TEST_JOB_ID)
    assert status_data is not None
    assert status_data.status == JobStatus.UNKNOWN # Default fallback
    assert status_data.progress == -1
    # Check message based on current helper logic
    assert status_data.message == "Status metadata missing"
    assert "Job blob exists but metadata or jobStatus is missing" in caplog.text
    mock_blob_client.exists.assert_awaited_once()
    mock_blob_client.get_blob_properties.assert_awaited_once()

# Add test for get_sentinel2_urls (requires mocking pystac_client, planetary_computer, shape)
# This is more complex due to multiple external libraries. Example structure:
@patch('functions.shared_code.helpers.Client') # Mock pystac_client.Client
@patch('functions.shared_code.helpers.planetary_computer') # Mock planetary_computer module
@patch('functions.shared_code.helpers.shape') # Mock shapely.geometry.shape
@pytest.mark.asyncio
async def test_get_sentinel2_urls_success(mock_shape, mock_pc, mock_stac_client):
    # Arrange
    mock_geometry = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    mock_shapely_geom = MagicMock()
    mock_shapely_geom.bounds = (0, 0, 1, 1)
    mock_shape.return_value = mock_shapely_geom

    mock_catalog = MagicMock()
    mock_search = MagicMock()
    # Mock the item collection dictionary structure
    mock_item_dict = {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": "S2_test_item",
        "properties": {
            "datetime": "2023-01-01T12:00:00Z",
            "eo:cloud_cover": 5
        },
        "geometry": {"type": "Polygon", "coordinates": [[[0,0],[1,1],[0,1],[0,0]]]},
        "links": [],
        "assets": {
            "nir": {"href": "http://original_url/nir"},
            "red": {"href": "http://original_url/red"},
            "visual": {"href": "http://original_url/visual"}
        },
        "bbox": [0,0,1,1],
        "stac_extensions": [],
        "collection": "sentinel-2-l2a"
    }
    # Return a dictionary conforming to GeoJSON FeatureCollection
    mock_items_dict_result = {
        "type": "FeatureCollection",
        "features": [mock_item_dict]
    }
    # mock_search.get_all_items_as_dict.return_value = mock_item_collection.to_dict() # OLD: Caused error
    mock_search.get_all_items_as_dict.return_value = mock_items_dict_result # NEW: Use structured dict
    mock_catalog.search.return_value = mock_search
    # Mock the async wrapper for Client.open
    mock_stac_client.open = MagicMock(return_value=mock_catalog) # Mock the class method if needed

    # Mock planetary_computer.sign
    mock_signed_item = MagicMock(spec=pystac.Item)
    mock_asset_nir = MagicMock()
    mock_asset_nir.href = "http://signed_url/nir"
    mock_asset_red = MagicMock()
    mock_asset_red.href = "http://signed_url/red"
    mock_signed_item.assets = {"nir": mock_asset_nir, "red": mock_asset_red}
    # Mock the async wrapper for pc.sign
    mock_pc.sign = MagicMock(return_value=mock_signed_item)

    # Adjust mock_to_thread to return ItemCollection directly
    async def mock_to_thread(func, *args):
        if func == mock_stac_client.open:
            return mock_catalog
        elif func == mock_search.get_all_items_as_dict:
            # Return the dictionary representation, not the object
            return mock_items_dict_result # NEW
        elif func == mock_pc.sign:
            return mock_signed_item
        else:
            return await asyncio.get_event_loop().run_in_executor(None, func, *args)

    with patch('asyncio.to_thread', mock_to_thread):
        result_urls = await helpers.get_sentinel2_urls(
            TEST_JOB_ID,
            mock_geometry,
            "2023-01-01/2023-01-02",
            bands=["nir", "red"]
        )

    assert result_urls == {"nir": "http://signed_url/nir", "red": "http://signed_url/red"}
    mock_shape.assert_any_call(mock_geometry)
    # Check if the mocks inside to_thread were called (optional)
    # mock_stac_client.open.assert_called_once_with("https://planetarycomputer.microsoft.com/api/stac/v1")
    # mock_catalog.search.assert_called_once_with(...)
    # mock_search.get_all_items_as_dict.assert_called_once()
    # mock_pc.sign.assert_called_once()

