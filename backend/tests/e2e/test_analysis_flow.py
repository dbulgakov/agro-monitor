import asyncio
import json

import pytest
from fastapi import status
from functions.lib.schemas import JobStatus
from functions.lib.helpers.job_status import update_job_status

async def test_successful_analysis_flow(
    client,
    analysis_queue_client,
    reports_container_client,
    reports_container_name,
    blob_service_client
):
    test_payload = {
        "area": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon", 
                "coordinates": [[[30, 50], [30.1, 50], [30.1, 50.1], [30, 50.1], [30, 50]]]}
        },
        "date_range": "2024-01-01/2024-01-31",
        "crop_type": "wheat",
        "frequency": "single",
        "ndvi_threshold": 0.3,
        "max_cloud_cover": 20
    }
    response = await client.post("/api/analyze", json=test_payload)
    assert response.status_code == status.HTTP_202_ACCEPTED
    job_id = response.json()["jobId"]

    messages = [m async for m in analysis_queue_client.receive_messages(max_messages=1)]
    assert messages
    message = messages[0]

    await update_job_status(blob_service_client, job_id, JobStatus.COMPLETED, 100, "Analysis complete")
    blob = reports_container_client.get_blob_client(f"{job_id}/report.json")
    report_data = {"jobId": job_id, "fieldId": "test", "result": "ok", "status": JobStatus.COMPLETED.value}
    await blob.upload_blob(json.dumps(report_data), overwrite=True)
    await analysis_queue_client.delete_message(message)

    report_response = await client.get(f"/api/report/{job_id}")
    assert report_response.status_code == 200
    assert report_response.json()["status"] == JobStatus.COMPLETED.value

async def test_analyze_invalid_payload(client):
    response = await client.post("/api/analyze", json={"bad": "data"})
    assert response.status_code == 422

async def test_progress_for_nonexistent_job(client):
    response = await client.get("/api/progress/nonexistent-job")
    assert response.status_code in {200, 404}

async def test_report_for_nonexistent_job(client):
    response = await client.get("/api/report/nonexistent-job")
    assert response.status_code == 404

async def test_job_failure_simulation(
    client,
    reports_container_client,
    reports_container_name,
    blob_service_client
):
    job_id = "fail-job"
    await update_job_status(blob_service_client, job_id, JobStatus.FAILED, -1, "Simulated failure")
    response = await client.get(f"/api/report/{job_id}")
    assert response.status_code in {200, 202}
    assert response.json()["detail"]["status"] == "FAILED"
