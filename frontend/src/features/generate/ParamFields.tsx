import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { ModelCaps } from '@/lib/api'

export type Params = {
  n: number
  aspect_ratio: string | null
  resolution: string | null
  output_format: string | null
  background: string | null
  quality: string | null
  seed: number | null
  output_compression: number | null
}

export const DEFAULT_PARAMS: Params = {
  n: 1,
  aspect_ratio: null,
  resolution: null,
  output_format: null,
  background: null,
  quality: null,
  seed: null,
  output_compression: null,
}

const UNSET = '__default__'

function SelectField({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string
  label: string
  value: string | null
  options: string[]
  onChange: (value: string | null) => void
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Select
        value={value ?? UNSET}
        onValueChange={(next) => onChange(next === UNSET ? null : next)}
      >
        <SelectTrigger id={id}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={UNSET}>Default</SelectItem>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

/**
 * Every control here exists because the selected model advertises it.
 * A model that cannot do X never renders X at all.
 */
export function ParamFields({
  caps,
  value,
  onChange,
}: {
  caps: ModelCaps
  value: Params
  onChange: (patch: Partial<Params>) => void
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {caps.aspect_ratios.length > 0 ? (
        <SelectField
          id="aspect_ratio"
          label="Aspect ratio"
          value={value.aspect_ratio}
          options={caps.aspect_ratios}
          onChange={(aspect_ratio) => onChange({ aspect_ratio })}
        />
      ) : null}

      {caps.resolutions.length > 0 ? (
        <SelectField
          id="resolution"
          label="Resolution"
          value={value.resolution}
          options={caps.resolutions}
          onChange={(resolution) => onChange({ resolution })}
        />
      ) : null}

      {caps.formats.length > 0 ? (
        <SelectField
          id="output_format"
          label="Format"
          value={value.output_format}
          options={caps.formats}
          onChange={(output_format) => onChange({ output_format })}
        />
      ) : null}

      {caps.backgrounds.length > 0 ? (
        <SelectField
          id="background"
          label="Background"
          value={value.background}
          options={caps.backgrounds}
          onChange={(background) => onChange({ background })}
        />
      ) : null}

      {caps.qualities.length > 0 ? (
        <SelectField
          id="quality"
          label="Quality"
          value={value.quality}
          options={caps.qualities}
          onChange={(quality) => onChange({ quality })}
        />
      ) : null}

      {caps.n.max > 1 ? (
        <div className="grid gap-2">
          <Label htmlFor="n">Images</Label>
          <Input
            id="n"
            type="number"
            min={caps.n.min}
            max={caps.n.max}
            value={value.n}
            onChange={(event) => {
              const next = Number(event.target.value)
              onChange({
                n: Number.isFinite(next)
                  ? Math.min(Math.max(next, caps.n.min), caps.n.max)
                  : caps.n.min,
              })
            }}
          />
          <p className="text-xs text-muted-foreground">
            {caps.n.min}–{caps.n.max} per request
          </p>
        </div>
      ) : null}

      {caps.supports_seed ? (
        <div className="grid gap-2">
          <Label htmlFor="seed">Seed</Label>
          <Input
            id="seed"
            type="number"
            placeholder="random"
            value={value.seed ?? ''}
            onChange={(event) =>
              onChange({ seed: event.target.value === '' ? null : Number(event.target.value) })
            }
          />
        </div>
      ) : null}

      {caps.supports_compression ? (
        <div className="grid gap-2">
          <Label htmlFor="compression">Compression</Label>
          <Input
            id="compression"
            type="number"
            min={0}
            max={100}
            placeholder="default"
            value={value.output_compression ?? ''}
            onChange={(event) =>
              onChange({
                output_compression:
                  event.target.value === '' ? null : Number(event.target.value),
              })
            }
          />
        </div>
      ) : null}
    </div>
  )
}
