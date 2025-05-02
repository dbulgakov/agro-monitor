import os
import logging
import numpy as np
import openai

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

openai_client = None

if OPENAI_API_KEY:
    try:
        openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        logging.info("Initialized OpenAI client.")
    except Exception as e:
        logging.error(f"OpenAI init failed: {e}", exc_info=True)
else:
    logging.warning("OPENAI_API_KEY not set.")

def generate_openai_recommendations(job_id: str, ndvi_data: np.ndarray, stress_mask: np.ndarray, crop_type: str) -> str:
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    if not openai_client:
        return "AI recommendations disabled (OpenAI client not initialized)."

    valid = ndvi_data[~np.isnan(ndvi_data)]
    if valid.size == 0:
        return "No valid NDVI data available to generate recommendations."

    avg_ndvi = float(np.mean(valid))
    stress_pct = float((np.sum(stress_mask) / valid.size) * 100)

    prompt = (
        f"Analysis of a {crop_type} field shows an average NDVI of {avg_ndvi:.3f} "
        f"with {stress_pct:.1f}% of the area under potential stress (NDVI below threshold). "
        "Provide concise (max 3 sentences) agronomic recommendations based on these values. "
        "Focus on potential causes and actions. Language: Ukrainian."
    )

    try:
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=150,
        )
        recommendation = resp.choices[0].message.content.strip()
        if not recommendation:
            return "AI generated an empty recommendation."
        return recommendation
    except openai.APIConnectionError as e:
        log_adapter.error(f"OpenAI API request failed to connect: {e}", exc_info=True)
        return "Failed to connect to OpenAI API."
    except openai.RateLimitError as e:
        log_adapter.error(f"OpenAI API request exceeded rate limit: {e}", exc_info=True)
        return "Rate limit exceeded for OpenAI API."
    except openai.APIStatusError as e:
        log_adapter.error(f"OpenAI API returned an error status: {e.status_code} - {e.response}", exc_info=True)
        return f"OpenAI API error (status {e.status_code})."
    except Exception as e:
        log_adapter.error(f"An unexpected error occurred during OpenAI call: {e}", exc_info=True)
        return "Failed to generate recommendations due to an unexpected error."