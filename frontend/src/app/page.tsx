'use client'; // Required for useState, useRouter, onClick

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Feature, Polygon, Point, Position } from 'geojson';
import dynamic from 'next/dynamic'; // Import dynamic
import CropTypeSelect from '@/components/CropTypeSelect';
import FrequencySelect from '@/components/FrequencySelect';
import ProgressBar from '@/components/ProgressBar'; // Import ProgressBar
import { startAnalysis, CropType, AnalysisFrequency, AnalysisParameters, JobProgress } from '@/lib/api';
import { subscribeToJobProgress } from '@/lib/sseClient'; // Import SSE subscriber
import DatePicker, { registerLocale } from "react-datepicker";
import { uk } from 'date-fns/locale/uk'; // Corrected import
import "react-datepicker/dist/react-datepicker.css";
import "@/styles/datepicker-custom.css";
import type { MapSelectorRef } from '@/components/MapSelector'; // Import ref type

// Register Ukrainian locale
registerLocale('uk', uk);

// Dynamically import MapSelector
const MapSelector = dynamic(() => import('@/components/MapSelector'), {
    ssr: false,
    loading: () => <div className="h-full w-full bg-gray-200 flex items-center justify-center"><p>Завантаження мапи...</p></div>
});

// Constants for options
const CROP_OPTIONS: { value: CropType; label: string }[] = [
  { value: 'wheat', label: 'Пшениця' },
  { value: 'corn', label: 'Кукурудза' },
  { value: 'sunflower', label: 'Соняшник' },
  { value: 'soy', label: 'Соя' },
];

const FREQUENCY_OPTIONS: { value: AnalysisFrequency; label: string }[] = [
  { value: 'single', label: 'Одноразовий' },
  // { value: 'weekly', label: 'Еженедельный' }, // Weekly might need more backend logic
];

const NDVI_THRESHOLDS = [
  { value: 0.2, label: '0.2 (Сильний стрес)' },
  { value: 0.3, label: '0.3 (Стандарт)' },
  { value: 0.4, label: '0.4 (Чутливий)' },
];

const CLOUD_COVER_OPTIONS = [
  { value: 10, label: '10% (Чисте небо)' },
  { value: 20, label: '20% (Компроміс)' },
  { value: 40, label: '40% (Більше знімків)' },
];

// --- Helper Functions --- //
const formatDate = (date: Date | null): string => {
    return date ? date.toISOString().split('T')[0] : '';
};

const parseDate = (dateStr: string): Date | null => {
    const date = new Date(dateStr);
    return isNaN(date.getTime()) ? null : date;
};

const formatCoordinates = (geometry: Polygon | Point | undefined): string => {
    if (!geometry) return 'Координати відсутні';
    if (geometry.type === 'Point') {
        return `Точка: [${geometry.coordinates[1].toFixed(4)}, ${geometry.coordinates[0].toFixed(4)}]`;
    }
    if (geometry.type === 'Polygon') {
        // Show first few points for brevity
        const points = geometry.coordinates[0]; // Exterior ring
        const displayPoints = points.slice(0, 3);
        let coordString = displayPoints.map(p => `[${p[1].toFixed(4)}, ${p[0].toFixed(4)}]`).join(', ');
        if (points.length > 3) coordString += ', ...';
        return `Полігон (${points.length} точок): ${coordString}`;
    }
    return 'Невідомий тип геометрії';
};

// --- Component --- //
type AnalysisStatus = 'idle' | 'starting' | 'processing-sse' | 'success' | 'error';

export default function HomePage() {
  const router = useRouter();
  const sseCleanupRef = useRef<(() => void) | null>(null);
  const mapSelectorRef = useRef<MapSelectorRef | null>(null); // Create ref for MapSelector

  // --- State Hooks --- //
  const [selectedArea, setSelectedArea] = useState<Feature<Polygon | Point> | null>(null);
  const [analysisParams, setAnalysisParams] = useState<AnalysisParameters>({
    ndvi_threshold: 0.3,
    date_range: '', // Initialized empty, will be set by date picker
    max_cloud_cover: 20,
    crop_type: CROP_OPTIONS[0].value,
  });
  const [frequency, setFrequency] = useState<AnalysisFrequency>(FREQUENCY_OPTIONS[0].value);
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus>('idle');
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobProgress, setJobProgress] = useState<JobProgress | null>(null);
  const [error, setError] = useState<string | null>(null);

  // State for Date Picker
  const [startDate, setStartDate] = useState<Date | null>(() => {
      const date = new Date();
      date.setMonth(date.getMonth() - 3);
      return date;
  });
  const [endDate, setEndDate] = useState<Date | null>(new Date());

  // --- Handlers & Logic --- //

  // Define resetState first
  const resetState = useCallback(() => {
     setSelectedArea(null);
     setAnalysisStatus('idle');
     setJobId(null);
     setJobProgress(null);
     setError(null);
     sseCleanupRef.current?.();
     sseCleanupRef.current = null;
     // --- Call clearLayers on MapSelector --- //
     console.log('[HomePage] Скидання стану, очищення шарів мапи.');
     mapSelectorRef.current?.clearLayers();
     // -------------------------------------- //
  }, []);

  const handleParamChange = useCallback(<K extends keyof AnalysisParameters>(
    param: K,
    value: AnalysisParameters[K]
  ) => {
    setAnalysisParams(prev => ({ ...prev, [param]: value }));
  }, []);

  // Now define handleAreaSelect which uses resetState
  const handleAreaSelect = useCallback((area: Feature<Polygon | Point> | null) => {
    console.log("Вибрана область (GeoJSON):", area);
    setSelectedArea(area);
    if (analysisStatus !== 'idle') {
       resetState();
    }
  }, [analysisStatus, resetState]);

  // Update date_range when dates change
  useEffect(() => {
    if (startDate && endDate) {
      handleParamChange('date_range', `${formatDate(startDate)}/${formatDate(endDate)}`);
    } else {
      handleParamChange('date_range', '');
    }
  }, [startDate, endDate, handleParamChange]);

  // Cleanup SSE connection ONLY on component unmount
  useEffect(() => {
    // Store the ref value in a variable inside the effect scope
    const cleanupFunc = sseCleanupRef.current;
    return () => {
      console.log('[HomePage Cleanup] Компонент демонтується, очищення SSE з\'єднання.');
      cleanupFunc?.(); // Use the captured value
    };
  }, []); // <-- Empty dependency array

  const handleAnalyzeClick = async () => {
    if (!isAreaSelected || !isDateRangeValid) {
      setError(!isAreaSelected ? 'Виберіть область на мапі.' : 'Виберіть коректний діапазон дат.');
      return;
    }
    setError(null);
    // setJobId(null); // Not strictly necessary if we clean up below
    setJobProgress({ progress: 0, statusMessage: 'Запуск аналізу...', isComplete: false });
    setAnalysisStatus('starting');

    // --- Clean up any PREVIOUS SSE connection --- //
    console.log('[HomePage] Очищення попереднього SSE з\'єднання (якщо є).');
    sseCleanupRef.current?.(); // Call cleanup from the ref
    sseCleanupRef.current = null; // Clear the ref
    // ------------------------------------------ //

    try {
      const payload = {
        ...analysisParams,
        area: selectedArea,
        frequency: frequency,
      };
      const response = await startAnalysis(payload);
      console.log('Аналіз запущено, ID Завдання:', response.jobId);
      setJobId(response.jobId); // Update jobId state
      // Status will be set by the first onProgress event

      // --- Subscribe to NEW SSE --- //
      console.log('[HomePage] Підписка на нове SSE з\'єднання.');
      sseCleanupRef.current = subscribeToJobProgress(response.jobId, {
          onProgress: (progressData) => {
              console.log('[HomePage] Отримано прогрес:', progressData); 
              setJobProgress(progressData); 
              
              setAnalysisStatus(currentStatus => {
                  if (currentStatus !== 'processing-sse' && currentStatus !== 'error' && currentStatus !== 'success' && !progressData.isComplete) {
                      console.log('[HomePage] Встановлення статусу processing-sse');
                      return 'processing-sse';
                  }
                  return currentStatus; 
              });
          },
          onComplete: (finalData) => {
              console.log('[HomePage] SSE Завершено:', finalData); 
              setJobProgress(finalData);
              setAnalysisStatus('success');
              sseCleanupRef.current = null; // Clear ref on completion
          },
          onError: (err) => {
              console.error('[HomePage] Помилка SSE:', err);
              setError(err.message || 'Помилка SSE з\'єднання.');
              setAnalysisStatus('error');
              setJobProgress((prev: JobProgress | null) => ({ 
                  ...(prev ?? { progress: 0, isComplete: false }), 
                  statusMessage: 'Помилка SSE', 
                  error: err.message 
              }));
              sseCleanupRef.current = null; // Clear ref on error
          }
      });
      // ------------------------ //

    } catch (err) {
      console.error('Не вдалося запустити аналіз:', err);
      const errorMessage = err instanceof Error ? err.message : 'Не вдалося запустити аналіз.';
      setError(errorMessage);
      setAnalysisStatus('error');
      setJobProgress(null);
      // Ensure cleanup ref is cleared on API error too
      sseCleanupRef.current = null;
    }
  };

  const handleGoToReport = () => {
    if (jobId) {
      router.push(`/report/${jobId}`);
    }
  };

  // --- Derived State --- //
  const isAreaSelected = selectedArea !== null;
  const isDateRangeValid = startDate && endDate && startDate <= endDate;
  const isLoading = analysisStatus === 'starting' || analysisStatus === 'processing-sse';

  // --- Render --- //
  return (
    <div className="flex flex-col md:flex-row h-screen bg-gray-100">
      {/* Map Section */}
      <div className="w-full md:w-2/3 h-1/2 md:h-full relative border-r border-gray-300">
        {/* Pass the ref to MapSelector */}
        <MapSelector ref={mapSelectorRef} onAreaSelect={handleAreaSelect} />
      </div>

      {/* Settings Panel */}
      <div className="w-full md:w-1/3 h-1/2 md:h-full p-5 md:p-6 bg-white overflow-y-auto flex flex-col">
        <h1 className="text-xl font-semibold mb-5 text-gray-800 border-b border-gray-200 pb-3">
           Параметри аналізу поля
        </h1>

        {/* Form Section */}
        <div className="flex-grow space-y-4 text-sm">
          {/* Area Selection Info */}
          <div className={`p-3 rounded-md border ${isAreaSelected ? 'border-blue-300 bg-blue-50' : 'border-gray-300 bg-gray-50'}`}>
            <p className={`font-medium ${isAreaSelected ? 'text-blue-800' : 'text-gray-700'}`}>
              {isAreaSelected ? 'Область для аналізу вибрана' : 'Намалюйте область на мапі'}
            </p>
            {selectedArea && (
              <div className="text-xs text-gray-500 mt-1 space-y-0.5">
                 <p>Тип: {selectedArea.geometry.type}</p>
                 <p className="break-all">{formatCoordinates(selectedArea.geometry)}</p>
              </div>
            )}
          </div>

          {/* Crop Type */}
          <div>
            <label htmlFor="crop_type" className="block font-medium text-black mb-1">Культура</label>
            <CropTypeSelect
              value={analysisParams.crop_type}
              onChange={(v) => handleParamChange('crop_type', v as CropType)}
              options={CROP_OPTIONS}
            />
          </div>

          {/* NDVI Threshold */}
          <div>
            <label htmlFor="ndvi_threshold" className="block font-medium text-black mb-1">Поріг NDVI</label>
            <select
              id="ndvi_threshold"
              value={analysisParams.ndvi_threshold}
              onChange={(e) => handleParamChange('ndvi_threshold', parseFloat(e.target.value))}
              className="input-style"
            >
              {NDVI_THRESHOLDS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

           {/* Cloud Cover */}
           <div>
            <label htmlFor="max_cloud_cover" className="block font-medium text-black mb-1">Макс. хмарність (%)</label>
            <select
              id="max_cloud_cover"
              value={analysisParams.max_cloud_cover}
              onChange={(e) => handleParamChange('max_cloud_cover', parseInt(e.target.value, 10))}
              className="input-style"
            >
              {CLOUD_COVER_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          {/* Date Range Picker */}
          <div>
            <label className="block font-medium text-black mb-1">Діапазон дат</label>
            <div className="flex items-center space-x-2">
                 <DatePicker
                    selected={startDate}
                    onChange={(date) => setStartDate(date)}
                    selectsStart
                    startDate={startDate}
                    endDate={endDate}
                    dateFormat="dd/MM/yyyy"
                    locale="uk"
                    className="input-style w-full"
                    placeholderText="Початок"
                 />
                 <span className="text-gray-500">-</span>
                 <DatePicker
                    selected={endDate}
                    onChange={(date) => setEndDate(date)}
                    selectsEnd
                    startDate={startDate}
                    endDate={endDate}
                    minDate={startDate || undefined} // Pass undefined if startDate is null
                    dateFormat="dd/MM/yyyy"
                    locale="uk"
                    className="input-style w-full"
                    placeholderText="Кінець"
                 />
            </div>
            {!isDateRangeValid && startDate && endDate && (
                 <p className="text-xs text-red-500 mt-1">Дата початку не може бути пізніше дати кінця.</p>
            )}
          </div>
        </div>

        {/* Action Area */}
        <div className="mt-6 pt-5 border-t border-gray-200 space-y-3">
           {/* Error Display */}
            {analysisStatus === 'error' && (
                <div className="p-3 bg-red-100 border border-red-300 text-red-700 text-sm rounded-md break-words">
                    <b>Помилка:</b> {error || 'Сталася невідома помилка.'}
                </div>
            )}

            {/* Progress Display */}
            {analysisStatus === 'processing-sse' && jobProgress && (
                <div className="space-y-1.5 text-center">
                    <p className="text-sm font-medium text-gray-600">
                       <span className="inline-block align-middle mr-2">
                           <svg className="animate-spin h-4 w-4 text-blue-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                       </span>
                       {jobProgress.statusMessage} ({jobProgress.progress}%)
                    </p>
                    <ProgressBar progress={jobProgress.progress} />
                </div>
            )}

          {/* Analyze Button / Go To Report Button */}
          {analysisStatus !== 'success' ? (
            <button
              className={`w-full flex justify-center items-center bg-blue-600 text-white py-2.5 px-4 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition duration-150 ease-in-out font-medium`}
              onClick={handleAnalyzeClick}
              disabled={isLoading || !isAreaSelected || !isDateRangeValid}
            >
              {analysisStatus === 'starting' || analysisStatus === 'processing-sse' ? (
                 <>
                    {/* Optional: Keep spinner only for 'starting'? */}
                    {/* {analysisStatus === 'starting' && (
                         <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    )} */} 
                    <span>{analysisStatus === 'starting' ? 'Запуск...' : 'Виконується...'}</span>
                </>
              ) :
               'Аналізувати'}
            </button>
          ) : (
            // Success State
            <button
              className="w-full bg-green-600 text-white py-2.5 px-4 rounded-md hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500 transition duration-150 ease-in-out font-medium"
              onClick={handleGoToReport}
            >
              Перейти до звіту (Завдання: {jobId})
            </button>
          )}

          {/* Reset button appears on success or error */}
          {(analysisStatus === 'success' || analysisStatus === 'error') && (
             <button
                 onClick={resetState}
                 className="w-full text-center text-xs text-gray-500 hover:text-gray-700 mt-2 underline"
             >
                Скинути та почати спочатку
             </button>
          )}
        </div>
      </div>
    </div>
  );
}

// Helper for input styles (can be moved to globals.css or a dedicated utility)
const inputStyle = "mt-1 block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm rounded-md shadow-sm";
