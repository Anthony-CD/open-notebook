'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Settings2, Sparkles, Eye, Wrench } from 'lucide-react'
import { useModelDefaults, useModels } from '@/lib/hooks/use-models'
import { useTranslation } from '@/lib/hooks/use-translation'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { Badge } from '@/components/ui/badge'
import { Model } from '@/lib/types/models'

interface ModelSelectorProps {
  currentModel?: string
  onModelChange: (model?: string) => void
  disabled?: boolean
}

/**
 * Honest, best-effort model metadata - context window + capability badges.
 * Renders the "unknown"/"not tracked" state explicitly rather than hiding
 * the fact that most direct-provider models have no catalog data (see
 * api/models.py ModelResponse.capabilities_known docstring) - no invented
 * capabilities or context window.
 */
function ModelMetadataInfo({ model }: { model: Model }) {
  const { t } = useTranslation()
  const hasVision = model.capabilities?.includes('vision')
  const hasTools = model.capabilities?.includes('tools')

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span>
        {model.context_window
          ? t('models.contextWindowLabel', { count: model.context_window })
          : t('models.contextWindowUnknown')}
      </span>
      {model.capabilities_known ? (
        <>
          {hasVision && (
            <Badge variant="outline" className="gap-1 text-xs">
              <Eye className="h-3 w-3" />
              {t('models.capabilityVision')}
            </Badge>
          )}
          {hasTools && (
            <Badge variant="outline" className="gap-1 text-xs">
              <Wrench className="h-3 w-3" />
              {t('models.capabilityTools')}
            </Badge>
          )}
        </>
      ) : (
        <span>{t('models.capabilitiesUnknown')}</span>
      )}
    </div>
  )
}

export function ModelSelector({ 
  currentModel, 
  onModelChange,
  disabled = false 
}: ModelSelectorProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [selectedModel, setSelectedModel] = useState(currentModel || 'default')
  const { data: models, isLoading } = useModels()
  const { data: defaults } = useModelDefaults()

  useEffect(() => {
    setSelectedModel(currentModel || 'default')
  }, [currentModel])

  // Filter for language models only and sort by name
  const languageModels = useMemo(() => {
    if (!models) {
      return []
    }
    return [...models]
      .filter((model) => model.type === 'language')
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [models])

  const defaultModel = useMemo(() => {
    if (!defaults?.default_chat_model) return undefined
    return languageModels.find(model => model.id === defaults.default_chat_model)
  }, [defaults?.default_chat_model, languageModels])

  const currentModelName = useMemo(() => {
    if (currentModel) {
      return languageModels.find(model => model.id === currentModel)?.name || currentModel
    }
    if (defaultModel) {
      return defaultModel.name
    }
    return t('common.default')
  }, [currentModel, languageModels, defaultModel, t('common.default')])

  const handleSave = () => {
    onModelChange(selectedModel === 'default' ? undefined : selectedModel)
    setOpen(false)
  }

  const handleReset = () => {
    setSelectedModel('default')
    onModelChange(undefined)
    setOpen(false)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button 
          variant="outline" 
          size="sm"
          disabled={disabled}
          className="gap-2"
        >
          <Settings2 className="h-4 w-4" />
          <span className="text-xs">
            {currentModelName}
          </span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5" />
            {t('common.modelConfiguration')}
          </DialogTitle>
          <DialogDescription>
            {t('transformations.overrideModelDesc')}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="model">{t('common.model')}</Label>
            <Select value={selectedModel} onValueChange={setSelectedModel}>
              <SelectTrigger id="model">
                <SelectValue placeholder={t('models.selectModelPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">
                  <div className="flex items-center justify-between w-full">
                    <span>
                      {defaultModel 
                        ? `${t('common.default')} (${defaultModel.name})` 
                        : t('transformations.systemDefault')}
                    </span>
                    {defaultModel?.provider && (
                      <span className="text-xs text-muted-foreground ml-2">
                        {defaultModel.provider}
                      </span>
                    )}
                  </div>
                </SelectItem>
                {isLoading ? (
                  <div className="flex items-center justify-center py-2">
                    <LoadingSpinner size="sm" />
                  </div>
                ) : (
                  languageModels.map((model) => (
                    <SelectItem key={model.id} value={model.id}>
                      <div className="flex items-center justify-between w-full">
                        <span>{model.name}</span>
                        <span className="text-xs text-muted-foreground ml-2">
                          {model.provider}
                        </span>
                      </div>
                    </SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>
          </div>
          {selectedModel && selectedModel !== 'default' && (
            <div className="rounded-lg bg-muted p-3 space-y-2">
              <p className="text-sm text-muted-foreground">
                {t('transformations.sessionUseReplacement', { name: languageModels.find(m => m.id === selectedModel)?.name || selectedModel })}
              </p>
              {(() => {
                const model = languageModels.find((m) => m.id === selectedModel)
                return model ? <ModelMetadataInfo model={model} /> : null
              })()}
            </div>
          )}
          {selectedModel === 'default' && defaultModel && (
            <div className="rounded-lg bg-muted p-3">
              <ModelMetadataInfo model={defaultModel} />
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            {t('models.embeddingModelDisabledHint')}
          </p>
        </div>
        <DialogFooter className="flex justify-between">
          <Button variant="outline" onClick={handleReset}>
            {t('common.resetToDefault')}
          </Button>
          <Button onClick={handleSave}>
            {t('common.saveChanges')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
