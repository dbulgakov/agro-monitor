// Placeholder for API interaction functions
import { Feature, Polygon, Point, FeatureCollection } from 'geojson';

// Base URL for your backend API
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || '/api'; // Use environment variable or proxy

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

/**
 * Sends a request to start the field analysis.
 */
export async function startAnalysis(payload: StartAnalysisPayload): Promise<StartAnalysisResponse> {
  console.log('Запуск аналізу з параметрами:', payload);

  const response = await fetch(`${API_BASE_URL}/analyze`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let errorMsg = `Помилка API: ${response.status} ${response.statusText}`;
    try {
        const errorData = await response.json();
        errorMsg = errorData.message || errorData.error || errorMsg;
    } catch (e) { /* Ignore parsing error */ }
    console.error('Помилка API запуску аналізу:', errorMsg);
    throw new Error(errorMsg);
  }

  return await response.json();

  // // Simulate API call
  // await new Promise(resolve => setTimeout(resolve, 800));
  // const simulatedJobId = `job_${Date.now()}`;
  // console.log('Simulated Job ID:', simulatedJobId);
  // // Simulate potential error
  // // if (Math.random() < 0.2) {
  // //   throw new Error("Simulated backend error: Invalid date range.");
  // // }
  // return { jobId: simulatedJobId };
}

/**
 * Fetches the full analysis report data for a given job ID.
 */
export async function fetchReport(jobId: string): Promise<ReportData> {
  console.log(`Завантаження звіту для ID завдання: ${jobId}`);

  const response = await fetch(`${API_BASE_URL}/report/${jobId}`);
  if (response.status === 404) {
      throw new Error("Звіт не знайдено.");
  }
  if (!response.ok) {
    let errorMsg = `Помилка API: ${response.status} ${response.statusText}`;
    try {
        const errorData = await response.json();
        errorMsg = errorData.message || errorData.error || errorMsg;
    } catch (e) { /* Ignore */ }
    console.error('Помилка API завантаження звіту:', errorMsg);
    throw new Error(errorMsg);
  }
  const report: ReportData = await response.json();
  console.log('Завантажені дані звіту:', report);
  return report;

//   // Simulate API call with dummy data
//   await new Promise(resolve => setTimeout(resolve, 1000));

//   // Example dummy report data (replace with data structure from your backend)
//   const dummySelectedArea: Feature<Polygon> = {
//       type: "Feature",
//       properties: {},
//       geometry: { // Example polygon coordinates
//           type: "Polygon",
//           coordinates: [
//               [
//                   [30.50, 50.45],
//                   [30.51, 50.45],
//                   [30.51, 50.46],
//                   [30.50, 50.46],
//                   [30.50, 50.45]
//               ]
//           ]
//       }
//   };

//   const dummyReport: ReportData = {
//     jobId: jobId,
//     status: 'COMPLETED',
//     parameters: {
//         ndvi_threshold: 0.3,
//         date_range: "2024-06-01/2024-07-30",
//         max_cloud_cover: 20,
//         crop_type: "wheat"
//     },
//     selectedArea: dummySelectedArea,
//     mapCenter: [50.455, 30.505], // Center based on selected area
//     mapZoom: 14,
//     resultGeoJson: {
//       type: "FeatureCollection",
//       features: [
//         { // Example good area
//           type: "Feature", properties: { health: 'good' },
//           geometry: { type: "Polygon", coordinates: [ [ [30.501, 50.451], [30.505, 50.451], [30.505, 50.455], [30.501, 50.455], [30.501, 50.451] ] ] }
//         },
//         { // Example average area
//             type: "Feature", properties: { health: 'average' },
//             geometry: { type: "Polygon", coordinates: [ [ [30.506, 50.456], [30.509, 50.456], [30.509, 50.459], [30.506, 50.459], [30.506, 50.456] ] ] }
//         }
//       ]
//     },
//     satelliteImages: [
//       { url: "/placeholder-image.jpg", date: "2024-07-15", cloudCover: 5 },
//       { url: "/placeholder-image.jpg", date: "2024-07-22", cloudCover: 15 },
//     ],
//     summary: `Отчет для задачи ${jobId}:
// Область: Полигон [координаты...]
// Параметры: NDVI > 0.3, Период Июнь-Июль, Облачность < 20%, Культура: Пшеница.
// Состояние поля в целом хорошее. Выявлены незначительные участки со средней вегетацией на севере.
// Использовано 2 снимка (5% и 15% облачности).`,
//   };
//   console.log('Simulated Report Data:', dummyReport);
//   // Simulate report not found
//   // if (jobId === 'job_notfound') throw new Error("Отчет не найден.");
//   return dummyReport;
} 