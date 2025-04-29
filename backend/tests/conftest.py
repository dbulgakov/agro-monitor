import pytest
import os
import sys

# Add project root to sys.path to allow imports like 'from functions.shared_code...'
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT_DIR)

@pytest.fixture(scope='function', autouse=True)
def set_test_environment_variables(monkeypatch):
    """Sets essential environment variables for test sessions."""
    # Default Azurite connection string
    azurite_connection_string = "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
    monkeypatch.setenv("AzureWebJobsStorage", azurite_connection_string)
    monkeypatch.setenv("ANALYSIS_QUEUE_NAME", "test-analysis-queue")
    monkeypatch.setenv("REPORTS_CONTAINER_NAME", "test-reports")
    monkeypatch.setenv("IMAGES_CONTAINER_NAME", "test-images")
    monkeypatch.setenv("STATUS_CONTAINER_NAME", "test-status") # Assuming this might be needed too
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key-optional") # Set a dummy key
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test-model")

    # Reload modules that capture environment variables at import time
    # This ensures they pick up the monkeypatched values.
    # List all modules that directly use os.getenv at the top level.
    modules_to_reload = [
        'functions.shared_code.helpers',
        'functions.AnalyzeFunction.main',
        'functions.ProcessQueueFunction.main',
        'functions.ProgressFunction.main',
        'functions.ReportFunction.main'
        # Add other relevant modules if they read env vars at import time
    ]
    import importlib
    for module_name in modules_to_reload:
        if module_name in sys.modules:
            try:
                importlib.reload(sys.modules[module_name])
                print(f"Reloaded {module_name} for test environment.") # Add print for debugging
            except ImportError:
                 print(f"Could not reload {module_name}, module not found or import error.")
            except Exception as e:
                 print(f"Error reloading {module_name}: {e}")


# --- Mocks for Azure SDK Clients ---

# Mock for BlobServiceClient and BlobClient (Synchronous)
@pytest.fixture
def mock_blob_service_client_factory(monkeypatch):
    """Provides a mocked BlobServiceClient factory and the resulting BlobClient.

    NOTE: This fixture now primarily provides the mock *objects*. Tests needing
    to ensure the BlobServiceClient isn't *actually* created should use
    @patch('functions.shared_code.helpers.get_blob_service_client') or similar.
    The environment variable fixture should allow the real SDK call to work
    against Azurite if `UseDevelopmentStorage=true` is correctly handled, but
    mocking methods on the returned client is usually needed.
    """
    mock_blob_client = Mock(spec=['exists', 'upload_blob', 'download_blob', 'set_blob_metadata', 'get_blob_properties', 'url'])
    mock_blob_client.url = "http://mockstorage/test-container/test-blob.json" # Default mock URL
    mock_blob_service_client = Mock(spec=['get_blob_client'])
    mock_blob_service_client.get_blob_client.return_value = mock_blob_client

    # Patch the factory function in the helpers module
    monkeypatch.setattr("functions.shared_code.helpers.BlobServiceClient.from_connection_string", lambda conn_str: mock_blob_service_client)
    # Also patch the get_blob_service_client helper directly to ensure it returns the mock
    monkeypatch.setattr("functions.shared_code.helpers.get_blob_service_client", lambda: mock_blob_service_client)

    print("Mock BlobServiceClient applied") # Debug print
    return mock_blob_service_client, mock_blob_client

# Mock for QueueClient (Synchronous)
@pytest.fixture
def mock_queue_client_factory(monkeypatch):
    """Provides a mocked QueueClient factory and the client instance."""
    mock_queue_client = MagicMock() # Use MagicMock for context manager support (__enter__/__exit__)
    mock_queue_client.__enter__.return_value = mock_queue_client # Return self on __enter__
    mock_queue_client.send_message = Mock()

    # Patch the factory function where it's used (e.g., AnalyzeFunction)
    # Note: The patch target depends on where `from_connection_string` is called.
    # Assuming it's called directly in AnalyzeFunction.main:
    # If QueueClient is imported directly: @patch('azure.storage.queue.QueueClient.from_connection_string')
    # If imported via helpers: @patch('functions.shared_code.helpers.QueueClient.from_connection_string')
    # Let's patch where it's likely used based on test_analyze_function
    # We need to patch it within the module where it's *called*.

    # Instead of patching here, rely on patching within the specific test functions
    # using @patch('path.to.QueueClient.from_connection_string'), returning this mock_queue_client.
    # This fixture provides the *mock object* itself.

    # Example of direct patch (if needed globally, but usually test-specific is better):
    # mock_factory = Mock(return_value=mock_queue_client)
    # monkeypatch.setattr("azure.storage.queue.QueueClient.from_connection_string", mock_factory)

    print("Mock QueueClient instance prepared") # Debug print
    return mock_queue_client # Return the instance for tests to use as return_value

# Add imports needed for mocks
from unittest.mock import Mock, MagicMock, patch
from azure.storage.queue import QueueClient # Needed for spec
from azure.storage.blob import BlobServiceClient, BlobClient # Needed for spec 