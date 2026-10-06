'use client'

import { useMemo, useState } from 'react'
import { formatDistanceToNow } from 'date-fns'
import { getDateLocale } from '@/lib/utils/date-locale'
import { InfoIcon, RefreshCcw, Star, Tag as TagIcon, Trash2, X as XIcon, Ban } from 'lucide-react'

import {
  CANCELABLE_EPISODE_STATUSES,
  EpisodeStatus,
  FAILED_EPISODE_STATUSES,
  PodcastEpisode,
} from '@/lib/types/podcasts'
import { useCancelPodcastJob, useUpdateEpisodeOrganization } from '@/lib/hooks/use-podcasts'
import { cn } from '@/lib/utils'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PodcastAudioPlayer } from './PodcastAudioPlayer'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { TFunction } from 'i18next'

interface EpisodeCardProps {
  episode: PodcastEpisode
  onDelete: (episodeId: string) => Promise<void> | void
  deleting?: boolean
  onRetry?: (episodeId: string) => Promise<void> | void
  retrying?: boolean
}

const getSTATUS_META = (t: TFunction): Record<
  EpisodeStatus | 'unknown',
  { label: string; className: string }
> => ({
  running: {
    label: t('podcasts.processingLabel'),
    className: 'bg-warn-tint text-warn border-warn/30',
  },
  processing: {
    label: t('podcasts.processingLabel'),
    className: 'bg-warn-tint text-warn border-warn/30',
  },
  completed: {
    label: t('podcasts.completedLabel'),
    className: 'bg-fern-tint text-fern border-fern/30',
  },
  failed: {
    label: t('podcasts.failedLabel'),
    className: 'bg-destructive-tint text-destructive border-destructive/30',
  },
  error: {
    label: t('podcasts.failedLabel'),
    className: 'bg-destructive-tint text-destructive border-destructive/30',
  },
  pending: {
    label: t('podcasts.pendingLabel'),
    className: 'bg-teal-tint text-teal border-teal/30',
  },
  submitted: {
    label: t('podcasts.pendingLabel'),
    className: 'bg-teal-tint text-teal border-teal/30',
  },
  queued: {
    label: t('podcasts.pendingLabel'),
    className: 'bg-teal-tint text-teal border-teal/30',
  },
  canceled: {
    label: t('podcasts.canceledLabel'),
    className: 'bg-muted text-muted-foreground border-transparent',
  },
  unknown: {
    label: t('common.unknown'),
    className: 'bg-muted text-muted-foreground border-transparent',
  },
})

function StatusBadge({ status }: { status?: EpisodeStatus | null }) {
  const { t } = useTranslation()
  // Don't show badge for completed episodes
  if (status === 'completed') {
    return null
  }

  const meta = getSTATUS_META(t)[status ?? 'unknown']
  return (
    <Badge
      variant="outline"
      className={cn('uppercase tracking-wide text-xs', meta.className)}
    >
      {meta.label}
    </Badge>
  )
}

type OutlineSegment = {
  name?: string
  description?: string
  size?: string
}

type OutlineData = {
  segments?: OutlineSegment[]
}

type TranscriptEntry = {
  speaker?: string
  dialogue?: string
}

type TranscriptData = {
  transcript?: TranscriptEntry[]
}

function extractOutlineSegments(outline: unknown): OutlineSegment[] {
  if (outline && typeof outline === 'object' && 'segments' in outline) {
    const data = outline as OutlineData
    if (Array.isArray(data.segments)) {
      return data.segments
    }
  }
  return []
}

/**
 * "provider / name" label for a snapshot model row. Prefers the display
 * fields the API resolves from the snapshot's model references, falls back
 * to the legacy snapshot strings (pre-#1107 episodes), then to a dash.
 */
function formatModelLabel(
  provider?: string | null,
  name?: string | null,
  legacyProvider?: string | null,
  legacyName?: string | null
): string {
  return `${provider || legacyProvider || '—'} / ${name || legacyName || '—'}`
}

function extractTranscriptEntries(transcript: unknown): TranscriptEntry[] {
  if (transcript && typeof transcript === 'object' && 'transcript' in transcript) {
    const data = transcript as TranscriptData
    if (Array.isArray(data.transcript)) {
      return data.transcript
    }
  }
  return []
}

export function EpisodeCard({ episode, onDelete, deleting, onRetry, retrying }: EpisodeCardProps) {
  const { t, language } = useTranslation()
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [tagInput, setTagInput] = useState('')

  const cancelJob = useCancelPodcastJob()
  const updateOrganization = useUpdateEpisodeOrganization()

  const outlineSegments = useMemo(() => extractOutlineSegments(episode.outline), [episode.outline])
  const transcriptEntries = useMemo(() => extractTranscriptEntries(episode.transcript), [episode.transcript])

  const handleToggleFavorite = () => {
    updateOrganization.mutate({
      episodeId: episode.id,
      payload: { favorited: !episode.favorited },
    })
  }

  const handleAddTag = () => {
    const cleaned = tagInput.trim()
    if (!cleaned) return
    const nextTags = Array.from(new Set([...(episode.tags ?? []), cleaned]))
    updateOrganization.mutate({ episodeId: episode.id, payload: { tags: nextTags } })
    setTagInput('')
  }

  const handleRemoveTag = (tag: string) => {
    const nextTags = (episode.tags ?? []).filter((existing) => existing !== tag)
    updateOrganization.mutate({ episodeId: episode.id, payload: { tags: nextTags } })
  }

  const handleCancelJob = () => {
    if (episode.command_id) {
      cancelJob.mutate(episode.command_id)
    }
  }

  const distance = episode.created
    ? formatDistanceToNow(new Date(episode.created), {
        addSuffix: true,
        locale: getDateLocale(language),
      })
    : null

  const createdLabel = distance
    ? t('podcasts.created', { time: distance })
    : null

  const handleDelete = () => {
    void onDelete(episode.id)
  }

  const handleRetry = () => {
    if (onRetry) {
      void onRetry(episode.id)
    }
  }

  const isFailed = FAILED_EPISODE_STATUSES.includes(episode.job_status as EpisodeStatus)
  const isCancelable = CANCELABLE_EPISODE_STATUSES.includes(
    episode.job_status as EpisodeStatus
  )

  return (
    <Card>
      <CardContent className="space-y-4 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                onClick={handleToggleFavorite}
                disabled={updateOrganization.isPending}
                title={episode.favorited ? t('podcasts.unfavorite') : t('podcasts.favorite')}
                aria-pressed={!!episode.favorited}
              >
                <Star
                  className={cn(
                    'h-4 w-4',
                    episode.favorited ? 'fill-warn text-warn' : 'text-muted-foreground'
                  )}
                />
              </Button>
              <h3 className="text-base font-semibold text-foreground">
                {episode.name}
              </h3>
              <StatusBadge status={episode.job_status} />
              {episode.tags?.map((tag) => (
                <Badge key={tag} variant="secondary" className="gap-1 text-[10px]">
                  <TagIcon className="h-2.5 w-2.5" />
                  {tag}
                  <button
                    type="button"
                    onClick={() => handleRemoveTag(tag)}
                    aria-label={t('podcasts.removeTag', { tag })}
                    className="ml-0.5 rounded-full hover:text-destructive"
                  >
                    <XIcon className="h-2.5 w-2.5" />
                  </button>
                </Badge>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              {t('podcasts.profile')}: {episode.episode_profile?.name || t('common.unknown')}
              {createdLabel ? ` • ${createdLabel}` : ''}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {isCancelable && episode.command_id ? (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="outline" size="sm" disabled={cancelJob.isPending}>
                    <Ban className="mr-2 h-4 w-4" />
                    {t('podcasts.cancelJob')}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>{t('podcasts.cancelJobConfirmTitle')}</AlertDialogTitle>
                    <AlertDialogDescription>
                      {t('podcasts.cancelJobConfirmDesc')}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                    <AlertDialogAction onClick={handleCancelJob}>
                      {t('podcasts.cancelJob')}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            ) : null}
            <Dialog open={detailsOpen} onOpenChange={setDetailsOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm">
                  <InfoIcon className="mr-2 h-4 w-4" /> {t('podcasts.details')}
                </Button>
              </DialogTrigger>
              <DialogContent className="w-[min(90vw,720px)] max-h-[85vh] overflow-hidden">
                <DialogHeader>
                  <DialogTitle>{episode.name}</DialogTitle>
                  <DialogDescription>
                    {episode.episode_profile?.name || t('common.unknown')}
                    {createdLabel ? ` • ${createdLabel}` : ''}
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 overflow-hidden">
                  {episode.audio_file || episode.audio_url ? (
                    <PodcastAudioPlayer episode={episode} />
                  ) : null}

                  <Tabs defaultValue="summary" className="h-[60vh] flex flex-col">
                    <TabsList className="grid w-full grid-cols-3">
                      <TabsTrigger value="summary">{t('podcasts.summaryTab')}</TabsTrigger>
                      <TabsTrigger value="outline">{t('podcasts.outlineTab')}</TabsTrigger>
                      <TabsTrigger value="transcript">{t('podcasts.transcriptTab')}</TabsTrigger>
                    </TabsList>

                    <TabsContent value="summary" className="flex-1 overflow-hidden">
                      <ScrollArea className="h-full pr-4">
                        <div className="space-y-6">
                          <section className="space-y-2">
                            <h4 className="text-sm font-semibold text-foreground">{t('podcasts.episodeProfile')}</h4>
                            <div className="grid gap-2 text-sm md:grid-cols-2">
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.outlineModel')}</p>
                                <p>
                                  {formatModelLabel(
                                    episode.episode_profile?.outline_model_provider,
                                    episode.episode_profile?.outline_model_name,
                                    episode.episode_profile?.outline_provider,
                                    episode.episode_profile?.outline_model
                                  )}
                                </p>
                              </div>
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.transcriptModel')}</p>
                                <p>
                                  {formatModelLabel(
                                    episode.episode_profile?.transcript_model_provider,
                                    episode.episode_profile?.transcript_model_name,
                                    episode.episode_profile?.transcript_provider,
                                    episode.episode_profile?.transcript_model
                                  )}
                                </p>
                              </div>
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.segments')}</p>
                                <p>{episode.episode_profile?.num_segments ?? '—'}</p>
                              </div>
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.maxTokens')}</p>
                                <p>{episode.episode_profile?.max_tokens ?? '—'}</p>
                              </div>
                            </div>
                            {episode.episode_profile?.default_briefing ? (
                              <div className="rounded border bg-muted/30 p-3 text-xs whitespace-pre-wrap">
                                {episode.episode_profile.default_briefing}
                              </div>
                            ) : null}
                          </section>

                          <section className="space-y-2">
                            <h4 className="text-sm font-semibold text-foreground">{t('podcasts.speakerProfile')}</h4>
                            <p className="text-xs text-muted-foreground">
                              {formatModelLabel(
                                episode.speaker_profile?.voice_model_provider,
                                episode.speaker_profile?.voice_model_name,
                                episode.speaker_profile?.tts_provider,
                                episode.speaker_profile?.tts_model
                              )}
                            </p>
                            {episode.speaker_profile?.speakers?.map((speaker, index) => (
                              <div
                                key={`${speaker.name}-${index}`}
                                className="rounded-md border bg-muted/20 p-3 text-xs"
                              >
                                <p className="font-semibold text-foreground">{speaker.name}</p>
                                <p className="text-muted-foreground">{t('podcasts.voiceId')}: {speaker.voice_id}</p>
                                <p className="mt-2 whitespace-pre-wrap text-muted-foreground">
                                  <span className="font-semibold">{t('podcasts.backstory')}:</span> {speaker.backstory}
                                </p>
                                <p className="mt-2 whitespace-pre-wrap text-muted-foreground">
                                  <span className="font-semibold">{t('podcasts.personality')}:</span> {speaker.personality}
                                </p>
                              </div>
                            ))}
                          </section>

                          {episode.briefing ? (
                            <section className="space-y-2">
                              <h4 className="text-sm font-semibold text-foreground">{t('podcasts.briefing')}</h4>
                              <div className="rounded border bg-muted/30 p-3 text-xs whitespace-pre-wrap">
                                {episode.briefing}
                              </div>
                            </section>
                          ) : null}

                          <section className="space-y-2">
                            <h4 className="text-sm font-semibold text-foreground">
                              {t('podcasts.contentSourcesTitle')}
                            </h4>
                            <p className="text-xs text-muted-foreground">
                              {t('podcasts.contentSourcesDesc')}
                            </p>
                            {episode.content_notebooks && episode.content_notebooks.length > 0 ? (
                              <div className="flex flex-wrap gap-1.5">
                                {episode.content_notebooks.map((name) => (
                                  <Badge key={name} variant="outline" className="text-[10px]">
                                    {name}
                                  </Badge>
                                ))}
                              </div>
                            ) : (
                              <p className="text-xs text-muted-foreground">
                                {t('podcasts.noContentSourcesInfo')}
                              </p>
                            )}
                          </section>

                          <section className="space-y-2">
                            <h4 className="text-sm font-semibold text-foreground">{t('podcasts.tagsLabel')}</h4>
                            <div className="flex flex-wrap gap-1.5">
                              {(episode.tags ?? []).length === 0 ? (
                                <span className="text-xs text-muted-foreground">{t('podcasts.noTags')}</span>
                              ) : (
                                episode.tags?.map((tag) => (
                                  <Badge key={tag} variant="secondary" className="gap-1 text-[10px]">
                                    <TagIcon className="h-2.5 w-2.5" />
                                    {tag}
                                    <button
                                      type="button"
                                      onClick={() => handleRemoveTag(tag)}
                                      aria-label={t('podcasts.removeTag', { tag })}
                                      className="ml-0.5 rounded-full hover:text-destructive"
                                    >
                                      <XIcon className="h-2.5 w-2.5" />
                                    </button>
                                  </Badge>
                                ))
                              )}
                            </div>
                            <div className="flex gap-2">
                              <Input
                                value={tagInput}
                                onChange={(e) => setTagInput(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') {
                                    e.preventDefault()
                                    handleAddTag()
                                  }
                                }}
                                placeholder={t('podcasts.addTagPlaceholder')}
                                className="h-8 text-xs"
                              />
                              <Button size="sm" variant="outline" onClick={handleAddTag}>
                                {t('podcasts.tagsLabel')}
                              </Button>
                            </div>
                          </section>
                        </div>
                      </ScrollArea>
                    </TabsContent>

                    <TabsContent value="outline" className="flex-1 overflow-hidden">
                      <ScrollArea className="h-full pr-4">
                        {outlineSegments.length > 0 ? (
                          <div className="space-y-3">
                            {outlineSegments.map((segment, index) => (
                              <div key={index} className="rounded border bg-muted/20 p-3 text-xs space-y-1">
                                <div className="flex items-center justify-between gap-2">
                                  <p className="font-semibold text-foreground">{segment.name ?? `${t('podcasts.segment')} ${index + 1}`}</p>
                                  {segment.size ? (
                                    <Badge variant="outline" className="text-[10px] uppercase tracking-wide">{segment.size}</Badge>
                                  ) : null}
                                </div>
                                <p className="text-muted-foreground whitespace-pre-wrap">{segment.description ?? t('podcasts.noDescription')}</p>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="text-xs text-muted-foreground">{t('podcasts.noOutline')}</p>
                        )}
                      </ScrollArea>
                    </TabsContent>

                    <TabsContent value="transcript" className="flex-1 overflow-hidden">
                      <ScrollArea className="h-full pr-4 space-y-3">
                        {transcriptEntries.length > 0 ? (
                          transcriptEntries.map((entry, index) => (
                            <div key={index} className="rounded border bg-muted/20 p-3 text-xs space-y-1">
                              <p className="font-semibold text-foreground">{entry.speaker ?? t('podcasts.speaker')}</p>
                              <p className="text-muted-foreground whitespace-pre-wrap">{entry.dialogue ?? ''}</p>
                            </div>
                          ))
                        ) : (
                          <p className="text-xs text-muted-foreground">{t('podcasts.noTranscript')}</p>
                        )}
                      </ScrollArea>
                    </TabsContent>
                  </Tabs>
                </div>
              </DialogContent>
            </Dialog>
            {isFailed && onRetry ? (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="outline" size="sm" disabled={retrying}>
                    <RefreshCcw className={cn('mr-2 h-4 w-4', retrying && 'animate-spin')} />
                    {retrying ? t('podcasts.retrying') : t('podcasts.retry')}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>{t('podcasts.retryConfirmTitle')}</AlertDialogTitle>
                    <AlertDialogDescription>
                      {t('podcasts.retryConfirmDesc')}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                    <AlertDialogAction onClick={handleRetry}>
                      {t('podcasts.retryConfirmAction')}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            ) : null}
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="ghost" size="sm" className="text-destructive">
                  <Trash2 className="mr-2 h-4 w-4" />
                  {t('podcasts.delete')}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{t('podcasts.deleteEpisodeTitle')}</AlertDialogTitle>
                  <AlertDialogDescription>
                    {t('podcasts.deleteEpisodeDesc', { name: episode.name })}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                  <AlertDialogAction onClick={handleDelete} disabled={deleting}>
                    {deleting ? t('podcasts.deleting') : t('podcasts.delete')}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>

        {episode.audio_file || episode.audio_url ? (
          <PodcastAudioPlayer episode={episode} />
        ) : null}

        {isFailed && episode.error_message ? (
          <div className="rounded-md border border-destructive/30 bg-destructive-tint p-3">
            <p className="text-xs font-medium text-destructive">{t('podcasts.errorDetails')}</p>
            <p className="mt-1 text-xs whitespace-pre-wrap text-destructive">{episode.error_message}</p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
