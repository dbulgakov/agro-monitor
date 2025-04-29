import pulumi
import pulumi_azure_native as azure_native
import pulumi.asset as asset
import os
import mimetypes # To guess content type

from .config import resource_group_name, location

def create_storage_account(rg):
    account = azure_native.storage.StorageAccount(
        "storageAccount",
        resource_group_name=rg.name,
        location=location,
        sku=azure_native.storage.SkuArgs(
            name=azure_native.storage.SkuName.STANDARD_LRS
        ),
        kind=azure_native.storage.Kind.STORAGE_V2,
        enable_https_traffic_only=True,
    )
    static_website = azure_native.storage.StorageAccountStaticWebsite(
        "staticWebsite",
        account_name=account.name,
        resource_group_name=rg.name,
        index_document="index.html",
        error404_document="404.html"
    )
    
    # --- Begin Frontend Asset Upload Automation ---
    
    # Define the path to the frontend build output directory
    frontend_out_path = os.path.join(os.path.dirname(__file__), '../../frontend/out')
    
    # Check if the directory exists before attempting to upload
    if os.path.isdir(frontend_out_path):
        pulumi.log.info(f"Uploading frontend assets from: {frontend_out_path}")
        # Walk through the directory and upload each file
        for root, _, files in os.walk(frontend_out_path):
            for file in files:
                # Construct the local file path
                local_path = os.path.join(root, file)
                # Construct the blob name (relative path within the container)
                relative_path = os.path.relpath(local_path, frontend_out_path)
                # Normalize for blob storage (use forward slashes)
                blob_name = relative_path.replace("\\", "/")
                # Guess the MIME type
                content_type, _ = mimetypes.guess_type(local_path)
                
                # Create a Blob resource for each file
                azure_native.storage.Blob(f"frontend-asset-{blob_name}",
                    account_name=account.name,
                    resource_group_name=rg.name,
                    container_name="$web", # Target the static website container
                    blob_name=blob_name,
                    source=asset.FileAsset(local_path), # Use FileAsset for content
                    content_type=content_type or "application/octet-stream", # Set ContentType
                    # Ensure this depends on the static website being enabled
                    opts=pulumi.ResourceOptions(depends_on=[static_website]) 
                )
    else:
        pulumi.log.warn(f"Frontend build directory not found: {frontend_out_path}. Skipping asset upload. Run 'npm run build' in 'frontend' directory first.")
        
    # --- End Frontend Asset Upload Automation ---

    return account, static_website
