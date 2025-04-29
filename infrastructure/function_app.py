import pulumi
import pulumi_azure_native as azure_native
import pulumi.asset as asset
import pulumi_docker as docker # Import Docker provider
import os

from .registry import get_registry_credentials # Import ACR credentials function

def create_function_app(rg, plan, storage_account, app_insights, app_config_store, registry):
    # --- Docker Image Build & Push --- 
    backend_dockerfile_path = os.path.join(os.path.dirname(__file__), '../../backend')
    image_name = registry.login_server.apply(lambda server: f"{server}/agromonitor-backend:latest")
    
    # Get ACR credentials for Docker push
    registry_creds = get_registry_credentials(rg, registry)
    registry_info = docker.RegistryArgs(
        server=registry.login_server,
        username=registry_creds.username,
        password=registry_creds.passwords[0].apply(lambda p: p.value)
    )

    # Define and build the Docker image using the Dockerfile
    backend_image = docker.Image("agromonitor-backend-image",
        build=docker.DockerBuildArgs(
            context=backend_dockerfile_path,
            dockerfile=os.path.join(backend_dockerfile_path, "Dockerfile"),
            platform="linux/amd64", # Specify platform for consistency
        ),
        image_name=image_name,
        registry=registry_info,
    )
    # --- End Docker --- 
    
    # Get App Config connection string 
    app_config_connection_string = azure_native.appconfiguration.list_configuration_store_keys_output(
        resource_group_name=rg.name,
        config_store_name=app_config_store.name
    ).apply(lambda keys: next((kv.connection_string for kv in keys.value if kv.read_only == False), None))

    app = azure_native.web.WebApp(
        "functionApp",
        resource_group_name=rg.name,
        server_farm_id=plan.id,
        site_config=azure_native.web.SiteConfigArgs(
            linux_fx_version=backend_image.image_name.apply(lambda name: f"DOCKER|{name}"),
            always_on=False, # Consumption plan doesn't support Always On
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
                    name="APPINSIGHTS_INSTRUMENTATIONKEY",
                    value=app_insights.instrumentation_key
                ),
                azure_native.web.NameValuePairArgs(
                    name="AzureAppConfigurationConnectionString",
                    value=app_config_connection_string
                ),
                azure_native.web.NameValuePairArgs(
                    name="DOCKER_REGISTRY_SERVER_URL", 
                    value=registry.login_server
                ),
                azure_native.web.NameValuePairArgs(
                    name="DOCKER_REGISTRY_SERVER_USERNAME", 
                    value=registry_creds.username
                ),
                azure_native.web.NameValuePairArgs(
                    name="DOCKER_REGISTRY_SERVER_PASSWORD", 
                    value=registry_creds.passwords[0].apply(lambda p: p.value)
                )
            ],
            cors=azure_native.web.CorsSettingsArgs(
                allowed_origins=storage_account.primary_endpoints.web.apply(
                    lambda url: [url.rstrip('/')] 
                ),
                support_credentials=False 
            )
        ),
        https_only=True
    )
    pulumi.export(
        "functionEndpoint",
        app.default_host_name.apply(lambda host: f"https://{host}/api")
    )
    pulumi.export("backendDockerImage", backend_image.image_name) # Export image name
    return app
