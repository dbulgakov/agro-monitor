import { NextResponse } from 'next/server';
import { ReportData, AnalysisParameters } from '@/lib/api';
import { Feature, Polygon, Point } from 'geojson';

// Define a more specific structure for the mock report based on user request
// Note: The actual ReportData type in api.ts might need updating later
interface MockReportStructure {
    jobId: string;
    status: 'COMPLETED';
    parameters: {
        crop_type: string;         // Тип культуры
        ndvi_threshold: number;      // NDVI-порог
        max_cloud_cover: number;     // Облачность
        snapshotDate: string;       // Дата снимка (из примера)
        // format?: string;         // Формат вывода (не нужен в данных)
    };
    selectedArea: Feature<Polygon>; // Keep for map context
    mapCenter: [number, number];
    mapZoom: number;
    areaSqKm: number;               // Площадь
    snapshotImageUrl?: string;      // URL оригинального снимка (RGB)
    ndviImageUrl?: string;          // URL NDVI-карты
    stressZoneImageUrl?: string;    // URL карты стресс-зон
    stressPercentage: number;       // Процент стресс-зон
    summary: string;                // Автоматический текстовый отчёт
    satelliteImages?: { url: string; date: string; cloudCover: number }[]; // Добавлено обратно
    // resultGeoJson?: FeatureCollection<Polygon | Point, { health: string }>; // Пока убрано, т.к. не используется на карте
}

// Updated helper function to generate new mock report data
function generateMockReport(jobId: string): MockReportStructure {
    const parameters = {
        crop_type: "Пшениця (wheat)", // Переведено
        ndvi_threshold: 0.3,           // From example
        max_cloud_cover: 20,         // From example
        snapshotDate: "12 квітня 2024", // Переведено
    };

    const selectedArea: Feature<Polygon> = {
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
    };

    const stressPercentage = 24.6; // From example
    const areaSqKm = 1.5; // Mock value

    // Переведенное резюме
    const summary = `У вказаній області аналізу були отримані супутникові дані Sentinel-2 від ${parameters.snapshotDate}. За розрахунками NDVI середній індекс склав 0.42, що відповідає нормальному стану рослинності.\nОднак, близько ${stressPercentage}% площі демонструють ознаки вегетаційного стресу (NDVI < ${parameters.ndvi_threshold}), що може свідчити про:\n- пізній схід\n- нестачу вологи\n- агрохімічні обмеження`;

    // Мок спутниковых снимков
    const satelliteImages = [
        { url: "/placeholder-rgb.jpg", date: "2024-04-12T10:00:00Z", cloudCover: 5 }, // Используем дату из параметров
        // { url: "/placeholder-image.jpg", date: "2024-07-22T11:30:00Z", cloudCover: 15 }, // Можно добавить еще, если нужно
    ];

    return {
        jobId: jobId,
        status: 'COMPLETED',
        parameters: parameters,
        selectedArea: selectedArea,
        mapCenter: [50.455, 30.505],
        mapZoom: 14,
        areaSqKm: areaSqKm,
        snapshotImageUrl: satelliteImages[0]?.url || "/placeholder-rgb.jpg", // Берем URL первого снимка
        ndviImageUrl: "/placeholder-ndvi.jpg",      // Placeholder URL
        stressZoneImageUrl: "/placeholder-stress.jpg",// Placeholder URL
        stressPercentage: stressPercentage,
        summary: summary,
        satelliteImages: satelliteImages, // Добавлено поле
    };
}

export async function GET(request: Request, { params }: { params: Promise<{ jobId: string }> }) {
    const { jobId } = await params;

    if (!jobId) {
        console.error('[Mock Report API] Job ID missing in path');
        // Перевод сообщения об ошибке
        return NextResponse.json({ message: 'ID завдання відсутнє' }, { status: 400 });
    }

    console.log(`[Mock Report API /api/report/${jobId}] Запит отримано`);

    // Simulate some delay
    await new Promise(resolve => setTimeout(resolve, 500));

    // Generate and return new mock report data structure
    const mockReport = generateMockReport(jobId);
    console.log(`[Mock Report API /api/report/${jobId}] Повернення тестової структури звіту`);
    return NextResponse.json(mockReport, { status: 200 });
} 