import azure.functions as func
# Use AsgiProxy to integrate FastAPI with Azure Functions V2+ model
from azure_functions_fastapi import AsgiProxy
from app import app

# No need to instantiate the middleware directly with AsgiProxy

def main(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    """Azure Functions entry point that proxies requests to the FastAPI app."""
    # The AsgiProxy handles the request and response cycle implicitly
    # by working with the $return binding in function.json.
    return AsgiProxy(app).handle(req, context)
