import { NextResponse } from 'next/server';
import { StartAnalysisPayload, StartAnalysisResponse } from '@/lib/api';

export async function POST(request: Request) {
  try {
    const payload: StartAnalysisPayload = await request.json();
    console.log('[Mock API /api/analyze] Отримано параметри:', payload);

    // Simulate some processing time
    await new Promise(resolve => setTimeout(resolve, 800));

    // Generate a fake Job ID
    const simulatedJobId = `mock_job_${Date.now()}`;
    console.log('[Mock API /api/analyze] Відповідь з ID Завдання:', simulatedJobId);

    const response: StartAnalysisResponse = { jobId: simulatedJobId };

    // Simulate a potential error randomly (e.g., 10% chance)
    // if (Math.random() < 0.1) {
    //   console.error('[Mock API /api/analyze] Simulating 500 error');
    //   return NextResponse.json({ message: 'Simulated internal server error' }, { status: 500 });
    // }

    return NextResponse.json(response, { status: 200 });

  } catch (error) {
    console.error('[Mock API /api/analyze] Помилка обробки запиту:', error);
    const errorMessage = error instanceof Error ? error.message : 'Невідома помилка';
    return NextResponse.json({ message: `Помилка розбору запиту: ${errorMessage}` }, { status: 400 });
  }
} 