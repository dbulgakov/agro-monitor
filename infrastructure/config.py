import pulumi
from pulumi import Config

azure_config = Config("azure")
location = azure_config.get("location") or "EastUS"
resource_group_name = azure_config.get("resourceGroupName") or "agro-monitor-rg"
