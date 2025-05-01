import os
import logging
import numpy as np
import asyncio
import openai

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
USE_ASYNC_OPENAI_CLIENT = os.getenv("USE_ASYNC_OPENAI_CLIENT", "false").lower() == "true"

openai_client = None
openai_async_client = None

if OPENAI_API_KEY:
    try:
        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        logging.info("Initialized OpenAI client.")
        if USE_ASYNC_OPENAI_CLIENT:
            from openai import AsyncOpenAI
            openai_async_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            logging.info("Initialized async OpenAI client.")
    except Exception as e:
        logging.error(f"OpenAI init failed: {e}", exc_info=True)
else:
    logging.warning("OPENAI_API_KEY not set.")

async def generate_openai_recommendations(job_id: str, ndvi_data: np.ndarray, stress_mask: np.ndarray, crop_type: str) -> str:
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    if not openai_client:
        return "AI recommendations disabled."
    valid = ndvi_data[~np.isnan(ndvi_data)]
    if valid.size == 0:
        return "No valid NDVI data."
    avg_ndvi = float(np.mean(valid))
    stress_pct = float((np.sum(stress_mask) / valid.size) * 100)
    prompt = (
        f"Field {crop_type}: avg NDVI {avg_ndvi:.3f}, stress {stress_pct:.1f}%. "
        "Provide 3-sentence advice."
    )
    try:
        if USE_ASYNC_OPENAI_CLIENT and openai_async_client:
            resp = await openai_async_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=150,
            )
            return resp.choices[0].message.content.strip()
        else:
            resp = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=150,
            )
            return resp.choices[0].message.content.strip()
    except Exception as e:
        log_adapter.error(f"OpenAI call failed: {e}", exc_info=True)
        return "Failed to generate recommendations."