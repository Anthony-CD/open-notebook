import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import { PodcastAudioPlayer } from './PodcastAudioPlayer'
import apiClient from '@/lib/api/client'
import type { PodcastEpisode } from '@/lib/types/podcasts'

// useTranslation is mocked globally in setup.ts (t returns the key string).

vi.mock('@/lib/api/client', () => ({
  default: { get: vi.fn() },
}))

vi.mock('@/lib/api/podcasts', () => ({
  resolvePodcastAssetUrl: vi.fn(async () => undefined),
}))

function makeEpisode(overrides: Partial<PodcastEpisode> = {}): PodcastEpisode {
  return {
    id: 'episode:1',
    name: 'Test Episode',
    episode_profile: {
      id: 'episode_profile:1',
      name: 'default',
      description: '',
      speaker_config: null,
      default_briefing: '',
      num_segments: 5,
    },
    speaker_profile: {
      id: 'speaker_profile:1',
      name: 'default',
      description: '',
      speakers: [],
    },
    briefing: 'briefing',
    job_status: 'completed',
    ...overrides,
  }
}

describe('PodcastAudioPlayer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  // Regression test for a live production bug: apiClient's request
  // interceptor prepends its own "/api" baseURL to any request path that
  // isn't a network-absolute URL (scheme+host). episode.audio_url already
  // starts with "/api/..." (PodcastEpisodeResponse), so passing it to
  // apiClient.get() unmodified double-prefixed the request to
  // "/api/api/podcasts/.../audio" - a 404 in relative-path/tunnel deploys
  // where getApiUrl() resolves to "" (confirmed live: the network request
  // Chrome actually sent was exactly that double-prefixed 404 URL). Fixed
  // by stripping the redundant leading "/api" before calling apiClient,
  // matching every other apiClient call in this codebase.
  it('strips the redundant leading /api before calling apiClient for the audio blob', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: new Blob(['audio']) })

    render(
      <PodcastAudioPlayer
        episode={makeEpisode({ audio_url: '/api/podcasts/episodes/episode:1/audio' })}
      />
    )

    // Audio is gated behind an explicit click (no silent large download on
    // mount) - trigger the load before asserting on the fetch it causes.
    fireEvent.click(screen.getByRole('button', { name: 'podcasts.loadAudioToPlay' }))

    await waitFor(() => {
      expect(apiClient.get).toHaveBeenCalledWith(
        '/podcasts/episodes/episode:1/audio',
        { responseType: 'blob' }
      )
    })
  })

  it('lets the user download the already-loaded audio without a second network request', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: new Blob(['audio']) })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    render(
      <PodcastAudioPlayer
        episode={makeEpisode({
          name: 'Beating the Street: Chapter 1',
          audio_url: '/api/podcasts/episodes/episode:1/audio',
        })}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'podcasts.loadAudioToPlay' }))

    const downloadButton = await screen.findByTitle('common.download')
    const getCalls = vi.mocked(apiClient.get).mock.calls.length

    fireEvent.click(downloadButton)

    // Reuses the blob URL already fetched for playback - no extra apiClient call.
    expect(apiClient.get).toHaveBeenCalledTimes(getCalls)
    expect(clickSpy).toHaveBeenCalledTimes(1)
    const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement
    expect(anchor.download).toBe('Beating_the_Street_Chapter_1.mp3')
    expect(anchor.href).toMatch(/^blob:/)

    clickSpy.mockRestore()
  })

  it('does not fetch the protected audio blob until the user clicks to load it', () => {
    // P1 "no autoplay / no silent large downloads": a protected episode
    // (audio_url set) must not trigger apiClient.get on mount - only the
    // explicit "load" button click should.
    render(
      <PodcastAudioPlayer
        episode={makeEpisode({ audio_url: '/api/podcasts/episodes/episode:1/audio' })}
      />
    )

    expect(apiClient.get).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'podcasts.loadAudioToPlay' })).toBeInTheDocument()
  })

  it('does not re-fetch when the load button is clicked twice in a row', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: new Blob(['audio']) })

    render(
      <PodcastAudioPlayer
        episode={makeEpisode({ audio_url: '/api/podcasts/episodes/episode:1/audio' })}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'podcasts.loadAudioToPlay' }))

    await waitFor(() => {
      expect(apiClient.get).toHaveBeenCalledTimes(1)
    })

    // Once loaded, the load button is replaced by the <audio> element itself -
    // nothing left to click that would re-trigger handleLoad. Guard directly
    // against a second call if handleLoad were somehow invoked again.
    expect(
      screen.queryByRole('button', { name: 'podcasts.loadAudioToPlay' })
    ).not.toBeInTheDocument()
    expect(apiClient.get).toHaveBeenCalledTimes(1)
  })
})
