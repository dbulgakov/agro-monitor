import pytest
from pydantic import ValidationError
from functions.shared_code.schemas import (
    StartAnalysisPayload,
    Frequency,
    GeoJsonPointGeometry,
    GeoJsonPolygonGeometry,
    GeoJsonFeaturePoint,
    GeoJsonFeaturePolygon,
    GeoJsonGeometry,
    GeometryType
)

# --- Test Data ---
VALID_POINT_GEOM = {"type": "Point", "coordinates": [34.5, 49.6]}
VALID_POLYGON_GEOM = {
    "type": "Polygon",
    "coordinates": [[ [34.4, 49.5], [34.6, 49.5], [34.6, 49.7], [34.4, 49.7], [34.4, 49.5] ]]
}

VALID_FEATURE_POINT = {"type": "Feature", "geometry": VALID_POINT_GEOM, "properties": {}}
VALID_FEATURE_POLYGON = {"type": "Feature", "geometry": VALID_POLYGON_GEOM, "properties": {}}

VALID_PAYLOAD_POLYGON = {
    "area": VALID_FEATURE_POLYGON,
    "frequency": "single",
    "ndvi_threshold": 0.3,
    "date_range": "2024-01-01/2024-05-01",
    "max_cloud_cover": 25,
    "crop_type": "Wheat"
}

VALID_PAYLOAD_POINT = {
    "area": VALID_FEATURE_POINT,
    "frequency": "weekly",
    "ndvi_threshold": 0.8,
    "date_range": "2023-10-01/2024-10-01",
    "max_cloud_cover": 10,
    "crop_type": "Corn"
}

# --- Tests for StartAnalysisPayload ---
def test_start_analysis_payload_valid_polygon():
    """Test successful validation with valid polygon data."""
    payload = StartAnalysisPayload.model_validate(VALID_PAYLOAD_POLYGON)
    assert payload.area.geometry.type == "Polygon"
    assert payload.frequency == Frequency.SINGLE
    assert payload.ndvi_threshold == 0.3
    assert payload.crop_type == "Wheat"

def test_start_analysis_payload_valid_point():
    """Test successful validation with valid point data."""
    payload = StartAnalysisPayload.model_validate(VALID_PAYLOAD_POINT)
    assert payload.area.geometry.type == "Point"
    assert payload.frequency == Frequency.WEEKLY
    assert payload.ndvi_threshold == 0.8


@pytest.mark.parametrize(
    "invalid_data, expected_error_part",
    [
        ({"date_range": "2024-05-01/2024-01-01"}, "Start date must be before end date"), # Invalid date order
        ({"date_range": "2024-01-01-2024-05-01"}, "Invalid date_range format"),       # Invalid date format
        ({"date_range": "2024/01/01/2024/05/01"}, "Invalid date_range format"),      # Invalid date format
        ({"ndvi_threshold": 1.1}, "ndvi_threshold"),                                  # NDVI too high
        ({"ndvi_threshold": -0.1}, "ndvi_threshold"),                                 # NDVI too low
        ({"max_cloud_cover": 101}, "max_cloud_cover"),                               # Cloud cover too high
        ({"max_cloud_cover": -1}, "max_cloud_cover"),                                # Cloud cover too low
        ({"frequency": "daily"}, "frequency"),                                       # Invalid frequency
        ({"area": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": []}}}, "area"), # Invalid area type
    ],
)
def test_start_analysis_payload_invalid_fields(invalid_data, expected_error_part):
    """Test validation errors for various invalid field values."""
    data = VALID_PAYLOAD_POLYGON.copy()
    data.update(invalid_data)
    with pytest.raises(ValidationError) as exc_info:
        StartAnalysisPayload.model_validate(data)
    assert expected_error_part in str(exc_info.value)


# --- Tests for GeoJSON Polygon Validation ---

def test_polygon_geometry_valid():
    """Test valid polygon geometry passes validation."""
    # Tested implicitly via VALID_PAYLOAD_POLYGON
    payload = StartAnalysisPayload.model_validate(VALID_PAYLOAD_POLYGON)
    # Check isinstance against base type and assert the type attribute
    assert isinstance(payload.area.geometry, GeoJsonGeometry)
    assert payload.area.geometry.type == GeometryType.POLYGON
    # Ensure coordinates structure matches polygon (list of list of lists)
    assert isinstance(payload.area.geometry.coordinates, list)
    assert isinstance(payload.area.geometry.coordinates[0], list)
    # Coordinates are lists of numbers [lon, lat]
    assert isinstance(payload.area.geometry.coordinates[0][0], list)


@pytest.mark.parametrize(
    "invalid_coords, expected_error",
    [
        ([[[34.4, 49.5], [34.6, 49.5], [34.6, 49.7]]], "at least 4 points"), # Not enough points
        ([[[34.4, 49.5], [34.6, 49.5], [34.6, 49.7], [34.4, 49.7]]], "must be closed"), # Not closed
        ([[]], "cannot be empty"), # Empty ring
        ([[[34.4, 49.5], [34.6, 49.5], [34.6, 49.7], [34.4, 49.7], [34.4, 49.5]], []], "cannot be empty"), # Inner empty ring
    ]
)
def test_polygon_geometry_invalid(invalid_coords, expected_error):
    """Test invalid polygon coordinates raise validation errors."""
    data = VALID_PAYLOAD_POLYGON.copy()
    data["area"] = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": invalid_coords},
        "properties": {}
    }
    with pytest.raises(ValidationError) as exc_info:
        StartAnalysisPayload.model_validate(data)
    assert expected_error in str(exc_info.value)


# Potential TODO: Add tests for ReportData, ErrorResponse, ProgressUpdate if they gain complex validation
