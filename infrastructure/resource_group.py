import pulumi_azure_native as azure_native
from .config import resource_group_name, location

def create_resource_group():
    return azure_native.resources.ResourceGroup(
        "resourceGroup",
        resource_group_name=resource_group_name,
        location=location
    )
