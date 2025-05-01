import { Feature, Polygon, Point, FeatureCollection } from 'geojson';

export const API_BASE_URL: string = (() => { 
  if (process.env.NODE_ENV === 'development') {
    console.log('[API Lib] Using relative base URL for development:', '');
    return ''; 
  } else {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!apiUrl) {
      console.error("NEXT_PUBLIC_API_URL is not set for production build!");
      return ""; 
    }
    console.log('[API Lib] Using configured API base URL:', apiUrl);
    return apiUrl;
  }
})();

export type CropType = "wheat" | "corn" | "sunflower" | "soy";
export type AnalysisFrequency = "single" | "weekly";

export interface AnalysisParameters {
  ndvi_threshold: number;
  date_range: string;
  max_cloud_cover: number;
  crop_type: CropType;
  snapshotDate?: string;
}

export interface StartAnalysisPayload extends AnalysisParameters {
  area: Feature<Polygon | Point>;
  frequency: AnalysisFrequency;
}

export interface StartAnalysisResponse {
  jobId: string;
}

export interface ReportData {
  jobId: string;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  parameters: AnalysisParameters;
  selectedArea: Feature<Polygon | Point>;
  mapCenter?: [number, number];
  mapZoom?: number;
  resultGeoJson?: FeatureCollection<Polygon | Point, { health: string }>;
  satelliteImages?: { url: string; date: string; cloudCover: number }[];
  summary: string;
  error?: string;
  areaSqKm?: number;
  snapshotImageUrl?: string;
  ndviImageUrl?: string;
  stressZoneImageUrl?: string;
  stressPercentage?: number;
}

export interface JobProgress {
  progress: number;
  statusMessage: string;
  isComplete: boolean;
  error?: string;
}

// Helper function for exponential backoff delay
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// Wrapper for fetch with retry logic for 500 errors
async function fetchWithRetry(url: string, options: RequestInit = {}, retries = 3, initialDelay = 500): Promise<Response> {
  let attempt = 0;
  while (attempt < retries) {
    try {
      const response = await fetch(url, options);
      if (response.ok) {
        return response; // Success
      }
      if (response.status === 500 && attempt < retries - 1) {
        const waitTime = initialDelay * Math.pow(2, attempt);
        console.warn(`API call to ${url} failed with status 500. Retrying in ${waitTime}ms... (Attempt ${attempt + 1}/${retries})`);
        await delay(waitTime);
        attempt++;
      } else {
        // Non-500 error, or last retry failed
        throw await handleApiError(response);
      }
    } catch (error) {
      // Handle network errors or errors thrown by handleApiError
      if (attempt < retries - 1) {
          const waitTime = initialDelay * Math.pow(2, attempt);
          console.warn(`API call to ${url} failed with network error or non-500 status. Retrying in ${waitTime}ms... (Attempt ${attempt + 1}/${retries})`, error);
          await delay(waitTime);
          attempt++;
      } else {
           console.error(`API call to ${url} failed after ${retries} attempts.`, error);
           // Re-throw the last error encountered
           throw error instanceof Error ? error : new Error('Unknown fetch error after retries');
      }
    }
  }
  // Should not be reached, but satisfies TypeScript
  throw new Error(`API call failed after ${retries} attempts.`);
}

async function handleApiError(response: Response): Promise<Error> {
  let errorMsg = `API Error: ${response.status} ${response.statusText}`;
  let errorDetails: unknown = null; 
  try {
    const errorData = await response.json();
    if (typeof errorData === 'object' && errorData !== null) {
      errorMsg = (errorData as any).detail || (errorData as any).message || (errorData as any).error || errorMsg;
      errorDetails = (errorData as any).details || errorData;
    } else {
      errorDetails = errorData;
    }
  } catch (parseError) {
    console.warn("Could not parse error response body as JSON:", parseError);
  }
  console.error('API Call Failed:', errorMsg, 'Details:', errorDetails);
  const error = new Error(errorMsg);
  return error;
}

export async function startAnalysis(payload: StartAnalysisPayload): Promise<StartAnalysisResponse> {
  console.log('[startAnalysis] API Base URL:', API_BASE_URL);
  console.log('[startAnalysis] Payload:', payload);
  try {
    // Use the retry wrapper
    const response = await fetchWithRetry(`${API_BASE_URL}/api/analyze`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });

    // No need to check response.ok here, fetchWithRetry handles it or throws
    return await response.json();

  } catch (error) {
    // Error is already logged by fetchWithRetry or handleApiError
    // console.error('Error starting analysis:', error); // Redundant logging
    throw error; // Re-throw for the caller (UI) to handle
  }
}

export async function fetchReport(jobId: string): Promise<ReportData> {
  console.log(`Завантаження звіту для ID завдання: ${jobId}`);

  try {
     // Use the retry wrapper
    const response = await fetchWithRetry(`${API_BASE_URL}/api/report/${jobId}`);

    // No need to check response.ok here, fetchWithRetry handles it or throws
    // Specific 404 handling can still be done if needed, but requires checking status *before* throwing in handleApiError
    // For now, let handleApiError manage it. If 404 needs special UI handling, we might adjust handleApiError or add checks here.

    const report: ReportData = await response.json();
    console.log('Завантажені дані звіту:', report);
    return report;

  } catch (error) {
    // Error is already logged by fetchWithRetry or handleApiError
    // console.error('Error fetching report:', error); // Redundant logging
    // Specific handling for 404 error message could be done here if caught differently
     if (error instanceof Error && error.message.includes("404")) {
        console.warn(`Report for job ${jobId} not found (404 handled after retries).`);
        // Throw a more user-friendly error for the UI
        throw new Error("Звіт не знайдено або ще не готовий.");
    }
    throw error; // Re-throw for the caller (UI) to handle
  }
} 