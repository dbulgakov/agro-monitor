// Removed NextRequest import; using standard Request type

import { JobProgress } from '@/lib/api';

// Helper function to create SSE messages
function createSSEMessage(data: JobProgress | { error: string }): string {
    return `data: ${JSON.stringify(data)}\n\n`;
}

// Use standard Request and destructure context.params
export async function GET(request: Request, { params }: { params: Promise<{ jobId: string }> }) {
    const { jobId } = await params;

    if (!jobId) {
      console.error('[Mock SSE] ID завдання не знайдено в шляху!');
      return new Response('ID завдання відсутнє', { status: 400 });
    }
    console.log(`[Mock SSE /api/progress/${jobId}] Запит на з\'єднання`);

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
        async start(controller) {
            console.log(`[Mock SSE /api/progress/${jobId}] Потік запущено`);
            let progress = 0;
            let intervalId: NodeJS.Timeout | null = null;

            const sendProgress = () => {
                if (progress >= 100) {
                    const finalData: JobProgress = {
                        progress: 100,
                        statusMessage: 'Аналіз завершено успішно!',
                        isComplete: true,
                    };
                    console.log(`[Mock SSE /api/progress/${jobId}] Надсилання повідомлення про завершення`);
                    controller.enqueue(encoder.encode(createSSEMessage(finalData)));
                    if (intervalId) clearInterval(intervalId);
                    controller.close();
                    return;
                }

                progress += Math.floor(Math.random() * 15) + 5;
                progress = Math.min(progress, 100);

                const statusMessages = [
                    'Завантаження супутникових знімків...',
                    'Обробка NDVI...',
                    'Кластеризація даних...',
                    'Формування карти зон...',
                    'Підготовка резюме...',
                ];
                const currentStatus = statusMessages[Math.min(Math.floor(progress / 20), statusMessages.length - 1)];

                const progressData: JobProgress = {
                    progress: progress,
                    statusMessage: currentStatus,
                    isComplete: false,
                };
                console.log(`[Mock SSE /api/progress/${jobId}] Надсилання прогресу: ${progress}% (${currentStatus})`);
                controller.enqueue(encoder.encode(createSSEMessage(progressData)));
            };

            request.signal.addEventListener('abort', () => {
                console.log(`[Mock SSE /api/progress/${jobId}] З\'єднання закрито клієнтом`);
                if (intervalId) clearInterval(intervalId);
                // eslint-disable-next-line @typescript-eslint/no-unused-vars
                try { controller.close(); } catch (e) { /* Ignore - Keep comment or add specific logging */ }
            });

            await new Promise(resolve => setTimeout(resolve, 50));
            sendProgress();

            intervalId = setInterval(sendProgress, Math.random() * 1000 + 1000);

        },
        cancel(reason) {
            console.log(`[Mock SSE /api/progress/${jobId}] Потік скасовано. Причина: ${reason}`);
        }
    });

    return new Response(stream, {
        headers: {
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        },
    });
}
