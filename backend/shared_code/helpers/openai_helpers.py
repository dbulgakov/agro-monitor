import os
import logging
import numpy as np
import openai
from shared_code.schemas import StartAnalysisPayload

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

openai_client = None

if OPENAI_API_KEY:
    try:
        openai_client = openai.OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=60.0, # Total request timeout
        )
        logging.info("Initialized OpenAI client with timeouts.")
    except Exception as e:
        logging.error(f"OpenAI init failed: {e}", exc_info=True)
else:
    logging.warning("OPENAI_API_KEY not set.")

def generate_openai_recommendations(metadata: dict, payload: StartAnalysisPayload, ndvi_data: np.ndarray, stress_mask: np.ndarray) -> str:
    job_id = metadata.get('jobId', 'unknown_job')
    log_adapter = logging.getLogger(__name__).getChild(job_id)
    if not openai_client:
        return "AI recommendations disabled (OpenAI client not initialized)."

    valid = ndvi_data[~np.isnan(ndvi_data)]
    if valid.size == 0:
        return "No valid NDVI data available to generate recommendations."

    avg_ndvi = metadata.get('ndviStats', {}).get('mean', 'N/A')
    min_ndvi = metadata.get('ndviStats', {}).get('min', 'N/A')
    max_ndvi = metadata.get('ndviStats', {}).get('max', 'N/A')
    std_dev_ndvi = metadata.get('ndviStats', {}).get('std_dev', 'N/A')
    stress_pct = metadata.get('ndviStats', {}).get('stress_percentage', 'N/A')
    area_km2 = metadata.get('areaSqKm', 'N/A')
    crop_type = payload.crop_type
    date_range = payload.date_range
    cloud_cover = metadata.get('sceneCloudCover', 'N/A')
    scene_id = metadata.get('sceneId', 'N/A')
    ndvi_threshold = metadata.get('ndviThreshold', 'N/A')

    # Format numerical values, handling 'N/A'
    avg_ndvi_str = f"{avg_ndvi:.3f}" if isinstance(avg_ndvi, (int, float)) else 'N/A'
    min_ndvi_str = f"{min_ndvi:.3f}" if isinstance(min_ndvi, (int, float)) else 'N/A'
    max_ndvi_str = f"{max_ndvi:.3f}" if isinstance(max_ndvi, (int, float)) else 'N/A'
    std_dev_ndvi_str = f"{std_dev_ndvi:.3f}" if isinstance(std_dev_ndvi, (int, float)) else 'N/A'
    stress_pct_str = f"{stress_pct:.1f}" if isinstance(stress_pct, (int, float)) else 'N/A'
    area_km2_str = f"{area_km2:.2f}" if isinstance(area_km2, (int, float)) else 'N/A'

    prompt = (
        f"Надайте оцінку стану поля та агрономічні рекомендації українською мовою для ділянки площею {area_km2_str} кв. км. "
        f"Аналіз проведено за період {date_range} для культури '{crop_type}'. "
        f"Використано сцену {scene_id} з хмарністю {cloud_cover}%. "
        f"Середній NDVI становить {avg_ndvi_str} (мін: {min_ndvi_str}, макс: {max_ndvi_str}, ст. відх: {std_dev_ndvi_str}). "
        f"{stress_pct_str}% площі має потенційний стрес (NDVI нижче порогу {ndvi_threshold}). "
        f"Дайте стислу оцінку (2-4 речення) стану поля, можливі причини проблемних зон (якщо {stress_pct_str}% > 5%) та конкретні дії. "
        f"Враховуйте тип культури при наданні рекомендацій."
    )

    log_adapter.info(f"Generated OpenAI prompt: {prompt}") # Log the generated prompt

    try:
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6, # Slightly increased temperature for more varied descriptions
            max_tokens=250, # Increased tokens for potentially longer assessment
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