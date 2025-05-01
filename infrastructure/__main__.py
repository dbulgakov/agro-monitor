import pulumi
import pulumi_azure_native.resources as resources
import pulumi_azure_native.storage as storage
import pulumi_azure_native.documentdb as documentdb
import pulumi_azure_native.web as web
import pulumi.asset as asset
import os

config = pulumi.Config()
location = config.require("location")
git_repo_url = config.require("gitRepoUrl")
git_branch = config.require("gitBranch")

project, stack = pulumi.get_project(), pulumi.get_stack()
rg = resources.ResourceGroup(f"{project}-rg-{stack}", location=location)

sa = storage.StorageAccount(
    "sa",
    resource_group_name=rg.name,
    location=rg.location,
    sku=storage.SkuArgs(name=storage.SkuName.STANDARD_LRS),
    kind=storage.Kind.STORAGE_V2,
)

storage.BlobContainer("images", account_name=sa.name, resource_group_name=rg.name, container_name="images")
storage.BlobContainer("reports", account_name=sa.name, resource_group_name=rg.name, container_name="reports")
queue = storage.Queue("analysis-requests", account_name=sa.name, resource_group_name=rg.name, queue_name="analysis-requests")

cosmos = documentdb.DatabaseAccount(
    "cosmos",
    resource_group_name=rg.name,
    location=rg.location,
    kind=documentdb.DatabaseAccountKind.GLOBAL_DOCUMENT_DB,
    database_account_offer_type="Standard",
    locations=[documentdb.LocationArgs(location_name=rg.location, failover_priority=0)],
    consistency_policy=documentdb.ConsistencyPolicyArgs(default_consistency_level=documentdb.DefaultConsistencyLevel.SESSION),
)

func_plan = web.AppServicePlan(
    "func-plan",
    resource_group_name=rg.name,
    location=rg.location,
    kind="functionapp",
    sku=web.SkuDescriptionArgs(name="Y1", tier="Dynamic"),
)

web_plan = web.AppServicePlan(
    "web-plan",
    resource_group_name=rg.name,
    location=rg.location,
    kind="app",
    reserved=True,
    sku=web.SkuDescriptionArgs(name="B1", tier="Basic"),
)

def common_settings():
    return [
        web.NameValuePairArgs(name="WEBSITES_ENABLE_APP_SERVICE_STORAGE", value="true"),
        web.NameValuePairArgs(name="AZURE_STORAGE_ACCOUNT", value=sa.name),
        web.NameValuePairArgs(name="AZURE_COSMOS_ENDPOINT", value=cosmos.document_endpoint),
        web.NameValuePairArgs(name="SCM_DO_BUILD_DURING_DEPLOYMENT", value="true"),
    ]

func_app = web.WebApp(
    "func-api",
    resource_group_name=rg.name,
    location=rg.location,
    server_farm_id=func_plan.id,
    kind="functionapp",
    site_config=web.SiteConfigArgs(
        app_settings=[*common_settings(), web.NameValuePairArgs(name="FUNCTIONS_WORKER_RUNTIME", value="python")]
    ),
    identity=web.ManagedServiceIdentityArgs(type="SystemAssigned"),
)

backend_zip = asset.AssetArchive({
    ".": asset.FileArchive(os.path.join(os.getcwd(), "backend"))
})
web.WebAppDeployment(
    "func-deploy",
    name=func_app.name,
    resource_group_name=rg.name,
    package=backend_zip,
)

frontend_app = web.WebApp(
    "frontend",
    resource_group_name=rg.name,
    location=rg.location,
    server_farm_id=web_plan.id,
    site_config=web.SiteConfigArgs(
        app_settings=[web.NameValuePairArgs(name="SCM_DO_BUILD_DURING_DEPLOYMENT", value="true")]
    ),
    identity=web.ManagedServiceIdentityArgs(type="SystemAssigned"),
)

frontend_zip = asset.AssetArchive({
    ".": asset.FileArchive(os.path.join(os.getcwd(), "frontend"))
})
web.WebAppDeployment(
    "frontend-deploy",
    name=frontend_app.name,
    resource_group_name=rg.name,
    package=frontend_zip,
)

pulumi.export("backend_endpoint", func_app.default_host_name.apply(lambda h: f"https://{h}"))
pulumi.export("frontend_endpoint", frontend_app.default_host_name.apply(lambda h: f"https://{h}"))
pulumi.export("storage_account_name", sa.name)
pulumi.export("analysis_queue_name", queue.name)
pulumi.export("images_container_name", "images")
pulumi.export("reports_container_name", "reports")
pulumi.export("cosmosdb_account_endpoint", cosmos.document_endpoint)
