import pulumi_azure_native as azure_native

def create_consumption_plan(rg):
    return azure_native.web.AppServicePlan(
        "functionPlan",
        resource_group_name=rg.name,
        kind="FunctionApp",
        sku=azure_native.web.SkuDescriptionArgs(
            name="Y1",
            tier="Dynamic"
        )
    )
