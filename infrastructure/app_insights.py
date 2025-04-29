import pulumi_azure_native as azure_native

def create_app_insights(rg):
    return azure_native.insights.Component(
        "appInsights",
        resource_group_name=rg.name,
        kind="web",
        application_type="web"
    )
