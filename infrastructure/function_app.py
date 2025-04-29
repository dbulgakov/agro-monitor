import pulumi
import pulumi_azure_native as azure_native

from .config import function_package_url

def create_function_app(rg, plan, storage_account, app_insights):
    app = azure_native.web.WebApp(
        "functionApp",
        resource_group_name=rg.name,
        server_farm_id=plan.id,
        site_config=azure_native.web.SiteConfigArgs(
            app_settings=[
                azure_native.web.NameValuePairArgs(
                    name="AzureWebJobsStorage",
                    value=storage_account.primary_connection_string
                ),
                azure_native.web.NameValuePairArgs(
                    name="FUNCTIONS_EXTENSION_VERSION",
                    value="~4"
                ),
                azure_native.web.NameValuePairArgs(
                    name="WEBSITE_RUN_FROM_PACKAGE",
                    value=function_package_url
                ),
                azure_native.web.NameValuePairArgs(
                    name="APPINSIGHTS_INSTRUMENTATIONKEY",
                    value=app_insights.instrumentation_key
                ),
            ],
            linux_fx_version="Python|3.9",
        ),
        https_only=True
    )
    pulumi.export(
        "functionEndpoint",
        app.default_host_name.apply(lambda host: f"https://{host}/api")
    )
    return app
