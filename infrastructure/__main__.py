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
rg = resources.ResourceGroup(f"{base_name}-rg", resource_group_name=f"{base_name}-rg", location=location)

sa = storage.StorageAccount(f"{base_name}sa{pulumi.get_stack()}",
    resource_group_name=rg.name,
    account_name=f"{base_name}sa{pulumi.get_stack()}",
    sku=storage.SkuArgs(name=storage.SkuName.STANDARD_LRS),
    kind=storage.Kind.STORAGE_V2,
    location=location,
)

primary_key = pulumi.Output.all(rg.name, sa.name).apply(
    lambda args: storage.list_storage_account_keys(resource_group_name=args[0], account_name=args[1]).keys[0].value
)

storage_conn = pulumi.Output.all(sa.name, primary_key).apply(
    lambda args: f"DefaultEndpointsProtocol=https;AccountName={args[0]};AccountKey={args[1]};EndpointSuffix=core.windows.net"
)

ai = insights.Component(f"{base_name}-ai",
    resource_group_name=rg.name,
    resource_name=f"{base_name}-ai",
    kind="web",
    application_type=insights.ApplicationType.WEB,
    location=location,
)

cosmos_account = documentdb.DatabaseAccount(f"{base_name}-cosmos-{pulumi.get_stack()}",
    resource_group_name=rg.name,
    account_name=f"{base_name}-cosmos-{pulumi.get_stack()}",
    location=location,
    database_account_offer_type=documentdb.DatabaseAccountOfferType.STANDARD,
    capabilities=[documentdb.CapabilityArgs(name="EnableServerless")],
    locations=[documentdb.LocationArgs(location_name=location, failover_priority=0)],
)

cosmos_db = documentdb.SqlDatabase(f"{base_name}-db",
    resource_group_name=rg.name,
    account_name=cosmos_account.name,
    database_name=f"{base_name}-db-{pulumi.get_stack()}",
)

cosmos_container = documentdb.SqlContainer(f"{base_name}-container",
    resource_group_name=rg.name,
    account_name=cosmos_account.name,
    database_name=cosmos_db.name,
    container_name=f"{base_name}-cnt-{pulumi.get_stack()}",
    resource=documentdb.ContainerResourceArgs(
        id=f"{base_name}-cnt-{pulumi.get_stack()}",
        partition_key=documentdb.ContainerPartitionKeyArgs(
            paths=["/id"],
            kind=documentdb.PartitionKind.HASH
        ),
    ),
)

cosmos_conn = pulumi.Output.all(rg.name, cosmos_account.name).apply(
    lambda args: documentdb.list_database_account_connection_strings(
        resource_group_name=args[0],
        account_name=args[1]
    ).connection_strings[0].connection_string
)

func_plan = web.AppServicePlan(f"{base_name}-func-plan",
    resource_group_name=rg.name,
    name=f"{base_name}-func-plan",
    location=location,
    kind="Linux",
    reserved=True,
    sku=web.SkuDescriptionArgs(name="Y1", tier="Dynamic"),
)

func_app = web.WebApp(f"{base_name}-func-app-{pulumi.get_stack()}",
    resource_group_name=rg.name,
    name=f"{base_name}-func-app-{pulumi.get_stack()}",
    location=location,
    server_farm_id=func_plan.id,
    kind="functionapp,linux",
    reserved=True,
    site_config=web.SiteConfigArgs(
        linux_fx_version="Python|3.11",
        app_settings=[
            web.NameValuePairArgs(name="AzureWebJobsStorage", value=storage_conn),
            web.NameValuePairArgs(name="FUNCTIONS_WORKER_RUNTIME", value="python"),
            web.NameValuePairArgs(name="FUNCTIONS_EXTENSION_VERSION", value="~4"),
            web.NameValuePairArgs(name="APPINSIGHTS_INSTRUMENTATIONKEY", value=ai.instrumentation_key),
            web.NameValuePairArgs(name="COSMOSDB_CONNECTION_STRING", value=cosmos_conn),
            web.NameValuePairArgs(name="AZURE_STORAGE_CONNECTION_STRING", value=storage_conn),
            web.NameValuePairArgs(name="OPENAI_API_KEY", value=openai_api_key),
        ],
    ),
    https_only=True,
)

web.WebAppSourceControl("backend-sc",
    name=func_app.name,
    resource_group_name=rg.name,
    repo_url=git_repo_url,
    branch=git_branch,
    is_manual_integration=True,
    is_git_hub_action=False,
    opts=pulumi.ResourceOptions(depends_on=[func_app]),
)

web_plan = web.AppServicePlan(f"{base_name}-web-plan",
    resource_group_name=rg.name,
    name=f"{base_name}-web-plan",
    location=location,
    kind="Linux",
    reserved=True,
    sku=web.SkuDescriptionArgs(name="F1", tier="Free"),
)

web_app = web.WebApp(f"{base_name}-web-app-{pulumi.get_stack()}",
    resource_group_name=rg.name,
    name=f"{base_name}-web-app-{pulumi.get_stack()}",
    location=location,
    server_farm_id=web_plan.id,
    kind="app,linux",
    reserved=True,
    site_config=web.SiteConfigArgs(
        linux_fx_version="NODE|18-lts",
        app_settings=[
            web.NameValuePairArgs(name="SCM_DO_BUILD_DURING_DEPLOYMENT", value="true"),
            web.NameValuePairArgs(name="WEBSITE_NODE_DEFAULT_VERSION", value="~18"),
            web.NameValuePairArgs(name="PROJECT", value="frontend"),
            web.NameValuePairArgs(
                name="NEXT_PUBLIC_API_URL",
                value=func_app.default_host_name.apply(lambda host: f"https://{host}/api")
            ),
        ],
        startup_command="npm run start",
        ftps_state="FtpsOnly",
    ),
    https_only=True,
)

web.WebAppSourceControl("frontend-sc",
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
pulumi.export("resource_group", rg.name)
pulumi.export("storage_account", sa.name)
pulumi.export("cosmos_account", cosmos_account.name)
pulumi.export("cosmos_database", cosmos_db.name)
pulumi.export("func_app_name", func_app.name)
pulumi.export("web_app_name", web_app.name)
