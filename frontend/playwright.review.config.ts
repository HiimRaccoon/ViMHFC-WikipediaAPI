import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const portable = resolve('../.tools/python312/python.exe');
const python = process.env.VIMFC_REVIEW_PYTHON || (existsSync(portable) ? portable : 'python');

export default defineConfig({
  testDir: './tests',
  testMatch: 'groq-review.spec.ts',
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:8766', channel: 'chrome', trace: 'retain-on-failure' },
  webServer: {
    command: `"${python}" -B ../tests/e2e/review_fixture.py --port 8766`,
    url: 'http://127.0.0.1:8766/api/session',
    reuseExistingServer: false,
    timeout: 60000,
  },
});
