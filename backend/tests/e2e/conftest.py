import os
import sys
import time
from pathlib import Path
import typing

import pytest
from dotenv import load_dotenv
from starlette.testclient import TestClient
from azure.storage.blob import BlobServiceClient
from azure.storage.queue import QueueServiceClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(dotenv_path=PROJECT_ROOT / ".env.test", override=True)

@pytest.fixture(scope="function", autouse=True)
def set_test_env_vars(monkeypatch):
    """Sets environment variables for the test session."""
    monkeypatch.setenv("PROGRESS_CHECK_INTERVAL_SECONDS", "0.2") # Use a short interval for testing SSE
    monkeypatch.setenv("PROGRESS_MAX_CHECKS", "50")  # Allow enough polling iterations for completion

@pytest.fixture(scope="function")
def blob_service_client():
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        pytest.skip("AZURE_STORAGE_CONNECTION_STRING not set, skipping Azure tests.")
    client = BlobServiceClient.from_connection_string(conn_str)
    yield client

@pytest.fixture(scope="function")
def queue_service_client():
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        pytest.skip("AZURE_STORAGE_CONNECTION_STRING not set, skipping Azure tests.")
    client = QueueServiceClient.from_connection_string(conn_str)
    yield client

@pytest.fixture(scope="session")
def analysis_queue_name():
    return os.getenv("ANALYSIS_QUEUE_NAME", "analysis-queue")

@pytest.fixture(scope="session")
def images_container_name():
    return os.getenv("IMAGES_CONTAINER_NAME", "images")

@pytest.fixture(scope="session")
def reports_container_name():
    return os.getenv("REPORTS_CONTAINER_NAME", "reports-container")

@pytest.fixture(scope="function")
def analysis_queue_client(queue_service_client, analysis_queue_name):
    qc = queue_service_client.get_queue_client(analysis_queue_name)
    try:
        try:
             props = qc.get_queue_properties()
             print(f"Queue '{analysis_queue_name}' already exists.")
        except ResourceNotFoundError:
             print(f"Creating queue: {analysis_queue_name}")
             qc.create_queue()
             time.sleep(1)
    except Exception as e:
        pytest.fail(f"Failed to ensure queue '{analysis_queue_name}' exists: {e}")

    yield qc
    try:
        print(f"Clearing queue: {analysis_queue_name}")
        qc.clear_messages()
    except Exception as e:
        print(f"Warning: Failed to clear/delete queue {analysis_queue_name}: {e}")

@pytest.fixture(scope="function")
def reports_container_client(blob_service_client, reports_container_name):
    cc = blob_service_client.get_container_client(reports_container_name)
    try:
        print(f"Ensuring container exists: {reports_container_name}")
        cc.create_container()
    except ResourceExistsError:
        pass
    except Exception as e:
        pytest.fail(f"Failed to ensure container '{reports_container_name}' exists: {e}")

    yield cc
    try:
        print(f"Deleting blobs in container: {reports_container_name}")
        blobs = cc.list_blobs()
        count = 0
        for blob in blobs:
            cc.delete_blob(blob.name)
            count += 1
        print(f"Deleted {count} blobs.")
    except Exception as e:
        print(f"Warning: Failed to clean container {reports_container_name}: {e}")

@pytest.fixture(scope="function")
def images_container_client(blob_service_client, images_container_name):
    """Creates and deletes the images container for each test."""
    cc = blob_service_client.get_container_client(images_container_name)
    try:
        print(f"Ensuring container exists: {images_container_name}")
        cc.create_container()
    except ResourceExistsError:
        pass
    except Exception as e:
        pytest.fail(f"Failed to ensure container '{images_container_name}' exists: {e}")

    yield cc
    try:
        print(f"Deleting blobs in container: {images_container_name}")
        blobs = cc.list_blobs()
        count = 0
        for blob in blobs:
            cc.delete_blob(blob.name)
            count += 1
        print(f"Deleted {count} blobs.")
    except Exception as e:
        print(f"Warning: Failed to clean container {images_container_name}: {e}")

@pytest.fixture(scope="function")
def client(blob_service_client, queue_service_client):
    from function_app import fastapi_app
    fastapi_app.state.blob_service_client = blob_service_client
    fastapi_app.state.queue_service_client = queue_service_client
    with TestClient(fastapi_app) as tc:
        yield tc
