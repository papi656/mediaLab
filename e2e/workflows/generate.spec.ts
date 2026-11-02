import { expect, test } from '@playwright/test'

import { login, pickModel, submitPrompt, waitForJob } from './helpers'

test('text to image: submit, poll, render, download', async ({ page }) => {
  await login(page)
  await submitPrompt(page, 'a red panda astronaut')

  const status = await waitForJob(page)
  expect(status).toBe('succeeded')

  const image = page.getByRole('img', { name: 'a red panda astronaut' })
  await expect(image).toBeVisible()
  await expect(image).toHaveAttribute('src', /\/api\/assets\//)

  const download = page.getByRole('link', { name: /Download/ })
  await expect(download).toHaveAttribute('href', /\/download$/)

  // The fake provider reports a deterministic cost, so the panel should show it.
  await expect(page.getByTestId('result-panel')).toContainText('$0.0100')
})

test('capability-driven form: unsupported controls are absent, not disabled', async ({ page }) => {
  await login(page)

  // Default model accepts up to 14 references and is single-image.
  await expect(page.getByText('Reference images', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Images')).toHaveCount(0)

  // A text-only model removes the reference picker entirely.
  await pickModel(page, /Recraft V4\.1 Flash/i)
  await expect(page.getByText('Reference images', { exact: true })).toHaveCount(0)
  await expect(page.getByText('text only')).toBeVisible()

  // A vector model exposes an SVG output format.
  await pickModel(page, /Recraft V4 Vector/i)
  await expect(page.locator('#output_format')).toBeVisible()
})

test('svg model produces an svg asset', async ({ page }) => {
  await login(page)
  await pickModel(page, /Recraft V4 Vector/i)

  await page.locator('#output_format').click()
  await page.getByRole('option', { name: 'svg' }).click()
  await submitPrompt(page, 'minimal geometric logo')

  expect(await waitForJob(page)).toBe('succeeded')
  const image = page.getByRole('img', { name: 'minimal geometric logo' })
  await expect(image).toBeVisible()
})

test('provider failure is surfaced with a retry', async ({ page }) => {
  await login(page)
  await submitPrompt(page, '[fail] this will break')

  expect(await waitForJob(page)).toBe('failed')
  await expect(page.getByTestId('result-panel')).toContainText('Fake provider failure')
  await expect(page.getByRole('button', { name: /Retry/ })).toBeVisible()
})
