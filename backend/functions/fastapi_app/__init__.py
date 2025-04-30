import azure.functions as func
import sys
from pathlib import Path

try:
    from app import app as fastapi_app
except ImportError:
    backend_root = Path(__file__).resolve().parent.parent.parent
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from app import app as fastapi_app

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)

async def main(req: func.HttpRequest, context: func.Context):
    return await app.handle_async(req, context) 