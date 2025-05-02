import pulumi
import pulumi_azure_native.resources as resources
import pulumi_azure_native.storage as storage
import pulumi_azure_native.web as web
import pulumi_azure_native.authorization as authorization
import pulumi_azure_native.insights as insights
import pulumi_azure_native.operationalinsights as operationalinsights
import os
import hashlib

config = pulumi.Config()
location = config.require("location")
repo_token = config.get_secret("repoToken")

project, stack = pulumi.get_project(), pulumi.get_stack()

unique_suffix = hashlib.sha1(f"{project}-{stack}".encode("utf-8")).hexdigest()[:6]

rg_name = f"rg-{project[:8]}-{stack[:8]}-{unique_suffix}"
sa_name = f"sa{project[:4]}{stack[:4]}{unique_suffix}"

rg = resources.ResourceGroup(
    "rg",
    resource_group_name=rg_name,
    location=location,
)

sa = storage.StorageAccount(
    "sa",
    account_name=sa_name,
    resource_group_name=rg.name,
    location=rg.location,
    sku=storage.SkuArgs(name=storage.SkuName.STANDARD_LRS),
    kind=storage.Kind.STORAGE_V2,
)

images_container = storage.BlobContainer("images", account_name=sa.name, resource_group_name=rg.name, container_name="images")
reports_container = storage.BlobContainer("reports", account_name=sa.name, resource_group_name=rg.name, container_name="reports")
queue = storage.Queue("analysis-requests", account_name=sa.name, resource_group_name=rg.name, queue_name="analysis-requests")

sa_keys = storage.list_storage_account_keys_output(resource_group_name=rg.name, account_name=sa.name)
connection_string = pulumi.Output.format(
    "DefaultEndpointsProtocol=https;AccountName={0};AccountKey={1};EndpointSuffix=core.windows.net",
    sa.name,
    sa_keys.keys[0].value,
)

func_plan = web.AppServicePlan(
    "func-plan",
    resource_group_name=rg.name,
    location=rg.location,
    kind="functionapp",
    sku=web.SkuDescriptionArgs(name="Y1", tier="Dynamic"),
    reserved=True,
)

# Create Log Analytics workspace
log_analytics_workspace = operationalinsights.Workspace(
    "log-analytics",
    resource_group_name=rg.name,
    location=rg.location,
    sku=operationalinsights.WorkspaceSkuArgs(
        name="PerGB2018"
    ),
    retention_in_days=30,
)

# Create Application Insights
app_insights = insights.Component(
    "app-insights",
    resource_group_name=rg.name,
    location=rg.location,
    kind="web",
    application_type="web",
    workspace_resource_id=log_analytics_workspace.id,
)

app_settings = [
    web.NameValuePairArgs(name="FUNCTIONS_WORKER_RUNTIME", value="python"),
    web.NameValuePairArgs(name="FUNCTIONS_EXTENSION_VERSION", value="~4"),
    web.NameValuePairArgs(name="AzureWebJobsStorage", value=connection_string),
    web.NameValuePairArgs(name="ANALYSIS_QUEUE_NAME", value=queue.name),
    web.NameValuePairArgs(name="IMAGES_CONTAINER_NAME", value="images"),
    web.NameValuePairArgs(name="REPORTS_CONTAINER_NAME", value="reports"),
    web.NameValuePairArgs(name="AZURE_STORAGE_CONNECTION_STRING", value=connection_string),
    web.NameValuePairArgs(name="APPINSIGHTS_INSTRUMENTATIONKEY", value=app_insights.instrumentation_key),
    web.NameValuePairArgs(name="APPLICATIONINSIGHTS_CONNECTION_STRING", value=app_insights.connection_string),
]

func_app = web.WebApp(
    "func-api",
    resource_group_name=rg.name,
    location=rg.location,
    server_farm_id=func_plan.id,
    kind="functionapp",
    site_config=web.SiteConfigArgs(
        app_settings=app_settings,
        linux_fx_version="Python|3.11",
        cors=web.CorsSettingsArgs(
            allowed_origins=["*"],
        ),
    ),
    identity=web.ManagedServiceIdentityArgs(type="SystemAssigned"),
)

current_config = authorization.get_client_config()
storage_queue_data_contributor_role_id = f"/subscriptions/{current_config.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/974c5e8b-45b9-4653-ba55-5f855dd0fb88"

queue_role_assignment = authorization.RoleAssignment(
    "funcQueueRoleAssignment",
    principal_id=func_app.identity.principal_id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=storage_queue_data_contributor_role_id,
    scope=sa.id,
)

# Assign Storage Blob Data Contributor role to the function app identity
storage_blob_data_contributor_role_id = f"/subscriptions/{current_config.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"

blob_role_assignment = authorization.RoleAssignment(
    "funcBlobRoleAssignment", # New resource name
    principal_id=func_app.identity.principal_id,
    principal_type=authorization.PrincipalType.SERVICE_PRINCIPAL,
    role_definition_id=storage_blob_data_contributor_role_id, # Use the blob role ID
    scope=sa.id, # Scope to the storage account
)

backend_api_url = func_app.default_host_name.apply(lambda h: f"https://{h}")

front_plan = web.AppServicePlan(
    "front-plan",
    resource_group_name=rg.name,
    location=rg.location,
    kind="app",
    sku=web.SkuDescriptionArgs(name="B1", tier="Basic"),
    reserved=True,
)

front_app = web.WebApp(
    "frontend",
    resource_group_name=rg.name,
    location=rg.location,
    server_farm_id=front_plan.id,
    kind="app",
    site_config=web.SiteConfigArgs(
        linux_fx_version="NODE|18-lts",
        app_command_line="node server.js",
        app_settings=[
            web.NameValuePairArgs(name="NEXT_PUBLIC_API_URL", value=backend_api_url),
            web.NameValuePairArgs(name="WEBSITE_RUN_FROM_PACKAGE", value="1"),
        ]
    ),
    identity=web.ManagedServiceIdentityArgs(type="SystemAssigned"),
)

pulumi.export("function_app_endpoint", backend_api_url)
pulumi.export("frontend_endpoint", front_app.default_host_name.apply(lambda h: f"https://{h}"))
pulumi.export("storage_account_name", sa.name)
pulumi.export("analysis_queue_name", queue.name)
pulumi.export("images_container_name", images_container.name)
pulumi.export("reports_container_name", reports_container.name)

pulumi.export("resource_group_name", rg.name)
pulumi.export("function_app_name", func_app.name)
pulumi.export("frontend_app_name", front_app.name)
pulumi.export("function_app_principal_id", func_app.identity.principal_id)
pulumi.export("app_insights_name", app_insights.name)
pulumi.export("app_insights_instrumentation_key", app_insights.instrumentation_key)
pulumi.export("log_analytics_workspace_name", log_analytics_workspace.name)
pulumi.export("log_analytics_workspace_id", log_analytics_workspace.id)
