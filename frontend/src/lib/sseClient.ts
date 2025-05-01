import { JobProgress, API_BASE_URL } from './api';

interface PollCallbacks {
  onProgress: (data: JobProgress) => void;
  onError: (error: Error) => void;
  onComplete: (data: JobProgress) => void;
}

// Poll interval in milliseconds
const POLL_INTERVAL_MS = 2000;

/**
 * Subscribe to job progress via simple HTTP polling every `POLL_INTERVAL_MS`.
 * Returns a cleanup function that stops the polling.
 */
export function subscribeToJobProgress(
  jobId: string,
  { onProgress, onError, onComplete }: PollCallbacks
): () => void {
  const url = `${API_BASE_URL}/api/progress/${jobId}`;
  let intervalId: NodeJS.Timeout | null = null;
  let stopped = false;

  console.log(`[Progress Poller] Starting polling for job ${jobId} at ${url}`);

  const poll = async () => {
    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data: JobProgress = await response.json();
      console.log('[Progress Poller] Received data:', data);

      onProgress(data);

      if (data.isComplete) {
        console.log('[Progress Poller] Job complete, stopping polling.');
        onComplete(data);
        cleanup();
      }
    } catch (err) {
      if (stopped) return; // Ignore errors after cleanup
      console.error('[Progress Poller] Error while polling progress:', err);
      onError(err instanceof Error ? err : new Error('Unknown polling error'));
      cleanup();
    }
  };

  // Kick-off immediately and then repeat every interval
  poll();
  intervalId = setInterval(poll, POLL_INTERVAL_MS);

  const cleanup = () => {
    if (stopped) return;
    stopped = true;
    if (intervalId) {
      clearInterval(intervalId);
      intervalId = null;
    }
    console.log(`[Progress Poller] Stopped polling for job ${jobId}`);
  };

  return cleanup;
} 