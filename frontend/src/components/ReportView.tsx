'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import 'leaflet/dist/leaflet.css';
import { ReportData } from '@/lib/api'; // Import the full ReportData type
import { FeatureCollection, Point, Polygon, Feature, Geometry } from 'geojson';

// Dynamically import Leaflet components
const MapContainer = dynamic(() => import('react-leaflet').then(mod => mod.MapContainer), { ssr: false });
const TileLayer = dynamic(() => import('react-leaflet').then(mod => mod.TileLayer), { ssr: false });
const GeoJSON = dynamic(() => import('react-leaflet').then(mod => mod.GeoJSON), { ssr: false });

// Define props using the imported ReportData type
interface ReportViewProps {
  reportData: ReportData;
}

// Define specific type for health properties if available
interface HealthProperties {
    health: 'good' | 'average' | 'poor' | string; // Allow string for flexibility or unknown values
}

const ReportView: React.FC<ReportViewProps> = ({ reportData }) => {
  // Destructure needed data. Ensure selectedArea is also available in ReportData type.
  const { resultGeoJson, selectedArea, mapCenter = [50.45, 30.52], mapZoom = 13 } = reportData;

  // Style function for analysis result GeoJSON layers (colored zones)
  const styleResultFeature = (feature: Feature<Geometry, HealthProperties> | undefined) => {
    const properties = feature?.properties;
    const health = properties?.health || 'unknown';
    let color = 'gray'; 
    let fillOpacity = 0.5;
    switch (health) {
      case 'good': color = '#16a34a'; fillOpacity = 0.6; break;
      case 'average': color = '#ca8a04'; fillOpacity = 0.6; break;
      case 'poor': color = '#dc2626'; fillOpacity = 0.6; break;
    }
    return {
      fillColor: color,
      weight: 1, 
      opacity: 0.8, // Slightly less opaque border for results
      color: 'white', 
      fillOpacity: fillOpacity
    };
  };

  // Style function for the original selected area polygon
  const styleSelectedArea = () => ({
    fillColor: '#3b82f6', // Blue (Tailwind blue-500)
    fillOpacity: 0.1, // Very light fill
    color: '#2563eb', // Darker blue border (Tailwind blue-600)
    weight: 2, // Slightly thicker border
    opacity: 1, 
  });

  // Check if resultGeoJson has features
  const hasResultFeatures = resultGeoJson && resultGeoJson.features && resultGeoJson.features.length > 0;

  return (
      // Remove the outer div and redundant sections
      <div className="h-96 rounded-md overflow-hidden border border-gray-200"> {/* Keep height and border */} 
        {typeof window !== 'undefined' ? (
          <MapContainer center={mapCenter} zoom={mapZoom} scrollWheelZoom={true} style={{ height: '100%', width: '100%' }}>
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            
            {/* 1. Render the original selected area */}
            {selectedArea && (
               <GeoJSON
                  key={"selected-" + JSON.stringify(selectedArea.geometry)} // Key for selected area
                  data={selectedArea}
                  style={styleSelectedArea}
                />
            )}

            {/* 2. Render the analysis result GeoJSON (if available) */}
            {hasResultFeatures && resultGeoJson && (
               <GeoJSON
                  key={"result-" + JSON.stringify(resultGeoJson)} // Key for result area
                  data={resultGeoJson as FeatureCollection<Polygon|Point, HealthProperties>} 
                  style={styleResultFeature}
                />
            )}
          </MapContainer>
        ) : (
           <div className="h-full bg-gray-200 flex items-center justify-center">
              <p className="text-gray-500">Карта завантажується...</p> {/* Переведено */}
           </div>
        )}
      </div>
      // Removed the Summary section and the <p> for no data
  );
};

export default ReportView; 