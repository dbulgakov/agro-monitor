'use client';

import React, { useState, useEffect, useRef, forwardRef, useImperativeHandle } from 'react';
import dynamic from 'next/dynamic';
import L, { LatLngExpression } from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet-draw/dist/leaflet.draw.css';
import { Feature, Polygon, Point } from 'geojson';

// Dynamically import Leaflet components
const MapContainer = dynamic(() => import('react-leaflet').then(mod => mod.MapContainer), { ssr: false });
const TileLayer = dynamic(() => import('react-leaflet').then(mod => mod.TileLayer), { ssr: false });
const FeatureGroup = dynamic(() => import('react-leaflet').then(mod => mod.FeatureGroup), { ssr: false });

// Import EditControl specific for client-side
const EditControl = dynamic(() => import('react-leaflet-draw').then(mod => mod.EditControl), { ssr: false });

// Fix default icon issue
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

// Define types for props and ref handle
interface MapSelectorProps {
  onAreaSelect: (area: Feature<Polygon | Point> | null) => void;
}

export interface MapSelectorRef {
  clearLayers: () => void;
}

const DEFAULT_CENTER: LatLngExpression = [50.4501, 30.5234]; // Kyiv
const DEFAULT_ZOOM = 10;

// Wrap component with forwardRef
const MapSelector = forwardRef<MapSelectorRef, MapSelectorProps>(({ onAreaSelect }, ref) => {
  const [mapCenter, setMapCenter] = useState<LatLngExpression>(DEFAULT_CENTER);
  const [currentZoom, setCurrentZoom] = useState<number>(DEFAULT_ZOOM);
  const [selectedLayer, setSelectedLayer] = useState<L.Layer | null>(null);
  const featureGroupRef = useRef<L.FeatureGroup>(null);
  const [mapReady, setMapReady] = useState(false);
  const mapRef = useRef<L.Map | null>(null); // Ref to store map instance

  // Expose clearLayers method using useImperativeHandle
  useImperativeHandle(ref, () => ({
    clearLayers: () => {
      featureGroupRef.current?.clearLayers();
      setSelectedLayer(null);
      onAreaSelect(null); // Also notify parent that selection is cleared
      console.log('[MapSelector] Layers cleared via ref');
    }
  }));

  // Request geolocation on mount
  useEffect(() => {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude } = position.coords;
        console.log('Geolocation success:', [latitude, longitude]);
        const newCenter: LatLngExpression = [latitude, longitude];
        setMapCenter(newCenter);
        setCurrentZoom(13);
        mapRef.current?.flyTo(newCenter, 13);
      },
      (error) => {
        console.warn(`Geolocation error (${error.code}): ${error.message}. Using default location.`);
      },
      {
        enableHighAccuracy: false,
        timeout: 5000,
        maximumAge: 0,
      }
    );
  }, []);

  // --- Handlers for Leaflet Draw --- //
  const handleCreated = (e: any) => {
    const layer = e.layer;
    // Clear previous layer if any
    featureGroupRef.current?.clearLayers();
    featureGroupRef.current?.addLayer(layer);
    setSelectedLayer(layer);
    const geoJson = layer.toGeoJSON() as Feature<Polygon | Point>; // Get GeoJSON
    console.log("Layer created (GeoJSON):", geoJson);
    onAreaSelect(geoJson); // Pass GeoJSON to parent
  };

  const handleEdited = (e: any) => {
    e.layers.eachLayer((layer: any) => {
      setSelectedLayer(layer);
      const geoJson = layer.toGeoJSON() as Feature<Polygon | Point>;
      console.log("Layer edited (GeoJSON):", geoJson);
      onAreaSelect(geoJson);
    });
  };

  const handleDeleted = (e: any) => {
    // Check if the deleted layer was the selected one
    let wasSelectedDeleted = false;
    if (selectedLayer) {
        e.layers.eachLayer((layer: any) => {
            if (layer === selectedLayer) {
                wasSelectedDeleted = true;
            }
        });
    }
    if (wasSelectedDeleted) {
        console.log("Selected layer deleted");
        setSelectedLayer(null);
        onAreaSelect(null);
    }
  };
  
  const handleMapReady = (mapInstance: L.Map) => {
      mapRef.current = mapInstance;
      setMapReady(true);
      console.log('Map instance ready:', mapInstance);
      // Add delay to ensure draw control is ready if needed
      // setTimeout(() => console.log('Draw control should be ready'), 100);
  };

  // --- Render --- //
  return (
    <div className="h-full w-full relative"> {/* Ensure relative positioning for absolute child */}
      {typeof window !== 'undefined' ? (
        <MapContainer
          ref={mapRef} // Add ref to MapContainer
          center={mapCenter}
          zoom={currentZoom}
          scrollWheelZoom={true}
          style={{ height: '100%', width: '100%' }}
          whenReady={() => handleMapReady(mapRef.current!)} // Get map instance from ref
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <FeatureGroup ref={featureGroupRef}>
            {mapReady && (
                <EditControl
                    position="topright"
                    onCreated={handleCreated}
                    draw={{
                        rectangle: false,
                        polygon: { 
                            allowIntersection: false, 
                            drawError: { color: '#ef4444', message: 'Самоперетин заборонено!' }, 
                            shapeOptions: { color: '#3b82f6' } 
                        },
                        circle: false,
                        circlemarker: false,
                        marker: false,
                        polyline: false,
                    }}
                />
            )}
          </FeatureGroup>

          {/* Prompt message when no area is selected */}  
          {!selectedLayer && (
            <div 
              className="absolute bottom-2 left-1/2 transform -translate-x-1/2 z-[1000] p-1.5 px-3 bg-black bg-opacity-60 text-white text-xs rounded shadow-md pointer-events-none"
            >
              Намалюйте полігон або поставте точку на мапі для вибору області аналізу
            </div>
          )}

        </MapContainer>
      ) : (
        <div className="h-full w-full bg-gray-200 flex items-center justify-center">
          <p>Загрузка карты...</p>
        </div>
      )}
    </div>
  );
});

MapSelector.displayName = 'MapSelector'; // Add display name for DevTools

export default MapSelector; 