import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Disable ESLint errors during production builds. The linter will still run in development
  eslint: {
    ignoreDuringBuilds: true,
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: process.env.NEXT_PUBLIC_API_URL + '/:path*',
      },
    ];
  },
};

export default nextConfig;
