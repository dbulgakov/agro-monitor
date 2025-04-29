import os
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.queue.aio import QueueServiceClient
import logging

# Use Managed Identity (or local credentials like Azure CLI login)
credential = DefaultAzureCredential()

# Get configuration from environment variables
storage_account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME") # Need to set this in config
queue_storage_uri = f"https://{storage_account_name}.queue.core.windows.net"
blob_storage_uri = f"https://{storage_account_name}.blob.core.windows.net"

# Ensure the storage account name is provided
if not storage_account_name:
    logging.warning("AZURE_STORAGE_ACCOUNT_NAME environment variable is not set. Azure Storage clients will not be initialized.")
    blob_service_client = None
    queue_service_client = None
else:
    try:
        # Initialize async clients
        blob_service_client = BlobServiceClient(account_url=blob_storage_uri, credential=credential)
        queue_service_client = QueueServiceClient(account_url=queue_storage_uri, credential=credential)
        logging.info(f"Initialized Azure Storage clients for account: {storage_account_name}")
    except Exception as e:
        logging.error(f"Failed to initialize Azure Storage clients for account {storage_account_name}: {e}", exc_info=True)
        blob_service_client = None
        queue_service_client = None

async def get_blob_service_client() -> BlobServiceClient | None:
    """Returns the initialized async BlobServiceClient instance."""
    # In a real app, you might manage the lifecycle or use dependency injection
    if not blob_service_client:
         logging.error("BlobServiceClient is not available. Check configuration and logs.")
    return blob_service_client

async def get_queue_service_client() -> QueueServiceClient | None:
    """Returns the initialized async QueueServiceClient instance."""
    if not queue_service_client:
         logging.error("QueueServiceClient is not available. Check configuration and logs.")
    return queue_service_client

# Example Usage (within async functions):
# async def some_function():
#     blob_client = await get_blob_service_client()
#     if blob_client:
#         async with blob_client: # Use async context manager
#             # ... use client ...
#             container_client = blob_client.get_container_client("mycontainer")
#
#     queue_client = await get_queue_service_client()
#     if queue_client:
#          async with queue_client:
#              queue = queue_client.get_queue_client("myqueue")
#              await queue.send_message("Hello World") 