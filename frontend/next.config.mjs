import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./i18n/request.ts');

const internalApiBase = process.env.FKV_INTERNAL_API_BASE || 'http://127.0.0.1:18180';
const distDir = process.env.NEXT_DIST_DIR || '.next-runtime';

const nextConfig = {
  reactStrictMode: true,
  distDir,
  httpAgentOptions: {
    timeout: 600_000,
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${internalApiBase}/:path*`
      }
    ];
  }
};

export default withNextIntl(nextConfig);
