import pulumi
import pulumi_azure_native as azure_native

def create_container_registry(rg):
    """Creates an Azure Container Registry."""
    registry = azure_native.containerregistry.Registry("containerRegistry",
        resource_group_name=rg.name,
        sku=azure_native.containerregistry.SkuArgs(name="Basic"), # Choose Basic, Standard, or Premium
        admin_user_enabled=True # Enable admin user for simpler authentication (alternatively use service principal/managed identity)
    )
    pulumi.export("registryLoginServer", registry.login_server)
    return registry

def get_registry_credentials(rg, registry):
    """Retrieves ACR credentials."""
    creds = pulumi.Output.all(rg.name, registry.name).apply(
        lambda args: azure_native.containerregistry.list_registry_credentials(resource_group_name=args[0], registry_name=args[1])
    )
    return creds

# Usage in function_app.py will involve:
# registry = create_container_registry(rg)
# creds = get_registry_credentials(rg, registry)
# image = pulumi_docker.Image(...)
# app_settings with DOCKER_REGISTRY_SERVER_URL, USERNAME, PASSWORD 