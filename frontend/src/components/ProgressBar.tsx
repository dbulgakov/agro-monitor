import React from 'react';

interface ProgressBarProps {
  progress: number; // 0 to 100
}

const ProgressBar: React.FC<ProgressBarProps> = ({ progress }) => {
  const cappedProgress = Math.min(Math.max(progress, 0), 100);

  return (
    <div className="w-full bg-gray-200 rounded-full h-4 mb-4 dark:bg-gray-700 overflow-hidden">
      <div
        className="bg-blue-600 h-4 rounded-full transition-all duration-300 ease-linear dark:bg-blue-500"
        style={{ width: `${cappedProgress}%` }}
      ></div>
    </div>
  );
};

export default ProgressBar; 