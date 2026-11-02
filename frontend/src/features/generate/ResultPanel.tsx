import { Download, RefreshCw } from 'lucide-react'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { Asset, Job } from '@/lib/api'
import { useJob, useRetryJob } from '@/lib/hooks'

function statusVariant(status: Job['status']): 'default' | 'secondary' | 'destructive' {
  if (status === 'succeeded') return 'default'
  if (status === 'failed' || status === 'interrupted') return 'destructive'
  return 'secondary'
}

export function ResultPanel({
  jobId,
  onUseAsReference,
}: {
  jobId: string | null
  onUseAsReference: (asset: Asset) => void
}) {
  const job = useJob(jobId)
  const retry = useRetryJob()

  if (!jobId) {
    return (
      <div className="flex h-full min-h-64 items-center justify-center rounded-lg border border-dashed text-sm text-muted-foreground">
        Your result will appear here.
      </div>
    )
  }

  if (job.isPending || !job.data) {
    return (
      <div className="grid gap-3">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="aspect-square w-full" />
      </div>
    )
  }

  const data = job.data
  const busy = data.status === 'queued' || data.status === 'running'
  const seconds =
    data.started_at && data.finished_at
      ? (new Date(data.finished_at).getTime() - new Date(data.started_at).getTime()) / 1000
      : null

  return (
    <div className="grid gap-4" data-testid="result-panel" data-status={data.status}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant={statusVariant(data.status)} data-testid="job-status">
          {data.status}
        </Badge>
        <span className="text-muted-foreground">{data.model}</span>
        {data.cost_usd != null ? (
          <span className="text-muted-foreground">· ${data.cost_usd.toFixed(4)}</span>
        ) : null}
        {seconds != null ? (
          <span className="text-muted-foreground">· {seconds.toFixed(1)}s</span>
        ) : null}
        {data.n_received != null && data.n_received !== data.n_requested ? (
          <span className="text-muted-foreground">
            · {data.n_received} of {data.n_requested} returned
          </span>
        ) : null}
      </div>

      {busy ? (
        <div className="grid gap-3">
          <Skeleton className="aspect-square w-full" />
          <p className="text-sm text-muted-foreground">
            {data.status === 'queued' ? 'Queued…' : 'Generating…'}
          </p>
        </div>
      ) : null}

      {data.error ? (
        <Alert variant="destructive">
          <AlertDescription>{data.error}</AlertDescription>
        </Alert>
      ) : null}

      {data.status === 'succeeded' && data.assets.length === 0 ? (
        <Alert>
          <AlertDescription>The provider returned no images.</AlertDescription>
        </Alert>
      ) : null}

      {data.assets.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {data.assets.map((asset) => (
            <figure key={asset.id} className="grid gap-2">
              <img
                src={asset.url}
                alt={data.prompt}
                className="w-full rounded-md border bg-muted object-contain"
              />
              <figcaption className="flex items-center gap-2 text-xs text-muted-foreground">
                {asset.width && asset.height ? (
                  <span>
                    {asset.width}×{asset.height}
                  </span>
                ) : null}
                <span>{(asset.bytes / 1024).toFixed(0)} KB</span>
                <a
                  href={asset.download_url}
                  className="ml-auto inline-flex items-center gap-1 underline-offset-2 hover:underline"
                  download
                >
                  <Download className="size-3" /> Download
                </a>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 underline-offset-2 hover:underline"
                  onClick={() => onUseAsReference(asset)}
                >
                  Use as reference
                </button>
              </figcaption>
            </figure>
          ))}
        </div>
      ) : null}

      {data.status === 'failed' || data.status === 'interrupted' ? (
        <Button
          variant="outline"
          size="sm"
          className="w-fit"
          disabled={retry.isPending}
          onClick={() => retry.mutate(data.id)}
        >
          <RefreshCw className="size-3.5" /> Retry
        </Button>
      ) : null}
    </div>
  )
}
