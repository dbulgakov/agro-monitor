import pulumi

def export_frontend_url(storage_account):
    pulumi.export(
        "frontendUrl",
        storage_account.primary_endpoints.web
    )
