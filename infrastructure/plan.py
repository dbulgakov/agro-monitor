import pulumi
import pulumi_azure_native as azure_native

def create_consumption_plan(rg):
    stack = pulumi.get_stack()
    plan_name = f"plan-agromonitor-{stack}"
    return azure_native.web.AppServicePlan(
        f"functionPlan-{stack}",
        resource_group_name=rg.name,
        name=plan_name,
        kind="FunctionApp",
        sku=azure_native.web.SkuDescriptionArgs(
            name="Y1",
            tier="Dynamic"
        )
    )
