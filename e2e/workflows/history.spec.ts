import { expect, test } from '@playwright/test'

import { login, submitPrompt, waitForJob } from './helpers'

test('a generation appears in history, opens, and can be retried', async ({ page }) => {
  await login(page)
  await submitPrompt(page, 'history entry')
  expect(await waitForJob(page)).toBe('succeeded')

  await page.getByRole('link', { name: 'History' }).click()
  await expect(page.getByTestId('history-grid')).toBeVisible()
  await expect(page.getByText('history entry')).toBeVisible()

  await page.getByLabel('Open generation: history entry').click()
  await expect(page.getByRole('dialog')).toBeVisible()

  await page.getByRole('button', { name: /Retry/ }).click()
  await expect(page.getByText(/Retried as/)).toBeVisible()
})

test('delete is soft: it disappears and Undo brings it back', async ({ page }) => {
  await login(page)
  await submitPrompt(page, 'undo me')
  expect(await waitForJob(page)).toBe('succeeded')

  await page.getByRole('link', { name: 'History' }).click()
  await page.getByLabel('Open generation: undo me').click()
  await page.getByRole('button', { name: /Delete/ }).click()

  await expect(page.getByText('Generation deleted')).toBeVisible()
  await expect(page.getByText('undo me')).toHaveCount(0)

  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByText('undo me')).toBeVisible({ timeout: 10_000 })
})
