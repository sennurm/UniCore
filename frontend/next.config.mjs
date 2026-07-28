/** @type {import('next').NextConfig} */
const nextConfig = {
  // Lets `make build-web` build into .next-build so a production build can
  // never clobber the running dev server's .next directory again.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};
export default nextConfig;
