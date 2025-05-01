import pulumi
import pulumi_azure_native.resources as resources
import pulumi_azure_native.storage as storage
import pulumi_azure_native.web as web
import os
import hashlib

config = pulumi.Config()
location = config.require("location")
git_repo_url = config.require("gitRepoUrl")
git_branch = config.require("gitBranch")
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

app_settings = [
    web.NameValuePairArgs(name="FUNCTIONS_WORKER_RUNTIME", value="python"),
    web.NameValuePairArgs(name="FUNCTIONS_EXTENSION_VERSION", value="~4"),
    web.NameValuePairArgs(name="WEBSITE_RUN_FROM_PACKAGE", value="1"),
    web.NameValuePairArgs(name="AZURE_STORAGE_CONNECTION_STRING", value=connection_string),
    web.NameValuePairArgs(name="ANALYSIS_QUEUE_NAME", value=queue.name),
    web.NameValuePairArgs(name="IMAGES_CONTAINER_NAME", value="images"),
    web.NameValuePairArgs(name="REPORTS_CONTAINER_NAME", value="reports"),
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
    ),
    identity=web.ManagedServiceIdentityArgs(type="SystemAssigned"),
)

web.WebAppSourceControl(
    "func-sc",
    name=func_app.name,
    resource_group_name=rg.name,
    repo_url=git_repo_url,
    branch=git_branch,
    is_manual_integration=False,
    is_git_hub_action=True,
    opts=pulumi.ResourceOptions(depends_on=[func_app]),
)

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
    ),
    identity=web.ManagedServiceIdentityArgs(type="SystemAssigned"),
)

web.WebAppSourceControl(
    "front-sc",
    name=front_app.name,
    resource_group_name=rg.name,
    repo_url=git_repo_url,
    branch=git_branch,
    is_manual_integration=False,
    is_git_hub_action=True,
    opts=pulumi.ResourceOptions(depends_on=[front_app]),
)

pulumi.export("function_app_endpoint", func_app.default_host_name.apply(lambda h: f"https://{h}"))
pulumi.export("frontend_endpoint", front_app.default_host_name.apply(lambda h: f"https://{h}"))
pulumi.export("storage_account_name", sa.name)
pulumi.export("analysis_queue_name", queue.name)
pulumi.export("images_container_name", images_container.name)
pulumi.export("reports_container_name", reports_container.name)

pulumi.export("resource_group_name", rg.name)
pulumi.export("function_app_name", func_app.name)
pulumi.export("frontend_app_name", front_app.name)
