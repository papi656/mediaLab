import { useRef } from 'react'
import { ImagePlus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { Asset } from '@/lib/api'
import { useUploadReference } from '@/lib/hooks'

/**
 * Rendered only when the selected model accepts references.
 * The limit is the model's own `input_references` max, not a guess.
 */
export function ReferencePicker({
  maxRefs,
  value,
  onChange,
  onError,
}: {
  maxRefs: number
  value: Asset[]
  onChange: (next: Asset[]) => void
  onError?: (message: string) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const upload = useUploadReference()

  if (maxRefs <= 0) return null

  const remaining = maxRefs - value.length

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return
    const accepted: Asset[] = []
    for (const file of Array.from(files).slice(0, Math.max(remaining, 0))) {
      try {
        accepted.push(await upload.mutateAsync(file))
      } catch (error) {
        onError?.(error instanceof Error ? error.message : 'Upload failed')
        break
      }
    }
    if (accepted.length) onChange([...value, ...accepted])
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <div className="grid gap-2">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium">Reference images</span>
        <span className="text-xs text-muted-foreground">
          {value.length} / {maxRefs}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {value.map((asset, index) => (
          <div key={asset.id} className="group relative size-20 overflow-hidden rounded-md border">
            <img src={asset.url} alt={`reference ${index + 1}`} className="size-full object-cover" />
            <button
              type="button"
              aria-label={`Remove reference ${index + 1}`}
              onClick={() => onChange(value.filter((a) => a.id !== asset.id))}
              className="absolute right-1 top-1 rounded-full bg-background/90 p-0.5 opacity-0 transition-opacity group-hover:opacity-100"
            >
              <X className="size-3" />
            </button>
          </div>
        ))}

        {remaining > 0 ? (
          <Button
            type="button"
            variant="outline"
            className="size-20 flex-col gap-1 border-dashed"
            disabled={upload.isPending}
            onClick={() => inputRef.current?.click()}
          >
            <ImagePlus className="size-4" />
            <span className="text-xs">{upload.isPending ? '…' : 'Add'}</span>
          </Button>
        ) : null}
      </div>

      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        multiple
        className="hidden"
        onChange={(event) => void handleFiles(event.target.files)}
      />
    </div>
  )
}
