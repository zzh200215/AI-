import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const baseUrl = process.env.README_SCREENSHOT_BASE_URL || 'http://127.0.0.1:5173'
const scriptDir = resolve(fileURLToPath(new URL('.', import.meta.url)))
const outputDir = resolve(scriptDir, '../../docs/images')
const success = (data) => ({ success: true, data })

const previewUser = {
  id: 15,
  username: 'legal-ops',
  email: 'legal-ops@example.com',
  role: 'admin',
  organization_id: 5,
  status: 'active',
}

async function mockHomeApi(page) {
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) return route.continue()

    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(previewUser) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/developer/onboarding') {
      return route.fulfill({ json: success({ user_role: 'enterprise_legal', completed_steps_json: '["创建合同台账"]' }) })
    }
    return route.fulfill({ json: success({}) })
  })
}

await mkdir(outputDir, { recursive: true })

const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 })

try {
  const loginPage = await context.newPage()
  await loginPage.goto(`${baseUrl}/login`, { waitUntil: 'networkidle' })
  await loginPage.screenshot({ path: resolve(outputDir, 'login.png'), fullPage: true })

  const homePage = await context.newPage()
  await homePage.addInitScript(() => localStorage.setItem('token', 'readme-preview-token'))
  await mockHomeApi(homePage)
  await homePage.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' })
  await homePage.locator('.onboarding').waitFor({ state: 'visible', timeout: 10_000 })
  await homePage.screenshot({ path: resolve(outputDir, 'home-onboarding.png'), fullPage: true })
} finally {
  await browser.close()
}
