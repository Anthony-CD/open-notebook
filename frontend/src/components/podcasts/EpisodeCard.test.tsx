import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'

import { EpisodeCard } from './EpisodeCard'
import type { PodcastEpisode } from '@/lib/types/podcasts'

// useTranslation is mocked globally in setup.ts (t returns the key string)

vi.mock('@/lib/api/client', () => ({
  default: { get: vi.fn() },
}))

vi.mock('@/lib/api/podcasts', () => ({
  resolvePodcastAssetUrl: vi.fn(async () => undefined),
}))

// EpisodeCard also drives cancel/organization mutations via these hooks -
// mock them (same pattern as CostMeterWidget.test.tsx) instead of wrapping
// every render in a real QueryClientProvider.
vi.mock('@/lib/hooks/use-podcasts', () => ({
  useCancelPodcastJob: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useUpdateEpisodeOrganization: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
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

function renderAndOpenDetails(episode: PodcastEpisode) {
  render(<EpisodeCard episode={episode} onDelete={vi.fn()} />)
  fireEvent.click(screen.getByText('podcasts.details'))
}

describe('EpisodeCard model details', () => {
  it('renders API-resolved model display fields for new episodes', () => {
    renderAndOpenDetails(
      makeEpisode({
        episode_profile: {
          id: 'episode_profile:1',
          name: 'modern',
          description: '',
          speaker_config: null,
          default_briefing: '',
          num_segments: 5,
          outline_llm: 'model:outline',
          transcript_llm: 'model:transcript',
          outline_model_provider: 'openai',
          outline_model_name: 'gpt-4o',
          transcript_model_provider: 'anthropic',
          transcript_model_name: 'claude-sonnet',
        },
        speaker_profile: {
          id: 'speaker_profile:1',
          name: 'modern',
          description: '',
          speakers: [],
          voice_model: 'model:voice',
          voice_model_provider: 'elevenlabs',
          voice_model_name: 'eleven_turbo',
        },
      })
    )

    expect(screen.getByText('openai / gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('anthropic / claude-sonnet')).toBeInTheDocument()
    expect(screen.getByText('elevenlabs / eleven_turbo')).toBeInTheDocument()
  })

  it('falls back to legacy snapshot strings for old episodes', () => {
    renderAndOpenDetails(
      makeEpisode({
        episode_profile: {
          id: 'episode_profile:1',
          name: 'legacy',
          description: '',
          speaker_config: null,
          default_briefing: '',
          num_segments: 5,
          outline_provider: 'openai',
          outline_model: 'gpt-3.5-turbo',
          transcript_provider: 'openai',
          transcript_model: 'gpt-4',
        },
        speaker_profile: {
          id: 'speaker_profile:1',
          name: 'legacy',
          description: '',
          speakers: [],
          tts_provider: 'openai',
          tts_model: 'tts-1',
        },
      })
    )

    expect(screen.getByText('openai / gpt-3.5-turbo')).toBeInTheDocument()
    expect(screen.getByText('openai / gpt-4')).toBeInTheDocument()
    expect(screen.getByText('openai / tts-1')).toBeInTheDocument()
  })

  it('degrades to dashes when references are unresolvable and no legacy strings exist', () => {
    renderAndOpenDetails(
      makeEpisode({
        episode_profile: {
          id: 'episode_profile:1',
          name: 'orphaned',
          description: '',
          speaker_config: null,
          default_briefing: '',
          num_segments: 5,
          outline_llm: 'model:deleted',
          transcript_llm: 'model:deleted',
        },
        speaker_profile: {
          id: 'speaker_profile:1',
          name: 'orphaned',
          description: '',
          speakers: [],
          voice_model: 'model:deleted',
        },
      })
    )

    expect(screen.getAllByText('— / —')).toHaveLength(3)
  })
})

describe('EpisodeCard audio wiring', () => {
  // The actual load/play/download/fetch behavior (including the
  // apiClient /api double-prefix regression and the "don't re-fetch
  // already-loaded audio" guard) now lives in PodcastAudioPlayer and is
  // covered directly in PodcastAudioPlayer.test.tsx. EpisodeCard's own job
  // is just to mount that component - inline in the card, and again inside
  // the details dialog - whenever the episode has audio.
  it('renders a PodcastAudioPlayer (inline) when the episode has a protected audio_url', () => {
    render(
      <EpisodeCard
        episode={makeEpisode({ audio_url: '/api/podcasts/episodes/episode:1/audio' })}
        onDelete={vi.fn()}
      />
    )

    expect(screen.getByRole('button', { name: 'podcasts.loadAudioToPlay' })).toBeInTheDocument()
  })

  it('renders a second, independent PodcastAudioPlayer inside the details dialog', () => {
    renderAndOpenDetails(
      makeEpisode({ audio_url: '/api/podcasts/episodes/episode:1/audio' })
    )

    // Radix's open Dialog marks the rest of the page aria-hidden (focus
    // trap), so the inline card player is still mounted but no longer
    // query-visible by role from outside - scope to the dialog itself to
    // confirm it got its own PodcastAudioPlayer instance.
    const dialog = screen.getByRole('dialog')
    expect(
      within(dialog).getByRole('button', { name: 'podcasts.loadAudioToPlay' })
    ).toBeInTheDocument()
  })

  it('renders no audio player when the episode has neither audio_file nor audio_url', () => {
    render(<EpisodeCard episode={makeEpisode()} onDelete={vi.fn()} />)

    expect(
      screen.queryByRole('button', { name: 'podcasts.loadAudioToPlay' })
    ).not.toBeInTheDocument()
  })
})
