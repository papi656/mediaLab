import { expect, test } from '@playwright/test'

import { login, pickModel, PNG_1PX, submitPrompt, waitForJob } from './helpers'

test('upload a reference and generate with it', async ({ page }) => {
  await login(page)

  await expect(page.getByText('Reference images', { exact: true })).toBeVisible()
  await page.locator('input[type=file]').setInputFiles({
    name: 'ref.png',
    mimeType: 'image/png',
    buffer: PNG_1PX,
  })

  await expect(page.getByText('1 / 14')).toBeVisible()
  await expect(page.getByRole('img', { name: 'reference 1' })).toBeVisible()

  await submitPrompt(page, 'match this reference')
  expect(await waitForJob(page)).toBe('succeeded')

  // The output can be fed back in: "Use as reference" appends it.
  await page.getByRole('button', { name: 'Use as reference' }).click()
  await expect(page.getByText('2 / 14')).toBeVisible()

  // And it can be removed again.
  await page.getByLabel('Remove reference 1').click()
  await expect(page.getByText('1 / 14')).toBeVisible()
})

test('references are capped by the selected model', async ({ page }) => {
  await login(page)

  await page.locator('input[type=file]').setInputFiles({
    name: 'ref.png',
    mimeType: 'image/png',
    buffer: PNG_1PX,
  })
  await expect(page.getByText('1 / 14')).toBeVisible()

  // Switching to a model that accepts no references drops them.
  await pickModel(page, /Recraft V4\.1 Flash/i)
  await expect(page.getByText('Reference images', { exact: true })).toHaveCount(0)
  await expect(page.getByText('/ 14')).toHaveCount(0)
})
