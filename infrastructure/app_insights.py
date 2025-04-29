import pulumi
import pulumi_azure_native as azure_native

def create_app_insights(rg):
    stack = pulumi.get_stack()
    app_insights_name = f"appi-agromonitor-{stack}"
    return azure_native.insights.Component(
        f"appInsights-{stack}",
        resource_name_=app_insights_name,
        resource_group_name=rg.name,
        kind="web",
        application_type="web"
    )
