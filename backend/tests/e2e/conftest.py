import os
import sys
import asyncio
from pathlib import Path

import pytest
from dotenv import load_dotenv
from httpx import AsyncClient
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.queue.aio import QueueServiceClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(dotenv_path=PROJECT_ROOT / ".env.test", override=True)

@pytest.fixture(scope="function")
async def blob_service_client():
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    client = BlobServiceClient.from_connection_string(conn_str)
    yield client
    await client.close()

@pytest.fixture(scope="function")
async def queue_service_client():
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    client = QueueServiceClient.from_connection_string(conn_str)
    yield client
    await client.close()

@pytest.fixture(scope="session")
def analysis_queue_name():
    return os.getenv("ANALYSIS_QUEUE_NAME", "analysis-queue")

@pytest.fixture(scope="session")
def reports_container_name():
    return os.getenv("REPORTS_CONTAINER_NAME", "reports-container")

@pytest.fixture(scope="function")
async def analysis_queue_client(queue_service_client, analysis_queue_name):
    qc = queue_service_client.get_queue_client(analysis_queue_name)
    try:
        await qc.create_queue()
    except ResourceExistsError:
        pass
    yield qc
    try:
        await qc.delete_queue()
    except ResourceNotFoundError:
        pass

@pytest.fixture(scope="function")
async def reports_container_client(blob_service_client, reports_container_name):
    cc = blob_service_client.get_container_client(reports_container_name)
    try:
        await cc.create_container()
    except ResourceExistsError:
        pass
    yield cc
    try:
        await cc.delete_container()
    except ResourceNotFoundError:
        pass

@pytest.fixture(scope="function")
async def client(blob_service_client):
    from app import app
    app.state.blob_service_client = blob_service_client
    async with AsyncClient(app=app, base_url="http://testserver") as ac:
        yield ac
