import type { components, paths } from './api-types'

export type Job = components['schemas']['JobOut']
export type Asset = components['schemas']['AssetOut']
export type ModelCaps = components['schemas']['ModelOut']
export type ModelsResponse = components['schemas']['ModelsOut']
export type GenerationInput = components['schemas']['GenerationIn']
export type Usage = components['schemas']['UsageOut']
export type JobList = components['schemas']['JobListOut']

export const TERMINAL = ['succeeded', 'failed', 'interrupted', 'cancelled'] as const
export type JobStatus = Job['status']

export function isTerminal(status: string | undefined): boolean {
  return !!status && (TERMINAL as readonly string[]).includes(status)
}

export class ApiError extends Error {
  status: number
  payload: unknown

  constructor(status: number, payload: unknown) {
    const detail = extractMessage(payload)
    super(detail)
    this.status = status
    this.payload = payload
  }
}

function extractMessage(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return 'Request failed'
  const body = payload as Record<string, unknown>
  const detail = body.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    const nested = detail as Record<string, unknown>
    const errors = nested.errors
    if (Array.isArray(errors) && errors.length > 0) return errors.join('; ')
    if (typeof nested.detail === 'string') return nested.detail
  }
  if (Array.isArray(body.detail)) {
    return body.detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? String(d.msg) : String(d)))
      .join('; ')
  }
  return 'Request failed'
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isForm = init.body instanceof FormData
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...init,
    headers: {
      // The backend rejects cookie-authenticated mutations without this header.
      'X-Requested-With': 'fetch',
      ...(isForm ? {} : { 'Content-Type': 'application/json' }),
      ...(init.headers ?? {}),
    },
  })

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const payload: unknown = text ? JSON.parse(text) : null
  if (!response.ok) throw new ApiError(response.status, payload)
  return payload as T
}

export const api = {
  me: () => request<{ authenticated: boolean }>('/api/auth/me'),

  login: (password: string) =>
    request<void>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),

  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),

  models: () => request<ModelsResponse>('/api/models'),

  createGeneration: (input: GenerationInput) =>
    request<Job>('/api/generations', { method: 'POST', body: JSON.stringify(input) }),

  getJob: (id: string) => request<Job>(`/api/generations/${id}`),

  listJobs: (params: { limit?: number; offset?: number; model?: string } = {}) => {
    const search = new URLSearchParams()
    if (params.limit) search.set('limit', String(params.limit))
    if (params.offset) search.set('offset', String(params.offset))
    if (params.model) search.set('model', params.model)
    const qs = search.toString()
    return request<JobList>(`/api/generations${qs ? `?${qs}` : ''}`)
  },

  deleteJob: (id: string) => request<void>(`/api/generations/${id}`, { method: 'DELETE' }),

  restoreJob: (id: string) =>
    request<Job>(`/api/generations/${id}/restore`, { method: 'POST' }),

  retryJob: (id: string) =>
    request<Job>(`/api/generations/${id}/retry`, { method: 'POST' }),

  usage: (period: 'today' | 'month' = 'today') =>
    request<Usage>(`/api/usage?period=${period}`),

  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Asset>('/api/uploads', { method: 'POST', body: form })
  },
}

export type ApiPaths = paths
