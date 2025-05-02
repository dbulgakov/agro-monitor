'use client';

import React, { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import dynamic from 'next/dynamic';
import { fetchReport, ReportData } from '@/lib/api'; // Import the fetch function and ReportData type
import 'leaflet/dist/leaflet.css';

// Dynamically import ReportView to ensure Leaflet loads client-side
const ReportView = dynamic(() => import('@/components/ReportView'), { ssr: false });

// Helper to format date range
const formatDateRange = (rangeString: string | undefined): string => {
  if (!rangeString || !rangeString.includes('/')) return 'Не указан';
  const [start, end] = rangeString.split('/');
  const formatDate = (dateStr: string) => {
      try {
          return new Date(dateStr).toLocaleDateString('ru-RU');
      } catch { return dateStr; }
  };
  return `${formatDate(start)} - ${formatDate(end)}`;
};

export default function ReportPage() {
  const params = useParams();
  const jobId = params.jobId as string;
  const [reportData, setReportData] = useState<ReportData | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (jobId) {
      setIsLoading(true);
      setError(null);
      fetchReport(jobId)
        .then((data) => {
          setReportData(data);
          setIsLoading(false);
        })
        .catch((err) => {
          console.error("Помилка завантаження звіту:", err);
          setError(err.message || 'Не вдалося завантажити звіт.');
          setIsLoading(false);
        });
    }
  }, [jobId]);

  const handlePrintReport = () => {
    window.print(); // Просто печатаем страницу
  };

  // --- Render Logic --- //

  const renderLoading = () => (
    <div className="flex flex-col items-center justify-center h-64 text-gray-600">
      <svg className="animate-spin h-8 w-8 text-blue-600 mb-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
         <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
         <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      <span>Завантаження звіту...</span> {/* Переведено */}
    </div>
  );

  const renderError = () => (
    <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded relative mb-6" role="alert">
      <strong className="font-bold">Помилка завантаження звіту!</strong> {/* Переведено */}
      <span className="block sm:inline ml-2">{error}</span>
    </div>
  );

  const renderReportContent = (data: ReportData) => (
    <div className="space-y-6"> {/* Уменьшаем основной отступ между блоками */} 
      {/* Map Section */}
      <div className="bg-white p-6 rounded-lg shadow-md border border-gray-200 mb-6"> {/* p-6, mb-6 */}
        <h2 className="text-lg font-semibold text-gray-800 mb-4">Карта області аналізу</h2> {/* text-lg, text-gray-800 */}
        <ReportView reportData={data} />
      </div>

      {/* Parameters Section */}
      <div className="bg-white p-6 rounded-lg shadow-md border border-gray-200 mb-6"> {/* p-6, mb-6 */}
        <h2 className="text-lg font-semibold text-gray-800 mb-4">Параметри аналізу</h2> {/* text-lg, text-gray-800 */}
        <table className="w-full text-sm text-left text-gray-600">
          <tbody>
            <tr className="odd:bg-gray-50"><td className="py-2 pr-3 font-medium">Тип культури:</td><td className="py-2">{data.requestPayload?.crop_type || 'N/A'}</td></tr>
            <tr className="odd:bg-gray-50"><td className="py-2 pr-3 font-medium">NDVI-поріг:</td><td className="py-2">{data.requestPayload?.ndvi_threshold ?? 'N/A'}</td></tr>
            <tr className="odd:bg-gray-50"><td className="py-2 pr-3 font-medium">Хмарність:</td><td className="py-2">≤ {data.requestPayload?.max_cloud_cover ?? 'N/A'}%</td></tr>
            <tr className="odd:bg-gray-50"><td className="py-2 pr-3 font-medium">Дата знімку:</td><td className="py-2">{formatDateRange(data.requestPayload?.date_range) || 'N/A'}</td></tr>
          </tbody>
        </table>
      </div>

      {/* Key Indicators Section */} 
      <div className="bg-white p-6 rounded-lg shadow-md border border-gray-200 mb-6"> {/* p-6, mb-6 */} 
        <h2 className="text-lg font-semibold text-gray-800 mb-4">Ключові показники</h2> {/* text-lg, text-gray-800 */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6"> {/* Увеличен gap */} 
           <div className="text-center p-4 border rounded-md">
             <p className="text-sm text-gray-500 mb-1">Площа аналізу</p>
             <p className="text-xl font-bold text-blue-600">{data.areaSqKm?.toFixed(2) ?? 'N/A'} км²</p> {/* text-xl */} 
           </div>
            <div className="text-center p-4 border rounded-md">
             <p className="text-sm text-gray-500 mb-1">Виявлено стрес-зон</p>
             <p className="text-xl font-bold text-orange-600">{data.ndviStatistics?.stress_percentage?.toFixed(1) ?? 'N/A'}%</p> {/* text-xl, text-orange-600 */} 
           </div>
        </div>
      </div>

      {/* Images Section */} 
      <div className="bg-white p-6 rounded-lg shadow-md border border-gray-200 mb-6"> {/* p-6, mb-6 */} 
        <h2 className="text-lg font-semibold text-gray-800 mb-4">Зображення</h2> {/* text-lg, text-gray-800 */} 
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 text-center"> {/* gap-8 */} 
          {/* Original Snapshot */}
          <div>
            <h3 className="text-base font-medium text-gray-700 mb-2">Оригінальне зображення (RGB)</h3> {/* text-base, text-gray-700 */} 
            {data.mapUrls?.rgb ? (
              <img src={data.mapUrls.rgb} alt="Супутниковий знімок RGB" className="w-full h-auto rounded border bg-gray-100 aspect-square object-cover shadow-sm"/>
            ) : (
              <div className="w-full h-40 rounded border bg-gray-100 flex items-center justify-center text-gray-400 flex-col text-xs shadow-sm">
                 <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-10 h-10 mb-2">
                   <path strokeLinecap="round" strokeLinejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm16.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Z" />
                 </svg>
                 Зображення недоступне
              </div>
            )}
            <p className="text-xs text-gray-600 mt-2">Знімок Sentinel-2, візуальний канал (RGB)</p> {/* text-gray-600, mt-2 */} 
          </div>
          {/* NDVI Map */}
          <div>
            <h3 className="text-base font-medium text-gray-700 mb-2">NDVI-карта</h3> {/* text-base, text-gray-700 */} 
            {data.mapUrls?.ndvi ? (
              <img src={data.mapUrls.ndvi} alt="NDVI карта" className="w-full h-auto rounded border bg-gray-100 aspect-square object-cover shadow-sm"/>
            ) : (
               <div className="w-full h-40 rounded border bg-gray-100 flex items-center justify-center text-gray-400 flex-col text-xs shadow-sm">
                 <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-10 h-10 mb-2">
                   <path strokeLinecap="round" strokeLinejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm16.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Z" />
                 </svg>
                  Зображення недоступне
               </div>
            )}
             {/* Легенда? */}
          </div>
          {/* Stress Zone Map */}
          <div>
            <h3 className="text-base font-medium text-gray-700 mb-2">Карта стрес-зон</h3> {/* text-base, text-gray-700 */} 
            {data.mapUrls?.stress ? (
              <img src={data.mapUrls.stress} alt="Карта стрес-зон" className="w-full h-auto rounded border bg-gray-100 aspect-square object-cover shadow-sm"/>
            ) : (
              <div className="w-full h-40 rounded border bg-gray-100 flex items-center justify-center text-gray-400 flex-col text-xs shadow-sm">
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-10 h-10 mb-2">
                   <path strokeLinecap="round" strokeLinejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm16.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Z" />
                 </svg>
                 Зображення недоступне
              </div>
            )}
            <p className="text-xs text-gray-600 mt-2">(NDVI &lt; {data.requestPayload?.ndvi_threshold ?? 'N/A'})</p> {/* text-gray-600, mt-2 */} 
          </div>
        </div>
      </div>
      
       {/* Summary Section */}
      <div className="bg-white p-6 rounded-lg shadow-md border border-gray-200 mb-6"> {/* p-6, mb-6 */} 
        <h2 className="text-lg font-semibold text-gray-800 mb-4">Текстовий звіт</h2> {/* text-lg, text-gray-800 */}
        <p className="text-sm text-gray-700 whitespace-pre-line leading-relaxed">
          {data.recommendations || "Резюме недоступне."}
        </p>
      </div>

      {/* Action Button */}
      <div className="mt-8 text-center md:text-left pb-10 print:hidden">
        <button
          onClick={handlePrintReport}
          className="bg-indigo-600 text-white py-2 px-6 rounded-md hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 transition duration-150 ease-in-out"
        >
          Зберегти / Друк 
        </button>
      </div>
    </div>
  );

  // --- Main Return --- //
  return (
    <div className="container mx-auto p-4 md:p-8 bg-gray-100 min-h-screen"> {/* bg-gray-100 */} 
      <div className="flex justify-between items-center mb-8"> {/* mb-8 */}
          <h1 className="text-2xl md:text-3xl font-bold text-gray-800">Звіт по аналізу поля</h1> 
           <p className="text-sm text-gray-500">ID Завдання: {jobId}</p> 
      </div>

      {isLoading && renderLoading()}

      {error && !isLoading && renderError()}

      {!isLoading && !error && reportData && renderReportContent(reportData)}

      {!isLoading && !error && !reportData && (
          <>
            <p className="text-center text-gray-500 mt-10">Дані звіту не знайдено або не завантажено.</p> 
          </>
      )}
    </div>
  );
}
// --- End of component --- 