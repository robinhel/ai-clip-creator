import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Det finns en lockfil till högre upp (i hemmappen); utan root gissar Next.js fel arbetsmapp.
  turbopack: { root: path.join(__dirname) },
  experimental: {
    mcpServer: false,
  },
};

export default nextConfig;
