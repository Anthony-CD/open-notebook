'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useModels } from '@/lib/hooks/use-models'
import { useTranslation } from '@/lib/hooks/use-translation'
import { usePreferencesStore } from '@/lib/stores/preferences-store'

const NO_PREFERENCE = '__none__'

/**
 * Local-only display preferences (density, default model) - separate from
 * the backend-bound SettingsForm below it on this page. Saved straight to
 * localStorage via preferences-store (zustand `persist`), so there's no
 * save button - each change applies immediately, same as ThemeToggle.
 */
export function PreferencesCard() {
  const { t } = useTranslation()
  const density = usePreferencesStore((state) => state.density)
  const setDensity = usePreferencesStore((state) => state.setDensity)
  const defaultModel = usePreferencesStore((state) => state.defaultModel)
  const setDefaultModel = usePreferencesStore((state) => state.setDefaultModel)
  const hasHydrated = usePreferencesStore((state) => state.hasHydrated)
  const { data: models, isLoading: modelsLoading } = useModels()
  const languageModels = models?.filter((model) => model.type === 'language') ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('settings.preferencesTitle')}</CardTitle>
        <CardDescription>{t('settings.preferencesDesc')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-3">
          <Label htmlFor="density-pref">{t('settings.density')}</Label>
          <Select
            value={density}
            onValueChange={(value) => setDensity(value as 'comfortable' | 'compact')}
            disabled={!hasHydrated}
          >
            <SelectTrigger id="density-pref" className="w-full sm:w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="comfortable">{t('settings.densityComfortable')}</SelectItem>
              <SelectItem value="compact">{t('settings.densityCompact')}</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-sm text-muted-foreground">{t('settings.densityDesc')}</p>
        </div>

        <div className="space-y-3">
          <Label htmlFor="default-model-pref">{t('settings.defaultModelLabel')}</Label>
          <Select
            value={defaultModel ?? NO_PREFERENCE}
            onValueChange={(value) => setDefaultModel(value === NO_PREFERENCE ? null : value)}
            disabled={!hasHydrated || modelsLoading}
          >
            <SelectTrigger id="default-model-pref" className="w-full sm:w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_PREFERENCE}>{t('settings.defaultModelNone')}</SelectItem>
              {languageModels.map((model) => (
                <SelectItem key={model.id} value={model.id}>
                  {model.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-sm text-muted-foreground">{t('settings.defaultModelDesc')}</p>
        </div>
      </CardContent>
    </Card>
  )
}
