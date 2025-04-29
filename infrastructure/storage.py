import pulumi_azure_native as azure_native

from .config import resource_group_name, location

def create_storage_account(rg):
    storage_account = azure_native.storage.StorageAccount(
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
        account_name=storage_account.name,
        resource_group_name=rg.name,
        index_document="index.html",
        error_document_404_path="404.html"
    )
    return storage_account, static_website
