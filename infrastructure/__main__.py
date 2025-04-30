import pulumi
import pulumi_azure_native.storage as storage
import pulumi_azure_native.resources as resources
import pulumi_azure_native.insights as insights
import pulumi_azure_native.web as web
import pulumi_azure_native.documentdb as documentdb


config = pulumi.Config()
location       = config.require("location")
git_repo_url   = config.require("gitRepoUrl")
git_branch     = config.require("gitBranch")
openai_api_key = config.require("openaiApiKey")         

base_name = "fastapidemo"


resource_group = resources.ResourceGroup(f"{base_name}-rg",
    resource_group_name=f"{base_name}-rg",
    location=location,
)


storage_account = storage.StorageAccount(f"{base_name}sa{pulumi.get_stack()}",
    resource_group_name=resource_group.name,
    account_name=f"{base_name}sa{pulumi.get_stack()}",
    sku=storage.SkuArgs(name=storage.SkuName.STANDARD_LRS),
    kind=storage.Kind.STORAGE_V2,
    location=location,
)

primary_storage_key = pulumi.Output.all(resource_group.name, storage_account.name).apply(
    lambda args: storage.list_storage_account_keys(
        resource_group_name=args[0],
        account_name=args[1]
    ).keys[0].value
)

storage_connection_string = pulumi.Output.all(storage_account.name, primary_storage_key).apply(
    lambda args: f"DefaultEndpointsProtocol=https;AccountName={args[0]};AccountKey={args[1]};EndpointSuffix=core.windows.net"
)


app_insights = insights.Component(f"{base_name}-ai",
    resource_group_name=resource_group.name,
    resource_name=f"{base_name}-ai",
    kind="web",
    application_type=insights.ApplicationType.WEB,
    location=location,
)


cosmosdb_account = documentdb.DatabaseAccount(f"{base_name}-cosmos-{pulumi.get_stack()}",
    resource_group_name=resource_group.name,
    account_name=f"{base_name}-cosmos-{pulumi.get_stack()}",
    location=location,
    database_account_offer_type=documentdb.DatabaseAccountOfferType.STANDARD,
    capabilities=[documentdb.CapabilityArgs(name="EnableServerless")],
    locations=[documentdb.LocationArgs(
        location_name=location,
        failover_priority=0,
    )]
)

cosmosdb_database = documentdb.SqlDatabase(f"{base_name}-db",
    resource_group_name=resource_group.name,
    account_name=cosmosdb_account.name,
    database_name=f"{base_name}-db-{pulumi.get_stack()}",
)

cosmosdb_container = documentdb.SqlContainer(f"{base_name}-container",
    resource_group_name=resource_group.name,
    account_name=cosmosdb_account.name,
    database_name=cosmosdb_database.name,
    container_name=f"{base_name}-cnt-{pulumi.get_stack()}",
    resource=documentdb.ContainerResourceArgs(
        id=f"{base_name}-cnt-{pulumi.get_stack()}",
        partition_key=documentdb.ContainerPartitionKeyArgs(
            paths=["/id"],
            kind=documentdb.PartitionKind.HASH
        ),
    )
)

cosmosdb_connection_string = pulumi.Output.all(resource_group.name, cosmosdb_account.name).apply(
    lambda args: documentdb.list_database_account_connection_strings(
        resource_group_name=args[0],
        account_name=args[1]
    ).connection_strings[0].connection_string
)


backend_app_service_plan = web.AppServicePlan(f"{base_name}-func-plan",
    resource_group_name=resource_group.name,
    name=f"{base_name}-func-plan",
    location=location,
    kind="Linux",
    reserved=True,
    sku=web.SkuDescriptionArgs(name="Y1", tier="Dynamic"),
)

backend_function_app = web.WebApp(f"{base_name}-func-app",
    resource_group_name=resource_group.name,
    name=f"{base_name}-func-app-{pulumi.get_stack()}",
    location=location,
    server_farm_id=backend_app_service_plan.id,
    kind="functionapp,linux",
    reserved=True,
    site_config=web.SiteConfigArgs(
        linux_fx_version="Python|3.11",
        app_settings=[
            web.NameValuePairArgs(name="AzureWebJobsStorage",             value=storage_connection_string),
            web.NameValuePairArgs(name="FUNCTIONS_WORKER_RUNTIME",        value="python"),
            web.NameValuePairArgs(name="FUNCTIONS_EXTENSION_VERSION",     value="~4"),
            web.NameValuePairArgs(name="APPINSIGHTS_INSTRUMENTATIONKEY",  value=app_insights.instrumentation_key),
            web.NameValuePairArgs(name="COSMOSDB_CONNECTION_STRING",      value=cosmosdb_connection_string),
            web.NameValuePairArgs(name="ENVIRONMENT",                     value="production"),
            web.NameValuePairArgs(name="AZURE_STORAGE_CONNECTION_STRING", value=storage_connection_string),
            web.NameValuePairArgs(name="OPENAI_API_KEY",                  value=openai_api_key),
        ],
    ),
    https_only=True,
)

backend_source_control = web.WebAppSourceControl("backend-sourcecontrol",
    name=backend_function_app.name,
    resource_group_name=resource_group.name,
    repo_url=git_repo_url,
    branch=git_branch,
    is_manual_integration=True,
    is_git_hub_action=False,
    opts=pulumi.ResourceOptions(depends_on=[backend_function_app]),
)


frontend_app_service_plan = web.AppServicePlan(f"{base_name}-web-plan",
    resource_group_name=resource_group.name,
    name=f"{base_name}-web-plan",
    location=location,
    kind="Linux",
    reserved=True,
    sku=web.SkuDescriptionArgs(name="F1", tier="Free"),
)

frontend_web_app = web.WebApp(f"{base_name}-web-app",
    resource_group_name=resource_group.name,
    name=f"{base_name}-web-app-{pulumi.get_stack()}",
    location=location,
    server_farm_id=frontend_app_service_plan.id,
    kind="app,linux",
    reserved=True,
    site_config=web.SiteConfigArgs(
        linux_fx_version="NODE|18-lts",
        app_settings=[
            web.NameValuePairArgs(name="SCM_DO_BUILD_DURING_DEPLOYMENT", value="true"),
            web.NameValuePairArgs(name="WEBSITE_NODE_DEFAULT_VERSION",   value="~18"),
            web.NameValuePairArgs(name="PROJECT",                        value="frontend"),
            # добавили:
            web.NameValuePairArgs(
                name="NEXT_PUBLIC_API_URL",
                value=backend_function_app.default_host_name.apply(
                    lambda host: f"https://{host}/api"
                )
            ),
        ],
        startup_command="npm run start",
        ftps_state="FtpsOnly",
    ),
    https_only=True,
)

frontend_source_control = web.WebAppSourceControl("frontend-sourcecontrol",
    name=frontend_web_app.name,
    resource_group_name=resource_group.name,
    repo_url=git_repo_url,
    branch=git_branch,
    is_manual_integration=True,
    is_git_hub_action=False,
    opts=pulumi.ResourceOptions(depends_on=[frontend_web_app]),
)


pulumi.export("backend_endpoint", frontend_web_app.default_host_name.apply(
    lambda h: f"https://{h}/api"
))
pulumi.export("frontend_endpoint", frontend_web_app.default_host_name.apply(
    lambda h: f"https://{h}"
))
pulumi.export("resource_group_name",         resource_group.name)
pulumi.export("storage_account_name",        storage_account.name)
pulumi.export("cosmosdb_account_name",       cosmosdb_account.name)
pulumi.export("cosmosdb_database_name",      cosmosdb_database.name)
pulumi.export("backend_function_app_name",   backend_function_app.name)
pulumi.export("frontend_web_app_name",       frontend_web_app.name)
