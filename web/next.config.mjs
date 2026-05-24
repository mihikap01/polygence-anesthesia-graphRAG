/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    // serve large json files (graph.json ~ a few MB) without bundler bloat
    serverComponentsExternalPackages: ["openai"],
  },
};
export default nextConfig;
