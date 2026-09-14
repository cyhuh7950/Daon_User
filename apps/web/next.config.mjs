const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  experimental: { authInterrupts: true },
  transpilePackages: ["@daon-user/ui", "@daon-user/design-tokens", "@daon-user/contracts"]
};

export default nextConfig;
