import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '../tests/e2e',
  use: { baseURL: 'http://127.0.0.1:8000', trace: 'retain-on-failure' },
  workers: 1,
  // Tests use controlled local packages; no webServer command may build a corpus.
});
