import { useState } from 'react'
import { Link } from 'react-router-dom'
import { RefreshCw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import type { Job } from '@/lib/api'
import { useDeleteJob, useJobs, useRestoreJob, useRetryJob } from '@/lib/hooks'

const UNDO_MS = 5000

export default function HistoryPage() {
  const jobs = useJobs()
  const remove = useDeleteJob()
  const restore = useRestoreJob()
  const retry = useRetryJob()
  const [open, setOpen] = useState<Job | null>(null)

  const deleteWithUndo = (job: Job) => {
    remove.mutate(job.id, {
      onSuccess: () => {
        toast('Generation deleted', {
          duration: UNDO_MS,
          action: {
            label: 'Undo',
            onClick: () => restore.mutate(job.id),
          },
        })
      },
      onError: (error) => toast.error(error.message),
    })
  }

  if (jobs.isPending) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton key={index} className="aspect-[4/5] w-full" />
        ))}
      </div>
    )
  }

  if (jobs.isError) {
    return <p className="text-sm text-destructive">{(jobs.error as Error).message}</p>
  }

  const items = jobs.data?.items ?? []
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
        Nothing here yet. <Link to="/" className="underline">Generate something</Link>.
      </div>
    )
  }

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="history-grid">
        {items.map((job) => {
          const thumb = job.assets[0]
          return (
            <div key={job.id} className="group overflow-hidden rounded-lg border">
              <button
                type="button"
                className="block w-full bg-muted"
                onClick={() => setOpen(job)}
                aria-label={`Open generation: ${job.prompt}`}
              >
                {thumb ? (
                  <img
                    src={thumb.url}
                    alt={job.prompt}
                    className="aspect-square w-full object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div className="flex aspect-square w-full items-center justify-center p-4 text-center text-xs text-muted-foreground">
                    {job.status === 'succeeded' ? 'no image' : job.status}
                  </div>
                )}
              </button>

              <div className="grid gap-2 p-3">
                <p className="line-clamp-2 text-sm">{job.prompt}</p>
                <div className="flex items-center gap-2">
                  <Badge variant="secondary" className="truncate text-[10px]">
                    {job.model}
                  </Badge>
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    {new Date(job.created_at).toLocaleString()}
                  </span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      <Dialog open={open !== null} onOpenChange={(next) => !next && setOpen(null)}>
        <DialogContent className="max-w-2xl">
          {open ? (
            <>
              <DialogHeader>
                <DialogTitle className="text-base">{open.model}</DialogTitle>
              </DialogHeader>

              <div className="grid gap-4">
                {open.assets.map((asset) => (
                  <img
                    key={asset.id}
                    src={asset.url}
                    alt={open.prompt}
                    className="w-full rounded-md border"
                  />
                ))}

                <p className="text-sm text-muted-foreground">{open.prompt}</p>

                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={retry.isPending}
                    onClick={() =>
                      retry.mutate(open.id, {
                        onSuccess: (job) => {
                          setOpen(null)
                          toast.success(`Retried as ${job.id.slice(0, 8)}`)
                        },
                        onError: (error) => toast.error(error.message),
                      })
                    }
                  >
                    <RefreshCw className="size-3.5" /> Retry
                  </Button>

                  {open.assets[0] ? (
                    <Button size="sm" variant="outline" asChild>
                      <a href={open.assets[0].download_url} download>
                        Download
                      </a>
                    </Button>
                  ) : null}

                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto text-destructive"
                    onClick={() => {
                      deleteWithUndo(open)
                      setOpen(null)
                    }}
                  >
                    <Trash2 className="size-3.5" /> Delete
                  </Button>
                </div>
              </div>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  )
}
