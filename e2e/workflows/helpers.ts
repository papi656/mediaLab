import { expect, type Page } from '@playwright/test'

export const PASSWORD = 'medialab'

/** 16x16 PNG, generated once so tests never touch the network or the filesystem. */
export const PNG_1PX = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAJUlEQVR42mP8z8BQz0AEYBxVSF+FjIOh' +
    'AEYDYBxVSF+FjIOhAEYDYBxVSF+FAP0kCQWJz2nLAAAAAElFTkSuQmCC',
  'base64',
)

export async function login(page: Page) {
  await page.goto('/')
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByLabel('Prompt')).toBeVisible()
}

export async function pickModel(page: Page, name: RegExp) {
  await page.locator('#model').click()
  await page.getByRole('option', { name }).click()
}

export async function submitPrompt(page: Page, prompt: string) {
  await page.getByLabel('Prompt').fill(prompt)
  await page.getByRole('button', { name: /^Generate$/ }).click()
}

/** Waits for the job to leave queued/running and returns the final status. */
export async function waitForJob(page: Page): Promise<string> {
  const status = page.getByTestId('job-status')
  await expect(status).toBeVisible()
  await expect
    .poll(async () => (await status.textContent())?.trim(), { timeout: 30_000 })
    .not.toBe('queued')
  await expect
    .poll(async () => (await status.textContent())?.trim(), { timeout: 30_000 })
    .not.toBe('running')
  return (await status.textContent())?.trim() ?? ''
}

export async function deleteGeneration(page: Page, prompt: string) {
  await page.goto('/history')
  await page.getByLabel(`Open generation: ${prompt}`).click()
  await page.getByRole('button', { name: /Delete/ }).click()
}
