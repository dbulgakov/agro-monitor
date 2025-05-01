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
    const response = await fetch(`${API_BASE_URL}/api/analyze`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw await handleApiError(response);
    }

    return await response.json();

  } catch (error) {
    console.error('Error starting analysis:', error);
    throw error;
  }
}

export async function fetchReport(jobId: string): Promise<ReportData> {
  console.log(`Завантаження звіту для ID завдання: ${jobId}`);

  try {
    const response = await fetch(`${API_BASE_URL}/api/report/${jobId}`);

    if (!response.ok) {
      if (response.status === 404) {
        console.warn(`Report for job ${jobId} not found (404).`);
        throw new Error("Звіт не знайдено або ще не готовий.");
      }
      throw await handleApiError(response);
    }

    const report: ReportData = await response.json();
    console.log('Завантажені дані звіту:', report);
    return report;

  } catch (error) {
    console.error('Error fetching report:', error);
    throw error;
  }
} 