import os
import logging
from fastapi import HTTPException, status


def get_required_env_vars(context: str) -> list[str]:
    mapping = {
        "start_analysis": ["AZURE_STORAGE_CONNECTION_STRING", "ANALYSIS_QUEUE_NAME"],
        "progress": ["AZURE_STORAGE_CONNECTION_STRING", "REPORTS_CONTAINER_NAME"],
        "report": ["AZURE_STORAGE_CONNECTION_STRING", "REPORTS_CONTAINER_NAME"],
    }
    return mapping.get(context, [])


def check_environment_variables(required_vars: list[str]):
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        msg = f"Missing required environment variables: {', '.join(missing)}"
        logging.critical(msg)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=msg)
    return True