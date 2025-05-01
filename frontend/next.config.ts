import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  // Disable ESLint errors during production builds. The linter will still run in development
  eslint: {
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
