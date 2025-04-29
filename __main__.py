import pulumi
from infrastructure.config import location, resource_group_name
from infrastructure.resource_group import create_resource_group
from infrastructure.storage import create_storage_account
from infrastructure.app_insights import create_app_insights
from infrastructure.plan import create_consumption_plan
from infrastructure.function_app import create_function_app
from infrastructure.frontend import export_frontend_url

# Create resources
rg = create_resource_group()
storage_account, static_website = create_storage_account(rg)
app_insights = create_app_insights(rg)
plan = create_consumption_plan(rg)
func_app = create_function_app(rg, plan, storage_account, app_insights)
export_frontend_url(storage_account)
