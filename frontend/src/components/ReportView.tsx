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
const ImageOverlay = dynamic(() => import('react-leaflet').then(mod => mod.ImageOverlay), { ssr: false });
const LayersControl = dynamic(() => import('react-leaflet').then(mod => mod.LayersControl), { ssr: false });
const LayerGroup = dynamic(() => import('react-leaflet').then(mod => mod.LayerGroup), { ssr: false });

// Add dynamic imports for BaseLayer and Overlay
const BaseLayer = dynamic(() => import('react-leaflet').then(mod => mod.LayersControl.BaseLayer), { ssr: false });
const Overlay = dynamic(() => import('react-leaflet').then(mod => mod.LayersControl.Overlay), { ssr: false });

// Define props using the imported ReportData type
interface ReportViewProps {
  reportData: ReportData;
}

// Define specific type for health properties if available
interface HealthProperties {
    health: 'good' | 'average' | 'poor' | string; // Allow string for flexibility or unknown values
}

const ReportView: React.FC<ReportViewProps> = ({ reportData }) => {
  // Destructure needed data. Use updated fields.
  const { 
    selectedArea, 
    mapCenter = [50.45, 30.52], 
    mapZoom = 13, 
    ndviStatistics, 
    mapUrls, 
    recommendations, 
    imageBounds 
  } = reportData;

  // NOTE: The component currently uses 'resultGeoJson' which was removed from the updated ReportData type.
  // We'll comment it out for now. The logic for displaying colored zones needs to be adapted 
  // potentially using the ndviStatistics or the new stress map if required.
  // const { resultGeoJson } = reportData; 

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

  // Check if resultGeoJson has features - This logic needs update
  // const hasResultFeatures = resultGeoJson && resultGeoJson.features && resultGeoJson.features.length > 0;
  const hasResultFeatures = false; // Placeholder - update required

  // TODO: Add logic to potentially display the stress map from mapUrls.stress

  return (
      <div className="h-96 rounded-md overflow-hidden border border-gray-200"> 
        {/* Remove the outer div and redundant sections */} {/* Keep height and border */} 
        {typeof window !== 'undefined' ? (
          <MapContainer center={mapCenter} zoom={mapZoom} scrollWheelZoom={true} style={{ height: '100%', width: '100%' }}>
             <LayersControl position="topright">
                <BaseLayer checked name="OpenStreetMap">
                  <TileLayer
                    attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                   />
                </BaseLayer>
                <BaseLayer name="Satellite">
                   <TileLayer 
                      url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}" 
                      attribution='Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community'
                   />
                </BaseLayer>

                <Overlay checked name="Selected Area">
                  <LayerGroup>
                    {selectedArea && (
                     <GeoJSON
                       key={"selected-" + JSON.stringify(selectedArea.geometry)} // Key for selected area
                       data={selectedArea}
                       style={styleSelectedArea}
                     />
                   )}
                  </LayerGroup>
                </Overlay>

                {mapUrls?.ndvi && imageBounds && (
                  <Overlay name="NDVI Map">
                    <ImageOverlay url={mapUrls.ndvi} bounds={imageBounds} opacity={0.7} zIndex={10} />
                  </Overlay>
                )}

                {mapUrls?.rgb && imageBounds && (
                  <Overlay name="RGB Map">
                    <ImageOverlay url={mapUrls.rgb} bounds={imageBounds} opacity={1} zIndex={10} />
                  </Overlay>
                )}

                {mapUrls?.stress && imageBounds && (
                  <Overlay checked name="Stress Zone Map">
                    <ImageOverlay url={mapUrls.stress} bounds={imageBounds} opacity={0.6} zIndex={10} />
                  </Overlay>
                )}
               
               {/* TODO: Optionally add back the resultGeoJson logic if needed and available */}

             </LayersControl>
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