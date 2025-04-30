import pulumi
import pulumi_azure_native.resources as resources
import pulumi_azure_native.storage as storage
import pulumi_azure_native.insights as insights
import pulumi_azure_native.web as web
import pulumi_azure_native.documentdb as documentdb

config = pulumi.Config()
location = config.require("azure-native:location")
git_repo_url = config.require("azure-fastapi-demo:gitRepoUrl")
git_branch = config.require("azure-fastapi-demo:gitBranch")
openai_api_key = config.require("azure-fastapi-demo:openaiApiKey")

base_name = "fastapidemo"
rg = resources.ResourceGroup(f"{base_name}-rg", location=location)

storage_account = storage.StorageAccount(
    f"{base_name}sa",
    resource_group_name=rg.name,
    sku=storage.SkuArgs(name=storage.SkuName.STANDARD_LRS),
    kind=storage.Kind.STORAGE_V2,
    location=location,
)
storage_keys = storage.list_storage_account_keys_output(
    resource_group_name=rg.name,
    account_name=storage_account.name,
)
storage_conn = storage_keys.keys[0].value.apply(
    lambda key: f"DefaultEndpointsProtocol=https;AccountName={storage_account.name};AccountKey={key};EndpointSuffix=core.windows.net"
)

ai = insights.Component(
    f"{base_name}-ai",
    resource_group_name=rg.name,
    kind="web",
    application_type=insights.ApplicationType.WEB,
    location=location,
)

cosmosdb = documentdb.DatabaseAccount(
    f"{base_name}-cosmos",
    resource_group_name=rg.name,
    locations=[documentdb.LocationArgs(location_name=location)],
    kind="GlobalDocumentDB",
    database_account_offer_type=documentdb.DatabaseAccountOfferType.STANDARD,
)
cosmos_conn = documentdb.list_database_account_connection_strings_output(
    resource_group_name=rg.name,
    account_name=cosmosdb.name,
).connection_strings[0].connection_string

func_plan = web.AppServicePlan(
    f"{base_name}-func-plan",
    resource_group_name=rg.name,
    kind="Linux",
    reserved=True,
    sku=web.SkuDescriptionArgs(
        tier="Dynamic",
        name="Y1",
    ),
)

func_app = web.WebApp(
    f"{base_name}-func-app-{pulumi.get_stack()}",
    resource_group_name=rg.name,
    name=f"{base_name}-func-app-{pulumi.get_stack()}",
    location=location,
    server_farm_id=func_plan.id,
    kind="functionapp,linux",
    reserved=True,
    site_config=web.SiteConfigArgs(
        linux_fx_version="Python|3.11",
        app_settings=[
            web.NameValuePairArgs(name="FUNCTIONS_WORKER_RUNTIME", value="python"),
            web.NameValuePairArgs(name="FUNCTIONS_EXTENSION_VERSION", value="~4"),
            web.NameValuePairArgs(name="AzureWebJobsStorage", value=storage_conn),
            web.NameValuePairArgs(name="AZURE_STORAGE_CONNECTION_STRING", value=storage_conn),
            web.NameValuePairArgs(name="WEBSITE_RUN_FROM_PACKAGE", value="1"),
            web.NameValuePairArgs(name="APPINSIGHTS_INSTRUMENTATIONKEY", value=ai.instrumentation_key),
            web.NameValuePairArgs(name="COSMOSDB_CONNECTION_STRING", value=cosmos_conn),
            web.NameValuePairArgs(name="OPENAI_API_KEY", value=openai_api_key),
            web.NameValuePairArgs(name="ANALYSIS_QUEUE_NAME", value="analysis-requests"),
            web.NameValuePairArgs(name="REPORTS_CONTAINER_NAME", value="reports"),
        ],
    ),
    https_only=True,
)

web.WebAppSourceControl(
    "backend-sc",
    name=func_app.name,
    resource_group_name=rg.name,
    repo_url=git_repo_url,
    branch=git_branch,
    is_manual_integration=True,
    is_git_hub_action=False,
    opts=pulumi.ResourceOptions(depends_on=[func_app]),
)

web_app = web.WebApp(
    f"{base_name}-web-app-{pulumi.get_stack()}",
    resource_group_name=rg.name,
    name=f"{base_name}-web-app-{pulumi.get_stack()}",
    location=location,
    server_farm_id=func_plan.id,
    kind="app,linux",
    reserved=True,
    site_config=web.SiteConfigArgs(
        linux_fx_version="NODE|18-lts",
        app_settings=[
            web.NameValuePairArgs(name="SCM_DO_BUILD_DURING_DEPLOYMENT", value="true"),
            web.NameValuePairArgs(name="WEBSITE_NODE_DEFAULT_VERSION", value="~18"),
            web.NameValuePairArgs(name="PROJECT", value="frontend"),
        ],
    ),
    https_only=True,
)

web.WebAppSourceControl(
    "frontend-sc",
    name=web_app.name,
    resource_group_name=rg.name,
    repo_url=git_repo_url,
    branch=git_branch,
    is_manual_integration=True,
    is_git_hub_action=False,
    opts=pulumi.ResourceOptions(depends_on=[web_app]),
)

pulumi.export("backend_endpoint", func_app.default_host_name.apply(lambda h: f"https://{h}/api"))
pulumi.export("frontend_endpoint", web_app.default_host_name.apply(lambda h: f"https://{h}"))
