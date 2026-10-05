'use client'

import { useEffect, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Play, Loader2 } from 'lucide-react'
import { Transformation } from '@/lib/types/transformations'
import { useExecuteTransformation, useEstimateTransformationCost } from '@/lib/hooks/use-transformations'
import { ModelSelector } from '@/components/common/ModelSelector'
import { useTranslation } from '@/lib/hooks/use-translation'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'

interface TransformationPlaygroundProps {
  transformations: Transformation[] | undefined
  selectedTransformation?: Transformation
}

function formatCostUsd(amount: number): string {
  if (amount === 0) return '$0.00'
  if (amount < 0.01) return `< $0.01`
  return `$${amount.toFixed(amount < 1 ? 4 : 2)}`
}

export function TransformationPlayground({ transformations, selectedTransformation }: TransformationPlaygroundProps) {
  const { t } = useTranslation()
  const [selectedId, setSelectedId] = useState(selectedTransformation?.id || '')
  const [inputText, setInputText] = useState('')
  const [modelId, setModelId] = useState('')
  const [output, setOutput] = useState('')
  const [actualCost, setActualCost] = useState<{ status?: string | null; amount?: number | null } | null>(null)

  const executeTransformation = useExecuteTransformation()
  const estimateCost = useEstimateTransformationCost()

  // Re-estimate (debounced) whenever the inputs that affect cost change -
  // this is a best-effort ESTIMATE only (real cost is unknown until the
  // call completes), never calls an LLM itself.
  useEffect(() => {
    if (!selectedId || !inputText.trim()) {
      estimateCost.reset()
      return
    }
    const handle = setTimeout(() => {
      estimateCost.mutate({
        transformation_id: selectedId,
        input_text: inputText,
        model_id: modelId || undefined,
      })
    }, 500)
    return () => clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, inputText, modelId])

  const handleExecute = async () => {
    if (!selectedId || !modelId || !inputText.trim()) {
      return
    }

    setActualCost(null)
    const result = await executeTransformation.mutateAsync({
      transformation_id: selectedId,
      input_text: inputText,
      model_id: modelId
    })

    setOutput(result.output)
    setActualCost({ status: result.cost_status, amount: result.actual_cost_usd })
  }

  const canExecute = selectedId && modelId && inputText.trim() && !executeTransformation.isPending
  const costEstimate = estimateCost.data

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t('transformations.playground')}</CardTitle>
          <CardDescription>
            {t('transformations.desc')}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="transformation">{t('navigation.transformation')}</Label>
              <Select name="transformation" value={selectedId} onValueChange={setSelectedId}>
                <SelectTrigger id="transformation">
                  <SelectValue placeholder={t('transformations.selectToStart')} />
                </SelectTrigger>
                <SelectContent>
                  {transformations?.map((transformation) => (
                    <SelectItem key={transformation.id} value={transformation.id}>
                      {transformation.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div>
              <ModelSelector
                label={t('transformations.model')}
                name="model"
                modelType="language"
                value={modelId}
                onChange={setModelId}
                placeholder={t('transformations.selectModel')}
              />
            </div>
          </div>

          <div>
            <Label htmlFor="input">{t('transformations.inputLabel')}</Label>
            <Textarea
              id="input"
              name="input"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder={t('transformations.inputPlaceholder')}
              rows={8}
              className="font-mono text-sm"
            />
          </div>

          {selectedId && inputText.trim() && (
            <div className="rounded-md border bg-muted/50 p-3 text-sm space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">{t('transformations.modelToBeUsed')}</span>
                <span className="font-medium">
                  {costEstimate?.model_name ?? (estimateCost.isPending ? t('transformations.previewGenerating') : '—')}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">{t('transformations.estimatedCost')}</span>
                <span className="font-medium">
                  {estimateCost.isPending
                    ? '…'
                    : costEstimate && costEstimate.cost_status === 'known' && costEstimate.estimated_cost_usd !== null
                      ? formatCostUsd(costEstimate.estimated_cost_usd)
                      : costEstimate?.cost_status === 'free'
                        ? t('transformations.freeModel')
                        : t('usage.costUnavailable')}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">{t('transformations.estimateDisclaimer')}</p>
            </div>
          )}

          <div className="flex justify-center">
            <Button
              onClick={handleExecute}
              disabled={!canExecute}
              size="lg"
            >
              {executeTransformation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  {t('transformations.running')}
                </>
              ) : (
                <>
                  <Play className="h-4 w-4 mr-2" />
                  {t('transformations.runTest')}
                </>
              )}
            </Button>
          </div>

          {actualCost && (
            <div className="rounded-md border bg-muted/50 p-3 text-sm flex items-center justify-between">
              <span className="text-muted-foreground">{t('transformations.actualCost')}</span>
              <span className="font-medium">
                {actualCost.status === 'known' && actualCost.amount !== null && actualCost.amount !== undefined
                  ? formatCostUsd(actualCost.amount)
                  : actualCost.status === 'free'
                    ? t('transformations.freeModel')
                    : t('usage.costUnavailable')}
              </span>
            </div>
          )}

          {output && (
            <div className="space-y-2">
              <span className="text-sm font-medium leading-none">{t('transformations.outputLabel')}</span>
              <Card>
                <ScrollArea className="h-[400px]">
                  <CardContent className="pt-6">
                    <div className="prose prose-sm max-w-none dark:prose-invert">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm, remarkMath]}
                        rehypePlugins={[rehypeKatex]}
                        components={{
                          table: ({ children }) => (
                            <div className="my-4 overflow-x-auto">
                              <table className="min-w-full border-collapse border border-border">{children}</table>
                            </div>
                          ),
                          thead: ({ children }) => <thead className="bg-muted">{children}</thead>,
                          tbody: ({ children }) => <tbody>{children}</tbody>,
                          tr: ({ children }) => <tr className="border-b border-border">{children}</tr>,
                          th: ({ children }) => <th className="border border-border px-3 py-2 text-left font-semibold">{children}</th>,
                          td: ({ children }) => <td className="border border-border px-3 py-2">{children}</td>,
                        }}
                      >
                        {output}
                      </ReactMarkdown>
                    </div>
                  </CardContent>
                </ScrollArea>
              </Card>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}