# app.py
from fastapi import FastAPI

# TODO: Import and include your actual routers here
# from your_router import router1, router2

app = FastAPI(
    title="AgroMonitor API",
    description="API for AgroMonitor services.",
    version="0.1.0"
)

# Example root endpoint (optional, remove if not needed)
@app.get("/", tags=["Default"])
async def read_root():
    return {"message": "Welcome to AgroMonitor API"}

# TODO: Include your routers
# app.include_router(router1)
# app.include_router(router2) 