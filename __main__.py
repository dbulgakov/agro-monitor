import pulumi
from infrastructure.config import location, resource_group_name
from infrastructure.resource_group import create_resource_group
from infrastructure.storage import create_storage_account
from infrastructure.app_insights import create_app_insights
from infrastructure.plan import create_consumption_plan
from infrastructure.function_app import create_function_app
from infrastructure.frontend import export_frontend_url
from infrastructure.app_config import create_app_configuration_store, add_app_configuration_key_value
from infrastructure.registry import create_container_registry

# Load Pulumi config for potential secrets
config = pulumi.Config()
# Example: Get secrets from Pulumi config (use --secret flag when setting)
# pc_api_key = config.get_secret("planetaryComputerApiKey")
# openai_api_key = config.get_secret("openaiApiKey")

# Create resources
rg = create_resource_group()
storage_account, static_website = create_storage_account(rg)
app_insights = create_app_insights(rg)
plan = create_consumption_plan(rg)
app_config_store = create_app_configuration_store(rg)
registry = create_container_registry(rg)

# Add example configuration values (replace with actual values/config references)
add_app_configuration_key_value(rg, app_config_store, "Settings:DefaultNdviThreshold", "0.3")
# if pc_api_key:
#     add_app_configuration_key_value(rg, app_config_store, "PlanetaryComputer:ApiKey", pc_api_key)
# if openai_api_key:
#     add_app_configuration_key_value(rg, app_config_store, "OpenAI:ApiKey", openai_api_key)

# Pass App Config connection string to Function App
func_app = create_function_app(rg, plan, storage_account, app_insights, app_config_store, registry)
export_frontend_url(storage_account)
