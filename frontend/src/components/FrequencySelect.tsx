'use client';
import React from 'react';

interface FrequencySelectProps {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}

const FrequencySelect: React.FC<FrequencySelectProps> = ({ value, onChange, options }) => {
  return (
    <fieldset className="mt-2">
      <legend className="sr-only">Частота анализа</legend>
      <div className="space-y-2">
        {options.map((option) => (
          <div key={option.value} className="flex items-center">
            <input
              id={`frequency-${option.value}`}
              name="frequency"
              type="radio"
              value={option.value}
              checked={value === option.value}
              onChange={(e) => onChange(e.target.value)}
              className="focus:ring-indigo-500 h-4 w-4 text-indigo-600 border-gray-300"
            />
            <label htmlFor={`frequency-${option.value}`} className="ml-3 block text-sm font-medium text-gray-700">
              {option.label}
            </label>
          </div>
        ))}
      </div>
    </fieldset>
  );
};

export default FrequencySelect; 