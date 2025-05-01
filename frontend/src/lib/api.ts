// Placeholder for API interaction functions
import { Feature, Polygon, Point, FeatureCollection } from 'geojson';

// Determine base URL based on environment and export it
export const API_BASE_URL: string = (() => { 
  if (process.env.NODE_ENV === 'development') {
    // Use relative path for local mock APIs served by Next.js dev server
    console.log('[API Lib] Using local mock API base URL:', '/api');
    return '/api';
  } else {
    // Use environment variable for production/deployed environments
    const apiUrl = process.env.NEXT_PUBLIC_API_URL;
    console.log('[API Lib] Using configured API base URL:', apiUrl || '/api (fallback)');
    // Fallback to /api is unlikely needed in production but kept as a safeguard
    return apiUrl || '/api'; 
  }
})(); // Immediately invoke the function to assign to the const

// --- Types --- //
export type CropType = "wheat" | "corn" | "sunflower" | "soy";
export type AnalysisFrequency = "single" | "weekly";

export interface AnalysisParameters {
  ndvi_threshold: number;
  date_range: string; // e.g., "2024-03-01/2024-04-30"
  max_cloud_cover: number;
  crop_type: CropType;
  snapshotDate?: string; // Добавлено поле даты снимка
}

export interface StartAnalysisPayload extends AnalysisParameters {
  area: Feature<Polygon | Point>; // GeoJSON Feature
  frequency: AnalysisFrequency;
}

export interface StartAnalysisResponse {
  jobId: string;
}

export interface ReportData {
  jobId: string;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  parameters: AnalysisParameters; // Теперь включает snapshotDate
  selectedArea: Feature<Polygon | Point>; // The area submitted
  // Add fields for report results
  mapCenter?: [number, number]; // Center for the report map view [latitude, longitude]
  mapZoom?: number;           // Zoom level for report map
  resultGeoJson?: FeatureCollection<Polygon | Point, { health: string }>; // Colored result area(s)
  satelliteImages?: { url: string; date: string; cloudCover: number }[]; // List of images used
  summary: string;            // Text summary of the report
  error?: string;             // Error message if status is FAILED

  // Добавленные поля для отчета
  areaSqKm?: number;           // Площадь в км²
  snapshotImageUrl?: string;   // URL RGB снимка
  ndviImageUrl?: string;       // URL NDVI карты
  stressZoneImageUrl?: string; // URL карты стресс-зон
  stressPercentage?: number;   // Процент стрессовых зон
}

// Re-add JobProgress interface as it's used by SSE client and page
export interface JobProgress {
    progress: number; // 0-100
    statusMessage: string; // e.g., "Downloading satellite images...", "Analyzing NDVI..."
    isComplete: boolean;
    error?: string;
}

// --- API Functions --- //

// Helper function to handle fetch errors
async function handleApiError(response: Response): Promise<Error> {
  let errorMsg = `API Error: ${response.status} ${response.statusText}`;
  // Use 'unknown' instead of 'any' for better type safety
  let errorDetails: unknown = null; 
  try {
      const errorData = await response.json();
      // Try to extract a meaningful message from common error response formats
      // Type assertion needed after checking properties
      if (typeof errorData === 'object' && errorData !== null) {
          errorMsg = (errorData as any).detail || (errorData as any).message || (errorData as any).error || errorMsg;
          errorDetails = (errorData as any).details || errorData;
      } else {
          errorDetails = errorData; // Keep original if not object
      }
  } catch (parseError) { // Give the catch variable a name
      // If response body is not JSON or empty, use the status text
      console.warn("Could not parse error response body as JSON:", parseError);
  }
  console.error('API Call Failed:', errorMsg, 'Details:', errorDetails);
  // Consider creating a custom error class
  const error = new Error(errorMsg);
  // Attach details more safely if needed, or omit these lines if not strictly necessary
  // (error as any).status = response.status; // Avoid 'any' if possible
  // (error as any).details = errorDetails;
  return error;
}

/**
 * Sends a request to start the field analysis.
 */
export async function startAnalysis(payload: StartAnalysisPayload): Promise<StartAnalysisResponse> {
  console.log('Запуск аналізу з параметрами:', payload);
  try {
    const response = await fetch(`${API_BASE_URL}/analyze`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      // Use the helper function to create and log the error
      throw await handleApiError(response);
    }

    return await response.json();

  } catch (error) {
    // Handle network errors or errors thrown by handleApiError
    console.error('Error starting analysis:', error);
    // Re-throw the error so the calling component can handle it (e.g., show UI message)
    throw error;
  }
}

/**
 * Fetches the full analysis report data for a given job ID.
 */
export async function fetchReport(jobId: string): Promise<ReportData> {
  console.log(`Завантаження звіту для ID завдання: ${jobId}`);

  try {
    const response = await fetch(`${API_BASE_URL}/report/${jobId}`);

    if (!response.ok) {
       // Use the helper function, handle 404 specifically if needed
       if (response.status === 404) {
          console.warn(`Report for job ${jobId} not found (404).`);
          // Throw a specific error message for 404
          throw new Error("Звіт не знайдено або ще не готовий.");
       }
       // For other errors, use the helper
       throw await handleApiError(response);
    }

    const report: ReportData = await response.json();
    console.log('Завантажені дані звіту:', report);
    return report;

  } catch (error) {
    // Handle network errors or errors thrown by handleApiError/404 check
    console.error('Error fetching report:', error);
    // Re-throw the error for the UI
    throw error;
  }
} 