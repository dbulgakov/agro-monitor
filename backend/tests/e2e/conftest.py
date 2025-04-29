import asyncio
import os
import sys
from pathlib import Path
import gc

import pytest
from dotenv import load_dotenv
from httpx import AsyncClient
import aiohttp
from azure.storage.queue.aio import QueueServiceClient
from azure.storage.blob.aio import BlobServiceClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

load_dotenv(dotenv_path=project_root / ".env.test")

from app import app, lifespan


@pytest.fixture(scope="function")
async def client():
    try:
        async with lifespan(app):
            async with AsyncClient(app=app, base_url="http://test") as ac:
                yield ac
    finally:
        # Attempt to find and close lingering aiohttp sessions/connectors
        closed_something = False
        for obj in gc.get_objects():
            if isinstance(obj, aiohttp.ClientSession):
                if not obj.closed:
                    try:
                        await obj.close()
                        closed_something = True
                    except Exception:
                        pass # Ignore errors during cleanup
            # Check for BaseConnector as well, as sessions might be closed but connector lingers
            elif isinstance(obj, aiohttp.BaseConnector):
                 if not obj.closed:
                    try:
                        await obj.close()
                        closed_something = True
                    except Exception:
                        pass # Ignore errors during cleanup

        # Give loop a tick to process closures if any happened
        if closed_something:
            await asyncio.sleep(0)


@pytest.fixture(scope="session")
def azure_storage_connection_string():
    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        pytest.fail("AZURE_STORAGE_CONNECTION_STRING environment variable not set.")
    return conn


@pytest.fixture(scope="session")
def analysis_queue_name():
    name = os.getenv("ANALYSIS_QUEUE_NAME")
    if not name:
        pytest.fail("ANALYSIS_QUEUE_NAME environment variable not set.")
    return name


@pytest.fixture(scope="session")
def reports_container_name():
    name = os.getenv("REPORTS_CONTAINER_NAME")
    if not name:
        pytest.fail("REPORTS_CONTAINER_NAME environment variable not set.")
    return name


@pytest.fixture(scope="function")
async def queue_service_client(azure_storage_connection_string):
    async with QueueServiceClient.from_connection_string(azure_storage_connection_string) as client:
        yield client


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
async def blob_service_client(azure_storage_connection_string):
    async with BlobServiceClient.from_connection_string(azure_storage_connection_string) as client:
        yield client


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
