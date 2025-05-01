import pulumi
import pulumi_azure_native.resources as resources
import pulumi_azure_native.storage as storage
import pulumi_azure_native.insights as insights
import pulumi_azure_native.web as web
import pulumi_azure_native.documentdb as documentdb
from pulumi import ResourceOptions

config = pulumi.Config()
location = config.require("location")
git_repo_url = config.require("gitRepoUrl")
git_branch = config.require("gitBranch")
openai_api_key = config.require("openaiApiKey")

retry_policy = ResourceOptions()
base_name = "agromonitor"

rg = resources.ResourceGroup(f"{base_name}-rg", location=location, opts=retry_policy)

storage_account = storage.StorageAccount(
    f"{base_name}sa",
    resource_group_name=rg.name,
    sku=storage.SkuArgs(name=storage.SkuName.STANDARD_LRS),
    kind=storage.Kind.STORAGE_V2,
    location=location,
    opts=retry_policy,
)

storage_keys = storage.list_storage_account_keys_output(
    resource_group_name=rg.name,
    account_name=storage_account.name,
)
primary_storage_key = storage_keys.keys[0].value
storage_conn = pulumi.Output.all(storage_account.name, primary_storage_key).apply(
    lambda args: f"DefaultEndpointsProtocol=https;AccountName={args[0]};AccountKey={args[1]};EndpointSuffix=core.windows.net"
)

analysis_queue = storage.Queue(
    "analysis-requests",
    resource_group_name=rg.name,
    account_name=storage_account.name,
    queue_name="analysis-requests",
    opts=retry_policy,
)

reports_container = storage.BlobContainer(
    "reports-container",
    resource_group_name=rg.name,
    account_name=storage_account.name,
    container_name="reports",
    opts=retry_policy,
)

images_container = storage.BlobContainer(
    "images-container",
    resource_group_name=rg.name,
    account_name=storage_account.name,
    container_name="images",
    opts=retry_policy,
)

ai = insights.Component(
    f"{base_name}-ai",
    resource_group_name=rg.name,
    kind="web",
    application_type=insights.ApplicationType.WEB,
    location=location,
    ingestion_mode="ApplicationInsights",
    opts=retry_policy,
)

cosmosdb_account = documentdb.DatabaseAccount(
    f"{base_name}-cosmos",
    resource_group_name=rg.name,
    locations=[documentdb.LocationArgs(location_name=location, failover_priority=0)],
    kind="GlobalDocumentDB",
    database_account_offer_type=documentdb.DatabaseAccountOfferType.STANDARD,
    consistency_policy=documentdb.ConsistencyPolicyArgs(
        default_consistency_level=documentdb.DefaultConsistencyLevel.SESSION,
    ),
    capabilities=[documentdb.CapabilityArgs(name="EnableServerless")],
    opts=retry_policy,
)

cosmos_conn_string = cosmosdb_account.name.apply(
    lambda name: documentdb.list_database_account_connection_strings_output(
        resource_group_name=rg.name,
        account_name=name,
    ).connection_strings[0].connection_string
)

func_rg = resources.ResourceGroup(f"{base_name}-func-rg", location=location, opts=retry_policy)

func_plan = web.AppServicePlan(
    f"{base_name}-func-plan",
    resource_group_name=func_rg.name,
    kind="FunctionApp",
    reserved=True,
    sku=web.SkuDescriptionArgs(tier="Dynamic", name="Y1"),
    location=location,
    opts=retry_policy,
)

fastapi_func_app = web.WebApp(
    f"{base_name}-func-api",
    resource_group_name=func_rg.name,
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
            web.NameValuePairArgs(name="APPINSIGHTS_INSTRUMENTATIONKEY", value=ai.instrumentation_key),
            web.NameValuePairArgs(name="COSMOSDB_CONNECTION_STRING", value=cosmos_conn_string),
            web.NameValuePairArgs(name="OPENAI_API_KEY", value=openai_api_key),
            web.NameValuePairArgs(name="ANALYSIS_QUEUE_NAME", value=analysis_queue.name),
            web.NameValuePairArgs(name="REPORTS_CONTAINER_NAME", value=reports_container.name),
            web.NameValuePairArgs(name="IMAGES_CONTAINER_NAME", value=images_container.name),
            web.NameValuePairArgs(name="PYTHON_ENABLE_WORKER_EXTENSIONS", value="1"),
            web.NameValuePairArgs(name="FUNCTIONS_WORKING_DIRECTORY", value="functions"),
        ]
    ),
    https_only=True,
    opts=retry_policy,
)

web_plan = web.AppServicePlan(
    f"{base_name}-web-plan",
    resource_group_name=rg.name,
    kind="Linux",
    reserved=True,
    sku=web.SkuDescriptionArgs(tier="Free", name="F1"),
    location=location,
    opts=retry_policy,
)

web_app = web.WebApp(
    f"{base_name}-web-app",
    resource_group_name=rg.name,
    location=location,
    server_farm_id=web_plan.id,
    kind="app,linux",
    reserved=True,
    site_config=web.SiteConfigArgs(
        linux_fx_version="NODE|18",
        app_settings=[
            web.NameValuePairArgs(name="SCM_DO_BUILD_DURING_DEPLOYMENT", value="true"),
            web.NameValuePairArgs(name="PROJECT", value="frontend"),
            web.NameValuePairArgs(
                name="NEXT_PUBLIC_API_URL",
                value=fastapi_func_app.default_host_name.apply(lambda h: f"https://{h}")
            ),
        ],
        always_on=False,
    ),
    https_only=True,
    opts=retry_policy,
)

web.WebAppSourceControl(
    "backend-sc",
    name=fastapi_func_app.name,
    resource_group_name=func_rg.name,
    repo_url=git_repo_url,
    branch=git_branch,
    is_manual_integration=True,
    opts=ResourceOptions(depends_on=[fastapi_func_app]),
)

web.WebAppSourceControl(
    "frontend-sc",
    name=web_app.name,
    resource_group_name=rg.name,
    repo_url=git_repo_url,
    branch=git_branch,
    is_manual_integration=True,
    opts=ResourceOptions(depends_on=[web_app]),
)

pulumi.export("backend_endpoint", fastapi_func_app.default_host_name.apply(lambda h: f"https://{h}"))
pulumi.export("frontend_endpoint", web_app.default_host_name.apply(lambda h: f"https://{h}"))
pulumi.export("storage_account_name", storage_account.name)
pulumi.export("analysis_queue_name", analysis_queue.name)
pulumi.export("reports_container_name", reports_container.name)
pulumi.export("images_container_name", images_container.name)
pulumi.export("cosmosdb_account_endpoint", cosmosdb_account.document_endpoint)