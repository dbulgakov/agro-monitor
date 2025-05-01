import logging
from .main import main as _main

async def main(msg):
    logging.info("process_analysis_job triggered")
    return _main(msg)
