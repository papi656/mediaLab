import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, isTerminal, type GenerationInput, type Job } from './api'

export const queryKeys = {
  me: ['me'] as const,
  models: ['models'] as const,
  jobs: (model?: string) => ['jobs', model ?? 'all'] as const,
  job: (id: string) => ['job', id] as const,
  usage: ['usage'] as const,
}

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: api.me,
    staleTime: 30_000,
    retry: false,
  })
}

export function useModels() {
  return useQuery({
    queryKey: queryKeys.models,
    queryFn: api.models,
    // The catalogue cannot change without a server restart.
    staleTime: Infinity,
  })
}

export function useJob(id: string | null) {
  return useQuery({
    queryKey: queryKeys.job(id ?? 'none'),
    queryFn: () => api.getJob(id as string),
    enabled: Boolean(id),
    refetchInterval: (query) => (isTerminal(query.state.data?.status) ? false : 900),
  })
}

export function useJobs(model?: string) {
  return useQuery({
    queryKey: queryKeys.jobs(model),
    queryFn: () => api.listJobs({ model, limit: 60 }),
  })
}

export function useUsage() {
  return useQuery({ queryKey: queryKeys.usage, queryFn: () => api.usage('today') })
}

export function useLogin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.login,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.me }),
  })
}

export function useLogout() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.logout,
    onSuccess: () => {
      client.clear()
      client.setQueryData(queryKeys.me, { authenticated: false })
    },
  })
}

export function useCreateGeneration() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: GenerationInput) => api.createGeneration(input),
    onSuccess: (job: Job) => {
      client.setQueryData(queryKeys.job(job.id), job)
      void client.invalidateQueries({ queryKey: ['jobs'] })
      void client.invalidateQueries({ queryKey: queryKeys.usage })
    },
  })
}

export function useDeleteJob() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deleteJob,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['jobs'] })
      void client.invalidateQueries({ queryKey: queryKeys.usage })
    },
  })
}

export function useRestoreJob() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.restoreJob,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['jobs'] })
      void client.invalidateQueries({ queryKey: queryKeys.usage })
    },
  })
}

export function useRetryJob() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.retryJob,
    onSuccess: (job: Job) => {
      client.setQueryData(queryKeys.job(job.id), job)
      void client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

export function useUploadReference() {
  return useMutation({ mutationFn: api.upload })
}
