import { NextResponse } from 'next/server';
import { ReportData, StartAnalysisPayload, AnalysisParameters } from '@/lib/api'; // Use the actual ReportData type
import { Feature, Polygon, Point } from 'geojson';

// Updated helper function to generate mock report data matching the LATEST ReportData interface
function generateMockReport(jobId: string): ReportData {
    // Mock the original request payload
    const requestPayload: StartAnalysisPayload = {
        area: {
            type: "Feature",
            properties: {},
            geometry: { 
                type: "Polygon",
                coordinates: [
                    [
                        [30.50, 50.45],
                        [30.51, 50.45],
                        [30.51, 50.46],
                        [30.50, 50.46],
                        [30.50, 50.45]
                    ]
                ]
            }
        },
        frequency: "single",
        ndvi_threshold: 0.3,
        date_range: "2024-04-01/2024-04-30",
        max_cloud_cover: 20,
        crop_type: "wheat", // Use valid CropType value
    };

    const stressPercentage = 24.6; // From example
    const areaSqKm = 1.5; // Mock value

    // Generate mock statistics
    const ndviStatistics = {
        mean: 0.42,
        min: 0.15,
        max: 0.78,
        std_dev: 0.11,
        stress_percentage: stressPercentage,
    };

    // Generate mock map URLs
    const mapUrls = {
        ndvi: "/placeholder-ndvi.jpg",
        rgb: "/placeholder-rgb.jpg", 
        stress: "/placeholder-stress.jpg",
    };
    
    // Generate mock image bounds [[min_lat, min_lon], [max_lat, max_lon]]
    const imageBounds: [[number, number], [number, number]] = [
      [50.448, 30.498], // SW corner (min lat, min lon)
      [50.462, 30.512]  // NE corner (max lat, max lon)
    ];

    // Переведенное резюме / рекомендации
    const recommendations = `У вказаній області аналізу були отримані супутникові дані Sentinel-2. За розрахунками NDVI середній індекс склав ${ndviStatistics.mean?.toFixed(2)}, що відповідає нормальному стану рослинності.\nОднак, близько ${stressPercentage}% площі демонструють ознаки вегетаційного стресу (NDVI < ${requestPayload.ndvi_threshold}), що може свідчити про:\n- пізній схід\n- нестачу вологи\n- агрохімічні обмеження`;

    return {
        jobId: jobId,
        status: 'COMPLETED',
        requestPayload: requestPayload,
        reportTimestamp: new Date().toISOString(),
        ndviStatistics: ndviStatistics,
        mapUrls: mapUrls,
        recommendations: recommendations,
        errorMessage: null,
        selectedArea: requestPayload.area, // Use area from request payload
        areaSqKm: areaSqKm,
        mapCenter: [50.455, 30.505], // Center can be calculated or mock
        mapZoom: 14, // Mock zoom
        imageBounds: imageBounds, // Add mock bounds
    };
}

// Define the type for route parameters, wrapping params in a Promise
type RouteParams = {
  params: Promise<{ // Wrap params in Promise
    jobId: string;
  }>
}

// Update the function signature to accept the props object
export async function GET(request: Request, props: RouteParams) {
    // Await the params object before accessing jobId
    const params = await props.params;
    const jobId = params.jobId; 

    // Remove logging for params object as we are using context now
    // console.log("[Mock Report API] Received params object:", params);

    if (!jobId) {
        console.error('[Mock Report API] Job ID missing in path');
        // Перевод сообщения об ошибке
        return NextResponse.json({ message: 'ID завдання відсутнє' }, { status: 400 });
    }

    console.log(`[Mock Report API /api/report/${jobId}] Запит отримано`);

    // Simulate some delay
    await new Promise(resolve => setTimeout(resolve, 500));

    // Generate and return new mock report data structure matching ReportData interface
    const mockReport = generateMockReport(jobId);
    console.log(`[Mock Report API /api/report/${jobId}] Повернення тестового звіту`);
    return NextResponse.json(mockReport, { status: 200 });
}