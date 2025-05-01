// Removed NextRequest import; using standard Request type

import { JobProgress } from '@/lib/api';

// Mock progress polling endpoint for Next.js dev environment
export async function GET(request: Request, { params }: { params: Promise<{ jobId: string }> }) {
    const { jobId } = await params;

    if (!jobId) {
      console.error('[Mock Progress] ID завдання не знайдено в шляху!');
      return new Response('ID завдання відсутнє', { status: 400 });
    }
    console.log(`[Mock Progress /api/progress/${jobId}] Запит прогресу`);

    // Store progress state globally across requests (simple in-memory mock)
    const globalAny = global as any;
    globalAny.__mockJobProgress = globalAny.__mockJobProgress || {};
    const state = globalAny.__mockJobProgress;

    if (!state[jobId]) {
        state[jobId] = { progress: 0 };
    }

    let { progress } = state[jobId];
    if (progress < 100) {
        progress += Math.floor(Math.random() * 15) + 5;
        progress = Math.min(progress, 100);
    }
    state[jobId].progress = progress;

    const statusMessages = [
        'Завантаження супутникових знімків...',
        'Обробка NDVI...',
        'Кластеризація даних...',
        'Формування карти зон...',
        'Підготовка резюме...',
    ];
    const currentStatus =
        progress === 100
            ? 'Аналіз завершено успішно!'
            : statusMessages[Math.min(Math.floor(progress / 20), statusMessages.length - 1)];

    const payload: JobProgress = {
        progress,
        statusMessage: currentStatus,
        isComplete: progress === 100,
    };

    return new Response(JSON.stringify(payload), {
        headers: { 'Content-Type': 'application/json' },
        status: 200,
    });
}
