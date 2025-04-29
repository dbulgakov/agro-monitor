import logging
from fastapi import FastAPI

from routers.analysis import router as analysis_router
from routers.report import router as report_router
from routers.progress import router as progress_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Agro Monitor Backend API",
    description="API для получения и анализа данных сельскохозяйственных полей. Включает SSE для отслеживания прогресса задач.",
    version="1.0.0",
    openapi_prefix="/api",
    servers=[{"url": "/api", "description": "Локальный или прокси-сервер"}],
)

app.include_router(analysis_router, prefix="/analyze")
app.include_router(report_router, prefix="/report")
app.include_router(progress_router, prefix="/progress")

@app.get("/", include_in_schema=False)
async def read_root():
    return {"message": "Welcome to AgroMonitor API v2 (Docker)"}