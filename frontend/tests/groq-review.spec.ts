import { test, expect } from '@playwright/test';

test.afterAll(async ({ request }) => {
  await request.post('/__fixture/shutdown', { data: {} });
});

test('blind label, refresh, revisions, comparison, audit and transfer', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    return host === '127.0.0.1' ? route.continue() : route.abort();
  });
  await page.goto('/');
  await expect(page.locator('#title')).toContainText('Bài');
  await expect(page.locator('#after-save')).toBeHidden();
  await expect(page.locator('#progress-text')).toHaveText('0 / 90 đã review');
  await page.screenshot({ path: '../.tools/groq-review-ui/browser-blind.png', fullPage: true });
  await page.locator('input[name=membership][value=IN_TAXONOMY]').check();
  await page.locator('#primary').selectOption('Person');
  await page.locator('input[name=sentence][value="0"]').check();
  await page.locator('input[name=confidence][value=high]').check();
  await page.locator('#reviewer').fill('synthetic-browser-fixture');
  await page.locator('#notes').fill('Synthetic browser control, not real human gold.');
  // A draft survives a browser refresh before any semantic submission.
  page.on('dialog', dialog => dialog.accept());
  await page.reload();
  await expect(page.locator('#notes')).toHaveValue('Synthetic browser control, not real human gold.');
  await expect(page.locator('#progress-text')).toHaveText('0 / 90 đã review');
  await page.locator('#save').click();
  await expect(page.locator('#revision')).toContainText('Revision 1');
  await expect(page.locator('#progress-text')).toHaveText('1 / 90 đã review');
  await page.reload();
  await expect(page.locator('#primary')).toHaveValue('Person');
  await page.locator('#reveal').click();
  await expect(page.locator('#comparison')).toBeVisible();
  await expect(page.locator('#comparison-cards')).toContainText('GROQ');
  await page.locator('#verification').selectOption('NOT_VERIFIABLE_WITH_AVAILABLE_EVIDENCE');
  await page.locator('#audit-notes').fill('Synthetic fixture has no archived verification evidence.');
  await page.locator('#audit-form button[type=submit]').click();
  await expect(page.locator('#audit-status')).toContainText('Đã lưu audit revision 1');
  await page.locator('#notes').fill('Synthetic correction after comparison exposure.');
  await page.locator('#save').click();
  await expect(page.locator('#revision')).toContainText('Revision 2');
  await page.locator('#history-button').click();
  await expect(page.locator('#history-json')).toContainText('comparison_exposed');
  await page.locator('#next').click();
  await expect(page.locator('#after-save')).toBeHidden();
  await expect(page.locator('#primary')).toHaveValue('');
  await expect(page.locator('input[name=membership]:checked')).toHaveCount(0);
  await page.locator('#previous').click();
  await expect(page.locator('#revision')).toContainText('Revision 2');
  const exported = await page.request.get('/api/export?format=jsonl');
  const records = (await exported.text()).trim().split('\n').map(line => JSON.parse(line));
  expect(records.filter(row => row.review_status === 'reviewed')).toHaveLength(1);
  await page.locator('#evaluate').click();
  await expect(page.locator('#metric-summary')).toContainText('ASSESSED_REVIEWED_CASES_ONLY');
  await page.screenshot({ path: '../.tools/groq-review-ui/browser-reviewed.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('mobile layout keeps review readable without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('#title')).toContainText('Bài');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('save locks newer edits and late comparison cannot leak into next article', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#title')).toContainText('Bài');
  await page.locator('input[name=membership][value=IN_TAXONOMY]').check();
  await page.locator('#primary').selectOption('Person');
  await page.locator('input[name=sentence][value="0"]').check();
  await page.locator('input[name=confidence][value=high]').check();
  await page.locator('#reviewer').fill('synthetic-race-fixture');
  let releaseSave!: () => void;
  const saveGate = new Promise<void>(resolve => { releaseSave = resolve; });
  await page.route('**/semantic', async route => { await saveGate; await route.continue(); });
  await page.locator('#save').click();
  try {
    await expect(page.locator('#notes')).toBeDisabled();
    await expect(page.locator('#next')).toBeDisabled();
  } finally { releaseSave(); }
  await expect(page.locator('#revision')).toContainText('Revision 1');
  await expect(page.locator('#notes')).toBeEnabled();
  let releaseComparison!: () => void;
  const comparisonGate = new Promise<void>(resolve => { releaseComparison = resolve; });
  await page.route('**/comparison', async route => {
    const response = await route.fetch();
    await comparisonGate;
    await route.fulfill({ response });
  });
  await page.locator('#reveal').click();
  const oldTitle = await page.locator('#title').textContent();
  await page.locator('#next').click();
  await expect(page.locator('#title')).not.toHaveText(oldTitle!);
  releaseComparison();
  await page.waitForTimeout(200);
  await expect(page.locator('#comparison')).toBeHidden();
  await expect(page.locator('#after-save')).toBeHidden();
});
