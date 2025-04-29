import pulumi
import pulumi_azure_native as azure_native
from .config import location

def create_resource_group():
    stack = pulumi.get_stack()
    resource_group_name = f"rg-agromonitor-{stack}"
    return azure_native.resources.ResourceGroup(
        f"rg-{stack}",
        resource_group_name=resource_group_name,
        location=location
    )
