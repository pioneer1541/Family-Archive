import {expect, test} from '@playwright/test';

const ROUTES = [
  {path: '/zh-CN/dashboard', rootSelector: '#view-dashboard', titlePrefix: '总览'},
  {path: '/zh-CN/docs', rootSelector: '#view-docs', titlePrefix: '文档'},
  {path: '/zh-CN/cats', rootSelector: '#view-cats', titlePrefix: '分类'},
  {path: '/zh-CN/agent', rootSelector: '#view-agent', titlePrefix: 'Agent'},
  {path: '/zh-CN/cats/finance__bills__water', rootSelector: '#view-cat-docs'}
];

test('navigation smoke: key routes render without 500', async ({page}) => {
  for (const item of ROUTES) {
    const response = await page.goto(item.path);
    expect(response?.status(), `route ${item.path} should not return 5xx`).toBeLessThan(500);
    await expect(page.locator(item.rootSelector), `missing root ${item.rootSelector} for ${item.path}`).toBeVisible();
    if (item.titlePrefix) {
      await expect(page).toHaveTitle(new RegExp(`^${item.titlePrefix}\\s\\|\\sFamily Knowledge Vault$`));
    }
  }
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute('href', '/manifest.webmanifest');
});
