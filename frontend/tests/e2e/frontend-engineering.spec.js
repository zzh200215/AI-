import { test, expect } from '@playwright/test'

// P1/P2 前端工程化验收（全 mock，锁定前端行为，不依赖真实后端）：
// 1) Capability 门禁：非管理员看不到系统中心入口、直达 /system 显示 403 状态；管理员正常进入。
// 2) 离线状态：离线横幅出现，页面继续展示缓存内容。
// 3) 幂等写：上传按钮连点只发一次 POST 且携带 Idempotency-Key（useMutation 合并 + 幂等键）。

const success = (data) => ({ success: true, message: 'OK', data, error: null, request_id: 'req-e2e', trace_id: 'trace-e2e' })

const DOC = {
  id: 1, title: '测试法规.txt', file_type: 'txt', status: 'completed',
  classification: 'statute', sensitivity_level: 'internal', permission_scope: 'public',
  knowledge_base_id: null, version_number: 1, summary: '', created_at: '2026-01-01T00:00:00',
}

/**
 * 注册单一 /api/** 拦截器：公共 mock + 测试专属处理。
 * @param {import('@playwright/test').Page} page
 * @param {string} role
 * @param {(ctx: { route: any, path: string, method: string, request: any }) => Promise<void>|void} [extra]
 */
async function mockApi(page, role, extra) {
  await page.addInitScript(() => localStorage.setItem('token', 'e2e-token'))
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'GET' && path === '/auth/me') {
      return route.fulfill({ json: success({ id: 3, username: 'e2e-user', role, status: 'active' }) })
    }
    if (method === 'GET' && path === '/developer/notifications/me') {
      return route.fulfill({ json: success({ items: [], unread: 0 }) })
    }
    if (method === 'GET' && path === '/documents/knowledge-bases') {
      return route.fulfill({ json: success([]) })
    }
    if (method === 'GET' && path === '/documents/') {
      return route.fulfill({ json: success({ items: [DOC], total: 1, page: 1, page_size: 10, has_next: false, has_previous: false }) })
    }
    if (method === 'GET' && path === '/health') {
      return route.fulfill({ json: success({ status: 'ok', timestamp: '2026-01-01T00:00:00', checks: {} }) })
    }
    if (extra) {
      return extra({ route, path, method, request })
    }
    await route.continue()
  })
}

test('capability：非管理员直达 /system 显示 403 状态且导航无系统中心', async ({ page }) => {
  await mockApi(page, 'user')
  await page.goto('/system')
  await expect(page.getByText('无权访问')).toBeVisible()
  await expect(page.locator('.top-nav').getByText('系统中心')).toHaveCount(0)
  await expect(page.locator('.top-nav').getByText('Agent配置')).toHaveCount(0)
})

test('capability：管理员可进入系统中心', async ({ page }) => {
  await mockApi(page, 'admin')
  await page.goto('/system')
  await expect(page.getByRole('tab', { name: '健康检查' })).toBeVisible()
  await expect(page.locator('.top-nav').getByText('系统中心')).toHaveCount(1)
})

test('offline：断网时展示离线横幅并保留缓存内容', async ({ page, context }) => {
  await mockApi(page, 'user')
  await page.goto('/documents')
  await expect(page.getByRole('heading', { name: '法律知识库', level: 1 })).toBeVisible()
  await expect(page.locator('.doc-item', { hasText: '测试法规.txt' })).toBeVisible()
  await context.setOffline(true)
  await expect(page.getByText('网络不可用，已暂停刷新与同步。恢复网络后自动继续。')).toBeVisible()
  // 已加载的文档列表仍展示（缓存内容，不清空闪烁）
  await expect(page.locator('.doc-item', { hasText: '测试法规.txt' })).toBeVisible()
})

test('幂等写：上传连点只发一次 POST 且携带 Idempotency-Key', async ({ page }) => {
  const uploads = []
  await mockApi(page, 'user', async ({ route, path, method, request }) => {
    if (method === 'POST' && path === '/documents/upload') {
      uploads.push({
        idemKey: request.headers()['idempotency-key'] || '',
        filename: (request.postData() || '').match(/filename="([^"]+)"/)?.[1] || 'unknown',
      })
      return route.fulfill({ json: success({ documents: [DOC], count: 1 }) })
    }
    if (method === 'POST' && path === '/documents/1/analyze') {
      return route.fulfill({ json: success({ document_id: 1, summary: 'ok', risks: [], todos: [] }) })
    }
    if (method === 'GET' && path.startsWith('/documents/1/')) {
      return route.fulfill({ json: success({ items: [], total: 0 }) })
    }
    if (method === 'GET' && path.startsWith('/agent/runs')) {
      return route.fulfill({ json: success({ items: [], total: 0 }) })
    }
    await route.continue()
  })

  await page.goto('/documents')
  await page.locator('input[type="file"]').setInputFiles({
    name: '合同草稿.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('这是一份测试合同内容，用于验证幂等上传。', 'utf-8'),
  })
  await expect(page.getByText('已选 1 份')).toBeVisible()

  // 同步连点：即使按钮未及禁用，useMutation 合并 + 同一 Idempotency-Key 保证只发一次请求
  await page.evaluate(() => {
    const btn = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('上传并分析'))
    btn.click()
    btn.click()
  })

  await expect.poll(() => uploads.length).toBe(1)
  expect(uploads[0].idemKey.length).toBeGreaterThan(0)
  expect(uploads[0].filename).toBe('合同草稿.txt')
})
