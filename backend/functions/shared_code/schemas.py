from pydantic import BaseModel, Field, field_validator, ValidationError
from typing import List, Literal, Union, Optional, Tuple, Any, Dict
from enum import Enum
from datetime import datetime

class Frequency(str, Enum):
    SINGLE = "single"
    WEEKLY = "weekly"

class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

class GeoJsonType(str, Enum):
    POINT = "Point"
    POLYGON = "Polygon"
    FEATURE = "Feature"

class GeoJsonPointGeometry(BaseModel):
    type: Literal[GeoJsonType.POINT]
    coordinates: Tuple[float, float]

class GeoJsonPolygonGeometry(BaseModel):
    type: Literal[GeoJsonType.POLYGON]
    coordinates: List[List[Tuple[float, float]]]

class GeoJsonFeaturePoint(BaseModel):
    type: Literal[GeoJsonType.FEATURE]
    geometry: GeoJsonPointGeometry
    properties: Optional[dict] = None

class GeoJsonFeaturePolygon(BaseModel):
    type: Literal[GeoJsonType.FEATURE]
    geometry: GeoJsonPolygonGeometry
    properties: Optional[dict] = None

class GeometryType(str, Enum):
    POLYGON = "Polygon"
    POINT = "Point"

class GeoJsonGeometry(BaseModel):
    type: GeometryType
    coordinates: Any

class AreaOfInterest(BaseModel):
    type: str = Field("Feature", frozen=True)
    geometry: GeoJsonGeometry
    properties: Optional[Dict[str, Any]] = None

class StartAnalysisPayload(BaseModel):
    area: AreaOfInterest
    frequency: Frequency
    ndvi_threshold: float = Field(..., ge=0, lt=1)
    date_range: str = Field(..., description="Date range in ISO 8601 interval format, e.g., '2023-01-01/2023-02-01'")
    max_cloud_cover: float = Field(..., ge=0, le=100)
    crop_type: str = Field(..., description="Type of crop being analyzed")

    @field_validator('date_range')
    @classmethod
    def check_date_range_format(cls, v: str) -> str:
        try:
            start_str, end_str = v.split('/')
            start_date = datetime.strptime(start_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_str, '%Y-%m-%d')
            if start_date >= end_date:
                raise ValueError("Start date must be before end date")
        except ValueError as e:
            raise ValueError(
                f'Invalid date_range format or value: "{v}". ' 
                f'Expected YYYY-MM-DD/YYYY-MM-DD with start date before end date. Error: {e}'
            )
        return v

    @field_validator('area')
    @classmethod
    def check_polygon_closure(cls, v: AreaOfInterest) -> AreaOfInterest:
        if v.geometry.type == GeometryType.POLYGON:
            for ring in v.geometry.coordinates:
                if not ring:
                    raise ValueError("Polygon ring cannot be empty.")
                if len(ring) < 4:
                     raise ValueError(f"Polygon ring must have at least 4 points. Got {len(ring)}.")
                if ring[0] != ring[-1]:
                    raise ValueError(f"Polygon ring must be closed. First: {ring[0]}, Last: {ring[-1]}")
        return v

class StartAnalysisResponse(BaseModel):
    jobId: str = Field(..., description="Unique identifier for the analysis job")

class ReportData(BaseModel):
    jobId: str
    status: JobStatus
    requestPayload: Optional[StartAnalysisPayload] = None
    reportTimestamp: Optional[str] = None
    ndviStatistics: Optional[Dict[str, Optional[float]]] = None
    mapUrls: Optional[Dict[str, Optional[str]]] = None
    recommendations: Optional[str] = None
    errorMessage: Optional[str] = None

class ErrorResponse(BaseModel):
    message: str
    details: Optional[Any] = None

class ProgressUpdate(BaseModel):
    jobId: str
    status: JobStatus
    progress: int = Field(..., ge=-1, le=100)
    message: Optional[str] = None
    timestamp: float

class JobStatusData(BaseModel):
    status: JobStatus
    progress: int
    message: Optional[str] = None