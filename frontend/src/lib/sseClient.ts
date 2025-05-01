import { JobProgress, API_BASE_URL } from './api';

interface SseCallbacks {
  onProgress: (data: JobProgress) => void;
  onError: (error: Error) => void;
  onComplete: (data: JobProgress) => void;
}

export function subscribeToJobProgress(
  jobId: string,
  { onProgress, onError, onComplete }: SseCallbacks
): () => void {
  let eventSource: EventSource | null = null;
  let reconnectTimeout: NodeJS.Timeout | null = null;
  const url = `${API_BASE_URL}/api/progress/${jobId}`;

  console.log(`[SSE Client] Connecting to: ${url}`);

  const connect = () => {
    if (eventSource) {
      eventSource.close();
    }
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout);
      reconnectTimeout = null;
    }

    try {
      console.log(`SSE: Підключення до ${url}`);
      eventSource = new EventSource(url);

      eventSource.onmessage = (event) => {
        try {
          const data: JobProgress = JSON.parse(event.data);
          console.log('SSE: Отримано дані', data);

          onProgress(data);

          if (data.isComplete) {
            console.log('SSE: Отримано повідомлення про завершення прогресу.');
            onComplete(data);
            closeConnection();
          } else if (data.error) {
            console.error('SSE: Отримано повідомлення про помилку', data.error);
            onError(new Error(data.error));
            closeConnection();
          }
        } catch (parseError) {
          console.error('SSE: Помилка розбору даних повідомлення', parseError);
          onError(parseError instanceof Error ? parseError : new Error('Не вдалося розібрати повідомлення SSE'));
          closeConnection();
        }
      };

      eventSource.onerror = (errorEvent) => {
        console.error('SSE: Помилка з\'єднання', errorEvent);
        if (eventSource?.readyState === EventSource.CLOSED) {
          console.log('SSE: З\'єднання закрито сервером або мережева помилка. Спроба перепідключення...');
          if (!reconnectTimeout) {
            reconnectTimeout = setTimeout(() => {
              console.log('SSE: Перепідключення...');
              connect();
            }, 5000);
          }
        } else {
          onError(new Error('Помилка SSE з\'єднання'));
          closeConnection();
        }
      };

      eventSource.onopen = () => {
        console.log(`SSE: З\'єднання відкрито до ${url}`);
        if (reconnectTimeout) {
          clearTimeout(reconnectTimeout);
          reconnectTimeout = null;
        }
      };

    } catch (err) {
      console.error('SSE: Не вдалося створити EventSource', err);
      onError(err instanceof Error ? err : new Error('Не вдалося створити EventSource'));
    }
  };

  const closeConnection = () => {
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout);
      reconnectTimeout = null;
    }
    if (eventSource) {
      console.log(`SSE: Закриття з\'єднання до ${url}`);
      eventSource.close();
      eventSource = null;
    }
  };

  connect();

  return closeConnection;
} 