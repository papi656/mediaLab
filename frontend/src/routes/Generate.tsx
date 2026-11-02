import { useEffect, useMemo, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { toast } from 'sonner'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { DEFAULT_PARAMS, ParamFields, type Params } from '@/features/generate/ParamFields'
import { ReferencePicker } from '@/features/generate/ReferencePicker'
import { ResultPanel } from '@/features/generate/ResultPanel'
import { ApiError, type Asset, type GenerationInput } from '@/lib/api'
import { useCreateGeneration, useModels } from '@/lib/hooks'

const PREFERRED_DEFAULT = 'google/gemini-3-pro-image'

export default function GeneratePage() {
  const models = useModels()
  const create = useCreateGeneration()

  const [modelId, setModelId] = useState('')
  const [prompt, setPrompt] = useState('')
  const [params, setParams] = useState<Params>(DEFAULT_PARAMS)
  const [referenceAssets, setReferenceAssets] = useState<Asset[]>([])
  const [jobId, setJobId] = useState<string | null>(null)

  const catalogue = models.data?.models ?? []
  const caps = useMemo(() => catalogue.find((m) => m.id === modelId), [catalogue, modelId])

  // Default model once the catalogue arrives; prefer one that accepts references.
  useEffect(() => {
    if (modelId || catalogue.length === 0) return
    const preferred =
      catalogue.find((m) => m.id === PREFERRED_DEFAULT) ??
      catalogue.find((m) => m.max_refs > 0) ??
      catalogue[0]
    setModelId(preferred.id)
  }, [catalogue, modelId])

  // Changing model invalidates anything the previous model could not do.
  useEffect(() => {
    setParams(DEFAULT_PARAMS)
    setReferenceAssets([])
  }, [modelId])

  const error = create.error
  const errorMessage = error instanceof ApiError ? error.message : null

  const canSubmit = Boolean(caps) && prompt.trim().length > 0 && !create.isPending

  const submit = () => {
    if (!caps) return
    const payload: GenerationInput = {
      model: caps.id,
      prompt: prompt.trim(),
      n: params.n,
      ...(params.aspect_ratio ? { aspect_ratio: params.aspect_ratio } : {}),
      ...(params.resolution ? { resolution: params.resolution } : {}),
      ...(params.output_format ? { output_format: params.output_format } : {}),
      ...(params.background ? { background: params.background } : {}),
      ...(params.quality ? { quality: params.quality } : {}),
      ...(params.seed !== null ? { seed: params.seed } : {}),
      ...(params.output_compression !== null
        ? { output_compression: params.output_compression }
        : {}),
      ...(referenceAssets.length > 0
        ? { reference_asset_ids: referenceAssets.map((asset) => asset.id) }
        : {}),
    }

    create.mutate(payload, {
      onSuccess: (job) => setJobId(job.id),
      onError: (err) => toast.error(err instanceof Error ? err.message : 'Could not start'),
    })
  }

  return (
    <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="grid gap-6">
        <div className="grid gap-2">
          <Label htmlFor="prompt">Prompt</Label>
          <Textarea
            id="prompt"
            rows={5}
            placeholder="a red panda astronaut, studio lighting"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') submit()
            }}
          />
          <p className="text-xs text-muted-foreground">⌘↵ to generate</p>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="model">Model</Label>
          <Select value={modelId} onValueChange={setModelId}>
            <SelectTrigger id="model">
              <SelectValue placeholder="Choose a model" />
            </SelectTrigger>
            <SelectContent>
              {catalogue.map((model) => (
                <SelectItem key={model.id} value={model.id}>
                  {model.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {caps ? (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="secondary">{caps.id}</Badge>
              {caps.max_refs > 0 ? (
                <span>up to {caps.max_refs} reference images</span>
              ) : (
                <span>text only</span>
              )}
              {caps.endpoint_count > 1 ? <span>· {caps.endpoint_count} providers</span> : null}
            </div>
          ) : null}
        </div>

        {caps ? (
          <ParamFields
            caps={caps}
            value={params}
            onChange={(patch) => setParams((current) => ({ ...current, ...patch }))}
          />
        ) : null}

        {caps ? (
          <ReferencePicker
            maxRefs={caps.max_refs}
            value={referenceAssets}
            onChange={setReferenceAssets}
            onError={(message) => toast.error(message)}
          />
        ) : null}

        {errorMessage ? (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage}</AlertDescription>
          </Alert>
        ) : null}

        <Button className="w-fit" disabled={!canSubmit} onClick={submit}>
          <Sparkles className="size-4" />
          {create.isPending ? 'Starting…' : 'Generate'}
        </Button>

        {models.data ? (
          <p className="text-xs text-muted-foreground">
            Catalogue updated {new Date(models.data.generated_at).toLocaleDateString()}
          </p>
        ) : null}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Result</CardTitle>
        </CardHeader>
        <CardContent>
          <ResultPanel
            jobId={jobId}
            onUseAsReference={(asset) => {
              if (!caps) return
              if (caps.max_refs === 0) {
                toast.error(`${caps.id} does not accept reference images`)
                return
              }
              if (referenceAssets.length >= caps.max_refs) {
                toast.error(`Limit is ${caps.max_refs} reference image(s)`)
                return
              }
              setReferenceAssets((current) => [...current, asset])
              toast.success('Added to references')
            }}
          />
        </CardContent>
      </Card>
    </div>
  )
}
