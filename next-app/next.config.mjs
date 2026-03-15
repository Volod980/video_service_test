/** @type {import('next').NextConfig} */
const nextConfig = {
  // "standalone" is only used for Docker builds (set NEXT_STANDALONE=true in Dockerfile).
  // Vercel has its own output system and does NOT support standalone mode.
  ...(process.env.NEXT_STANDALONE === "true" ? { output: "standalone" } : {}),
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
    ],
  },
};

export default nextConfig;
