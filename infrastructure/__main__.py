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
primary_storage_key = storage_keys.keys[0].value
storage_conn = pulumi.Output.all(storage_account.name, primary_storage_key).apply(
    lambda args: f"DefaultEndpointsProtocol=https;AccountName={args[0]};AccountKey={args[1]};EndpointSuffix=core.windows.net"
)

analysis_queue = storage.Queue(
    "analysis-requests",
    resource_group_name=rg.name,
    account_name=storage_account.name,
    queue_name="analysis-requests"
)

reports_container = storage.BlobContainer(
    "reports-container",
    resource_group_name=rg.name,
    account_name=storage_account.name,
    container_name="reports"
)

images_container = storage.BlobContainer(
    "images-container",
    resource_group_name=rg.name,
    account_name=storage_account.name,
    container_name="images"
)

ai = insights.Component(
    f"{base_name}-ai",
    resource_group_name=rg.name,
    kind="web",
    application_type=insights.ApplicationType.WEB,
    location=location,
)

cosmosdb_account = documentdb.DatabaseAccount(
    f"{base_name}-cosmos",
    resource_group_name=rg.name,
    locations=[documentdb.LocationArgs(location_name=location, failover_priority=0)],
    kind=documentdb.DatabaseKind.GLOBAL_DOCUMENT_DB,
    database_account_offer_type=documentdb.DatabaseAccountOfferType.STANDARD,
    consistency_policy=documentdb.ConsistencyPolicyArgs(
        default_consistency_level=documentdb.DefaultConsistencyLevel.SESSION,
    ),
    capabilities=[documentdb.CapabilityArgs(name="EnableServerless")]
)

cosmos_keys = documentdb.list_database_account_keys_output(
    resource_group_name=rg.name,
    account_name=cosmosdb_account.name,
)
primary_cosmos_key = cosmos_keys.primary_master_key

cosmos_conn_string = documentdb.list_database_account_connection_strings_output(
    resource_group_name=rg.name,
    account_name=cosmosdb_account.name,
).connection_strings[0].connection_string

cosmos_db_name = f"{base_name}-db"
cosmos_container_name = "Items"

sql_database = documentdb.SqlResourceSqlDatabase(
    f"{base_name}-sqldb",
    resource_group_name=rg.name,
    account_name=cosmosdb_account.name,
    database_name=cosmos_db_name,
    resource=documentdb.SqlDatabaseResourceArgs(id=cosmos_db_name),
    options=documentdb.CreateUpdateOptionsArgs(),
    opts=pulumi.ResourceOptions(depends_on=[cosmosdb_account])
)

sql_container = documentdb.SqlResourceSqlContainer(
    f"{base_name}-sqlcontainer",
    resource_group_name=rg.name,
    account_name=cosmosdb_account.name,
    database_name=sql_database.name,
    container_name=cosmos_container_name,
    resource=documentdb.SqlContainerResourceArgs(
        id=cosmos_container_name,
        partition_key=documentdb.ContainerPartitionKeyArgs(
            paths=["/partitionKey"],
            kind=documentdb.PartitionKind.HASH
        )
    ),
    options=documentdb.CreateUpdateOptionsArgs(),
    opts=pulumi.ResourceOptions(depends_on=[sql_database])
)

func_plan = web.AppServicePlan(
    f"{base_name}-func-plan",
    resource_group_name=rg.name,
    kind="Linux",
    reserved=True,
    sku=web.SkuDescriptionArgs(
        tier="Dynamic",
        name="Y1",
    ),
    location=location,
)

func_app = web.WebApp(
    f"{base_name}-func-app-{pulumi.get_stack()}",
    resource_group_name=rg.name,
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
            web.NameValuePairArgs(name="APPINSIGHTS_INSTRUMENTATIONKEY", value=ai.instrumentation_key),
            web.NameValuePairArgs(name="COSMOSDB_CONNECTION_STRING", value=cosmos_conn_string),
            web.NameValuePairArgs(name="OPENAI_API_KEY", value=openai_api_key),
            web.NameValuePairArgs(name="ANALYSIS_QUEUE_NAME", value=analysis_queue.name),
            web.NameValuePairArgs(name="REPORTS_CONTAINER_NAME", value=reports_container.name),
            web.NameValuePairArgs(name="IMAGES_CONTAINER_NAME", value=images_container.name),
        ],
        always_on=False
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
    opts=pulumi.ResourceOptions(depends_on=[func_app]),
)

web_plan = web.AppServicePlan(
    f"{base_name}-web-plan",
    resource_group_name=rg.name,
    kind="Linux",
    reserved=True,
    sku=web.SkuDescriptionArgs(
        tier="Free",
        name="F1",
    ),
    location=location,
)

web_app = web.WebApp(
    f"{base_name}-web-app-{pulumi.get_stack()}",
    resource_group_name=rg.name,
    location=location,
    server_farm_id=web_plan.id,
    kind="app,linux",
    reserved=True,
    site_config=web.SiteConfigArgs(
        linux_fx_version="NODE|18-lts",
        app_settings=[
            web.NameValuePairArgs(name="SCM_DO_BUILD_DURING_DEPLOYMENT", value="true"),
            web.NameValuePairArgs(name="PROJECT", value="frontend"),
            web.NameValuePairArgs(
                name="NEXT_PUBLIC_API_URL",
                value=func_app.default_host_name.apply(lambda h: f"https://{h}/api")
            ),
        ],
        always_on=False
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
    opts=pulumi.ResourceOptions(depends_on=[web_app]),
)

pulumi.export("backend_endpoint", func_app.default_host_name.apply(lambda h: f"https://{h}/api"))
pulumi.export("frontend_endpoint", web_app.default_host_name.apply(lambda h: f"https://{h}"))
pulumi.export("storage_account_name", storage_account.name)
pulumi.export("analysis_queue_name", analysis_queue.name)
pulumi.export("reports_container_name", reports_container.name)
pulumi.export("images_container_name", images_container.name)
pulumi.export("cosmosdb_account_endpoint", cosmosdb_account.document_endpoint)
pulumi.export("cosmosdb_database_name", sql_database.name if sql_database else "Not Created")
