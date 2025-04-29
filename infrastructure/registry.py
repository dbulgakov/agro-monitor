import pulumi
import pulumi_azure_native as azure_native

def create_container_registry(rg):
    """Creates an Azure Container Registry."""
    stack = pulumi.get_stack()
    # ACR names must be globally unique, alphanumeric, 5-50 characters.
    registry_name = f"acragromonitor{stack}".lower()[:50]

    registry = azure_native.containerregistry.Registry(f"containerRegistry-{stack}", # Pulumi name
        resource_group_name=rg.name,
        registry_name=registry_name, # Azure name
        sku=azure_native.containerregistry.SkuArgs(name="Basic"), # Choose Basic, Standard, or Premium
        admin_user_enabled=True # Enable admin user for simpler authentication (alternatively use service principal/managed identity)
    )
    pulumi.export(f"registryLoginServer_{stack}", registry.login_server) # Stack-specific export
    return registry

def get_registry_credentials(rg, registry):
    """Retrieves ACR credentials."""
    # Credentials depend on the registry name which is now stack-dependent
    # The logic correctly references registry.name, which will have the stack-specific name
    creds = pulumi.Output.all(rg.name, registry.name).apply(
        lambda args: azure_native.containerregistry.list_registry_credentials(resource_group_name=args[0], registry_name=args[1])
    )
    return creds

# Usage in function_app.py will involve:
# registry = create_container_registry(rg)
# creds = get_registry_credentials(rg, registry)
# image = pulumi_docker.Image(...)
# app_settings with DOCKER_REGISTRY_SERVER_URL, USERNAME, PASSWORD 