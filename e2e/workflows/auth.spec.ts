import { expect, test } from '@playwright/test'

import { login } from './helpers'

test('unauthenticated visit lands on the login screen', async ({ page }) => {
  await page.goto('/history')
  await expect(page.getByLabel('Password')).toBeVisible()
  await expect(page.getByLabel('Prompt')).toHaveCount(0)
})

test('the API is not reachable without a session', async ({ page, request }) => {
  const response = await request.get('/api/models', { maxRedirects: 0 })
  expect(response.status()).toBe(401)
})

test('wrong password shows an error and keeps you out', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Password').fill('definitely-not-it')
  await page.getByRole('button', { name: 'Sign in' }).click()

  await expect(page.getByText('invalid password')).toBeVisible()
  await expect(page.getByLabel('Prompt')).toHaveCount(0)
})

test('correct password opens the app', async ({ page }) => {
  await login(page)
  await expect(page.getByText('Generate').first()).toBeVisible()
})

test('logout returns to login and the app does not come back', async ({ page }) => {
  await login(page)
  await page.getByRole('button', { name: 'Log out' }).click()
  await expect(page.getByLabel('Password')).toBeVisible()

  await page.goto('/history')
  await expect(page.getByLabel('Password')).toBeVisible()
  await expect(page.getByTestId('history-grid')).toHaveCount(0)
})
