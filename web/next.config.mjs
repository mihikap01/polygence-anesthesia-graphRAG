// Two build modes:
//   default              → server build with API routes (uses Claude CLI / OpenAI server-side)
//   NEXT_PUBLIC_BYOK=1   → static export for Firebase Hosting; the browser calls the
//                          user's own OpenAI/Anthropic key and runs retrieval locally.
//
// Local `npm run dev` is unaffected — keep BYOK unset.

const isBYOK = process.env.NEXT_PUBLIC_BYOK === "1";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  ...(isBYOK ? { output: "export", images: { unoptimized: true } } : {}),
  experimental: {
    serverComponentsExternalPackages: ["openai"],
  },
};
export default nextConfig;
