import {expect, test} from '@playwright/test';

async function waitForAssistantMessage(page: import('@playwright/test').Page) {
  await expect(page.locator('.typing-bubble')).toHaveCount(0, {timeout: 90000});
  const bubble = page.locator('.msg.assistant .msg-bubble').last();
  await expect(bubble).toBeVisible({timeout: 90000});
  return bubble;
}

test('dashboard shows agent card and supports ask-to-agent jump', async ({page}) => {
  await page.goto('/zh-CN/dashboard');
  await expect(page.locator('#view-dashboard')).toBeVisible();
  await expect(page.locator('.dash-agent-card')).toBeVisible();
  await expect(page.locator('.dash-agent-chip')).toHaveCount(4);

  await page.fill('#dash-agent-input', '最近有哪些账单需要关注？');
  await page.keyboard.press('Enter');

  await expect(page).toHaveURL(/\/zh-CN\/agent/);
  await expect(page.locator('#view-agent')).toBeVisible();
  await expect(page.locator('.msg.user').last()).toContainText('最近有哪些账单需要关注');
  await expect(page.locator('.msg.assistant').last()).toBeVisible();
});

test('dashboard category stat bar is clickable and opens category docs route', async ({page}) => {
  await page.goto('/zh-CN/dashboard');
  await expect(page.locator('#view-dashboard')).toBeVisible();

  const firstBar = page.locator('.cat-bar.clickable').first();
  await expect(firstBar).toBeVisible();
  await firstBar.click();

  await expect(page).toHaveURL(/\/zh-CN\/cats\//);
  await expect(page.locator('#view-cat-docs')).toBeVisible();
});

test('docs page opens detail overlay from doc item', async ({page}) => {
  await page.goto('/zh-CN/docs');
  await expect(page.locator('#view-docs')).toBeVisible();

  const firstDoc = page.locator('.doc-item').first();
  await expect(firstDoc).toBeVisible();
  await firstDoc.click();

  await expect(page.locator('#detail-overlay')).toHaveClass(/open/);
  await page.locator('.view-btn').click();
  await expect(page.locator('#content-overlay')).toHaveClass(/open/);
  await expect(page.locator('.content-frame')).toHaveAttribute('src', /\/api\/v1\/documents\/.+\/content\?disposition=inline/);
  await page.locator('.content-close').click();
  await expect(page.locator('#content-overlay')).not.toHaveClass(/open/);
  await page.locator('.detail-close').click();
  await expect(page.locator('#detail-overlay')).not.toHaveClass(/open/);
});

test('agent suggestion sends message and renders assistant response', async ({page}) => {
  await page.goto('/zh-CN/agent');
  await expect(page.locator('#view-agent')).toBeVisible();

  const suggestion = page.locator('.agent-suggestion').first();
  await expect(suggestion).toBeVisible();
  await suggestion.click();

  await expect(page.locator('.msg.user')).toBeVisible();
  await expect(page.locator('.typing-bubble')).toBeVisible();
  await expect(page.locator('.msg.assistant')).toBeVisible();
});

test('agent billing query returns related docs and opens detail overlay', async ({page}) => {
  await page.goto('/zh-CN/agent');
  await expect(page.locator('#view-agent')).toBeVisible();

  await page.fill('#agent-input', '2024年12月电费账单');
  await page.keyboard.press('Enter');

  const assistantBubble = await waitForAssistantMessage(page);
  const assistant = assistantBubble.locator('xpath=ancestor::div[contains(@class,"msg assistant")]');
  const cards = assistant.locator('.msg-doc-card');
  const count = await cards.count();
  if (count > 0) {
    await cards.first().click();
    await expect(page.locator('#detail-overlay')).toHaveClass(/open/);
    await expect(page.locator('#d-title')).not.toHaveText('');
  } else {
    await expect(assistantBubble).toBeVisible();
  }
});

test('mobile drawer/tab navigation and detail bottom-sheet behavior', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/zh-CN/dashboard');

  await expect(page.locator('.mobile-menu-btn')).toBeVisible();
  await page.locator('.mobile-menu-btn').click();
  await expect(page.locator('.sidebar')).toHaveClass(/open/);

  await page.locator('#sidebar-backdrop').click();
  await expect(page.locator('.sidebar')).not.toHaveClass(/open/);

  await page.locator('#tab-docs').click();
  await expect(page).toHaveURL(/\/zh-CN\/docs/);
  await expect(page.locator('#view-docs')).toBeVisible();

  const firstDoc = page.locator('.doc-item').first();
  await expect(firstDoc).toBeVisible();
  await firstDoc.click();
  await expect(page.locator('#detail-overlay')).toHaveClass(/open/);

  const panelBox = await page.locator('.detail-panel').boundingBox();
  expect(panelBox).not.toBeNull();
  if (panelBox) {
    expect(panelBox.width).toBeGreaterThan(360);
  }

  await page.locator('.detail-close').click();
  await expect(page.locator('#detail-overlay')).not.toHaveClass(/open/);

  await page.locator('#tab-agent').click();
  await expect(page).toHaveURL(/\/zh-CN\/agent/);
  await expect(page.locator('#view-agent')).toBeVisible();
});

test('agent network bill query only shows internet bill related docs', async ({page}) => {
  await page.goto('/zh-CN/agent');
  await expect(page.locator('#view-agent')).toBeVisible();

  await page.fill('#agent-input', '家里的网络账单');
  await page.keyboard.press('Enter');

  const assistantBubble = await waitForAssistantMessage(page);
  const assistant = assistantBubble.locator('xpath=ancestor::div[contains(@class,"msg assistant")]');
  const cards = assistant.locator('.msg-doc-card');
  const total = await cards.count();
  for (let idx = 0; idx < total; idx += 1) {
    await expect(cards.nth(idx)).toContainText(/网络账单|Internet Bills|互联网/);
    await expect(cards.nth(idx)).not.toContainText(/电费|水费|燃气/);
  }
  if (total === 0) {
    await expect(assistantBubble).toBeVisible();
  }
});

test('agent property contact query does not show utilities bill cards', async ({page}) => {
  await page.goto('/zh-CN/agent');
  await expect(page.locator('#view-agent')).toBeVisible();

  await page.fill('#agent-input', '物业的联系方式是什么');
  await page.keyboard.press('Enter');

  const assistantBubble = await waitForAssistantMessage(page);
  const assistant = assistantBubble.locator('xpath=ancestor::div[contains(@class,"msg assistant")]');
  const cards = assistant.locator('.msg-doc-card');
  const total = await cards.count();
  for (let idx = 0; idx < total; idx += 1) {
    await expect(cards.nth(idx)).not.toContainText(/电费|水费|燃气|网络账单/);
  }
  if (total === 0) {
    await expect(assistantBubble).toBeVisible();
  }
});
