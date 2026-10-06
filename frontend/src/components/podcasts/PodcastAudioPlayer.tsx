'use client'

import { useEffect, useRef, useState } from 'react'
import { Download, Loader2, Rewind, FastForward } from 'lucide-react'

import apiClient from '@/lib/api/client'
import { resolvePodcastAssetUrl } from '@/lib/api/podcasts'
import { PodcastEpisode } from '@/lib/types/podcasts'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useTranslation } from '@/lib/hooks/use-translation'

const SKIP_SECONDS = 15
const SPEED_OPTIONS = [0.75, 1, 1.25, 1.5, 2]

function formatMegabytes(bytes?: number | null): string | null {
  if (!bytes || bytes <= 0) return null
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

interface PodcastAudioPlayerProps {
  episode: PodcastEpisode
  className?: string
}

/**
 * Podcast episode player: play/pause/progress/volume come from the native
 * <audio controls> element; skip +-15s and a speed selector are added on top
 * since neither is exposed by native <audio> controls.
 *
 * Audio is NOT fetched until the user explicitly clicks "load" (P1 "no
 * silent large downloads"): episode.audio_url is a Bearer-auth-protected
 * endpoint, so unlike a plain <audio src> with preload="none" it cannot be
 * lazily range-fetched by the browser on its own - the previous
 * implementation worked around that by eagerly GETing the whole file as a
 * blob in a mount effect, which silently downloaded every visible episode's
 * full audio as soon as the podcasts page rendered. Legacy episodes with
 * only `audio_file` (no protected endpoint, see api/routers/podcasts.py) get
 * a direct URL instead - no auth header needed, so native preload="none"
 * already defers the fetch to the browser without any gating here.
 */
export function PodcastAudioPlayer({ episode, className }: PodcastAudioPlayerProps) {
  const { t } = useTranslation()
  const audioRef = useRef<HTMLAudioElement>(null)
  const [directSrc, setDirectSrc] = useState<string | undefined>()
  const [blobSrc, setBlobSrc] = useState<string | undefined>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [speed, setSpeed] = useState(1)

  const isProtected = !!episode.audio_url
  const sizeLabel = formatMegabytes(episode.audio_size_bytes)

  // Legacy (no protected endpoint) episodes: resolving the direct URL is
  // just string-building (getApiUrl()), never a fetch - safe to do eagerly.
  useEffect(() => {
    let active = true
    if (!isProtected && episode.audio_file) {
      void resolvePodcastAssetUrl(episode.audio_file).then((url) => {
        if (active) setDirectSrc(url)
      })
    }
    return () => {
      active = false
    }
  }, [isProtected, episode.audio_file])

  useEffect(() => {
    return () => {
      if (blobSrc) URL.revokeObjectURL(blobSrc)
    }
  }, [blobSrc])

  const handleLoad = async () => {
    if (!episode.audio_url || loading || blobSrc) return
    setLoading(true)
    setError(null)
    try {
      const response = await apiClient.get<Blob>(
        episode.audio_url.replace(/^\/api(?=\/)/, ''),
        { responseType: 'blob' }
      )
      setBlobSrc(URL.createObjectURL(response.data))
    } catch (err) {
      console.error('Unable to load podcast audio', err)
      setError(t('podcasts.audioUnavailable'))
    } finally {
      setLoading(false)
    }
  }

  const audioSrc = isProtected ? blobSrc : directSrc

  const handleDownloadAudio = () => {
    if (!audioSrc) return
    const safeName = episode.name.replace(/[^a-zA-Z0-9_-]+/g, '_').slice(0, 80) || 'podcast'
    const link = document.createElement('a')
    link.href = audioSrc
    link.download = `${safeName}.mp3`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const applySpeed = (value: number) => {
    setSpeed(value)
    if (audioRef.current) {
      audioRef.current.playbackRate = value
    }
  }

  const skip = (deltaSeconds: number) => {
    const el = audioRef.current
    if (!el) return
    el.currentTime = Math.max(0, Math.min(el.duration || Infinity, el.currentTime + deltaSeconds))
  }

  if (!audioSrc) {
    if (isProtected && !error) {
      return (
        <div className={className}>
          <Button
            variant="outline"
            size="sm"
            onClick={handleLoad}
            disabled={loading}
            className="w-full justify-center"
          >
            {loading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {t('podcasts.loadingAudio')}
              </>
            ) : (
              <>
                {sizeLabel
                  ? t('podcasts.loadAudioWithSize', { size: sizeLabel })
                  : t('podcasts.loadAudioToPlay')}
              </>
            )}
          </Button>
        </div>
      )
    }
    if (error) {
      return <p className={className ? `${className} text-sm text-destructive` : 'text-sm text-destructive'}>{error}</p>
    }
    return null
  }

  return (
    <div className={className}>
      <div className="flex items-center gap-2 rounded-md border bg-card p-2">
        <audio
          ref={audioRef}
          controls
          preload="none"
          src={audioSrc}
          className="w-full flex-1 min-w-0"
          onLoadedMetadata={() => {
            if (audioRef.current) audioRef.current.playbackRate = speed
          }}
        />
        <Button variant="outline" size="icon" onClick={handleDownloadAudio} title={t('common.download')}>
          <Download className="h-4 w-4" />
        </Button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => skip(-SKIP_SECONDS)}
          title={t('podcasts.skipBack15')}
          aria-label={t('podcasts.skipBack15')}
        >
          <Rewind className="mr-1 h-3.5 w-3.5" />
          -{SKIP_SECONDS}s
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => skip(SKIP_SECONDS)}
          title={t('podcasts.skipForward15')}
          aria-label={t('podcasts.skipForward15')}
        >
          <FastForward className="mr-1 h-3.5 w-3.5" />
          +{SKIP_SECONDS}s
        </Button>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">{t('podcasts.speedLabel')}</span>
          <Select value={String(speed)} onValueChange={(v) => applySpeed(Number(v))}>
            <SelectTrigger className="h-7 w-[72px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SPEED_OPTIONS.map((option) => (
                <SelectItem key={option} value={String(option)} className="text-xs">
                  {option}x
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  )
}
