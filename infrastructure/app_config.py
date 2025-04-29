import pulumi
import pulumi_azure_native as azure_native

from .config import location # Assuming location is defined in config.py

def create_app_configuration_store(rg):
    """Creates an Azure App Configuration store."""
    stack = pulumi.get_stack()
    store_name = f"appcs-agromonitor-{stack}"
    store = azure_native.appconfiguration.ConfigurationStore(f"appConfigStore-{stack}", # Pulumi name
        resource_group_name=rg.name,
        location=location, # Use the location from config
        config_store_name=store_name, # Azure name
        sku=azure_native.appconfiguration.SkuArgs(
            name="Standard", # Choose Free or Standard tier
        )
    )
    pulumi.export(f"appConfigurationEndpoint_{stack}", store.endpoint) # Stack-specific export
    return store

def add_app_configuration_key_value(rg, store, key, value, content_type="text/plain", tags=None):
    """Adds a Key-Value pair to the App Configuration store."""
    stack = pulumi.get_stack()
    # Resource name needs sanitization and stack prefix
    safe_key_part = key.replace(':', '-').replace('_', '-').lower()
    kv_resource_name = f"kv-{stack}-{safe_key_part}"

    kv = azure_native.appconfiguration.KeyValue(kv_resource_name,
        config_store_name=store.name,
        resource_group_name=rg.name,
        key_value_name=key, # Use the actual key here
        key=key,
        value=value,
        content_type=content_type,
        tags=tags or {},
        opts=pulumi.ResourceOptions(parent=store) # Link to the store
    )
    return kv

# Example Usage (will be called from __main__.py):
# store = create_app_configuration_store(rg)
# add_app_configuration_key_value(rg, store, "PlanetaryComputer:ApiKey", "YOUR_PC_API_KEY_HERE") # Replace with actual value source (e.g., Pulumi config)
# add_app_configuration_key_value(rg, store, "OpenAI:ApiKey", "YOUR_OPENAI_KEY_HERE")
# add_app_configuration_key_value(rg, store, "Settings:DefaultNdviThreshold", "0.3") 