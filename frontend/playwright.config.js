const {defineConfig} = require('@playwright/test');

const e2ePort = Number(process.env.FKV_E2E_PORT || 18081);
const defaultBaseUrl = `http://127.0.0.1:${e2ePort}`;

module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 90000,
  expect: {
    timeout: 15000
  },
  reporter: 'line',
  webServer: {
    command: `NEXT_PUBLIC_KB_API_MODE=mock NEXT_DIST_DIR=.next-e2e npx next dev -p ${e2ePort}`,
    url: defaultBaseUrl,
    reuseExistingServer: !process.env.CI,
    timeout: 120000
  },
  use: {
    baseURL: process.env.FKV_WEB_BASE || defaultBaseUrl,
    headless: true,
    trace: 'retain-on-failure'
  }
});
