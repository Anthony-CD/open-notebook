export type EpisodeStatus =
  | 'running'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'error'
  | 'pending'
  | 'submitted'
  // The backend's canonical job-status vocabulary (open_notebook/utils/
  // job_status.py normalize_job_status) actually returns 'queued' and
  // 'canceled' - included here so cancel/retry UI can match on the real
  // values instead of only the legacy 'pending'/'submitted' aliases.
  | 'queued'
  | 'canceled'
  | 'unknown'

export interface EpisodeProfile {
  id: string
  name: string
  description: string
  /** speaker_profile record ID (null when the referenced profile no longer exists) */
  speaker_config: string | null
  /** Resolved speaker profile name, provided by the API for display */
  speaker_config_name?: string | null
  outline_llm?: string | null
  transcript_llm?: string | null
  language?: string | null
  default_briefing: string
  num_segments: number
  max_tokens?: number | null
}

export interface SpeakerVoiceConfig {
  name: string
  voice_id: string
  backstory: string
  personality: string
  voice_model?: string | null
}

export interface SpeakerProfile {
  id: string
  name: string
  description: string
  voice_model?: string | null
  speakers: SpeakerVoiceConfig[]
}

/**
 * Historical profile snapshot stored on an episode at generation time.
 * Episodes generated before the legacy provider/model strings were dropped
 * (#1107) may still carry them in the snapshot; newer episodes won't.
 *
 * The `*_model_provider` / `*_model_name` fields are resolved by the API at
 * serialization time from the snapshot's model record references
 * (outline_llm / transcript_llm / voice_model), batched per request. They
 * are absent when the reference is missing or no longer resolves (deleted
 * model) — fall back to the legacy strings, then to a placeholder.
 */
export interface EpisodeProfileSnapshot extends EpisodeProfile {
  outline_provider?: string | null
  outline_model?: string | null
  transcript_provider?: string | null
  transcript_model?: string | null
  outline_model_provider?: string | null
  outline_model_name?: string | null
  transcript_model_provider?: string | null
  transcript_model_name?: string | null
}

/** See EpisodeProfileSnapshot. */
export interface SpeakerProfileSnapshot extends SpeakerProfile {
  tts_provider?: string | null
  tts_model?: string | null
  voice_model_provider?: string | null
  voice_model_name?: string | null
}

export interface Language {
  code: string
  name: string
}

export interface PodcastEpisode {
  id: string
  name: string
  episode_profile: EpisodeProfileSnapshot
  speaker_profile: SpeakerProfileSnapshot
  briefing: string
  audio_file?: string | null
  audio_url?: string | null
  /** Size of the audio file on disk, in bytes - known before it's fetched (a stat(), not a read). */
  audio_size_bytes?: number | null
  transcript?: Record<string, unknown> | null
  outline?: Record<string, unknown> | null
  created?: string | null
  job_status?: EpisodeStatus | null
  error_message?: string | null
  /** surreal-commands job id backing this episode, when one exists - used to cancel a still-queued job. */
  command_id?: string | null
  favorited?: boolean
  tags?: string[]
  /** Notebook names recovered from the episode's stored content (best-effort, see api/routers/podcasts.py). */
  content_notebooks?: string[]
}

export interface PodcastEpisodeOrganizationUpdate {
  favorited?: boolean
  tags?: string[]
}

/** One generation stage's best-effort cost estimate (GET /podcasts/estimate). */
export interface PodcastStageEstimate {
  role: 'outline' | 'transcript' | 'tts'
  provider?: string | null
  model_name?: string | null
  cost_status: 'known' | 'free' | 'unknown'
  estimated_cost_usd?: number | null
  pricing_version?: string | null
  configured: boolean
}

export interface PodcastGenerationEstimate {
  outline: PodcastStageEstimate
  transcript: PodcastStageEstimate
  tts: PodcastStageEstimate
  total_estimated_cost_usd?: number | null
  is_complete: boolean
  is_estimate: boolean
}

export interface PodcastGenerationRequest {
  episode_profile: string
  /** speaker_profile record ID (the API also accepts a profile name) */
  speaker_profile: string
  episode_name: string
  content?: string
  notebook_id?: string
  briefing_suffix?: string | null
}

export interface PodcastGenerationResponse {
  job_id: string
  status: string
  message: string
  episode_profile: string
  episode_name: string
}

export type EpisodeStatusGroup = 'running' | 'completed' | 'failed' | 'pending'

export type EpisodeStatusGroups = Record<EpisodeStatusGroup, PodcastEpisode[]>

export const ACTIVE_EPISODE_STATUSES: EpisodeStatus[] = [
  'running',
  'processing',
  'pending',
  'submitted',
  'queued',
]

/** Jobs still waiting in the queue - the only ones the cancel endpoint can
 * actually cancel (commands/commands.py / open_notebook/utils/job_status.py
 * ACTIVE_JOB_STATUSES minus 'processing'). */
export const CANCELABLE_EPISODE_STATUSES: EpisodeStatus[] = [
  'pending',
  'submitted',
  'queued',
]

export const FAILED_EPISODE_STATUSES: EpisodeStatus[] = ['failed', 'error', 'canceled']

export function groupEpisodesByStatus(episodes: PodcastEpisode[]): EpisodeStatusGroups {
  return episodes.reduce<EpisodeStatusGroups>(
    (groups, episode) => {
      const status = episode.job_status || 'unknown'

      if (status === 'running' || status === 'processing') {
        groups.running.push(episode)
        return groups
      }

      if (status === 'completed') {
        groups.completed.push(episode)
        return groups
      }

      if (FAILED_EPISODE_STATUSES.includes(status)) {
        groups.failed.push(episode)
        return groups
      }

      groups.pending.push(episode)
      return groups
    },
    { running: [], completed: [], failed: [], pending: [] }
  )
}

export function speakerUsageMap(
  speakerProfiles: SpeakerProfile[] | undefined,
  episodeProfiles: EpisodeProfile[] | undefined
): Record<string, number> {
  if (!speakerProfiles || !episodeProfiles) {
    return {}
  }

  const usage: Record<string, number> = {}
  const nameById: Record<string, string> = {}

  for (const profile of speakerProfiles) {
    usage[profile.name] = 0
    nameById[profile.id] = profile.name
  }

  for (const episodeProfile of episodeProfiles) {
    // speaker_config references the speaker profile by record ID
    const key = episodeProfile.speaker_config
      ? nameById[episodeProfile.speaker_config]
      : undefined
    if (key !== undefined && key in usage) {
      usage[key] += 1
    }
  }

  return usage
}

/** Check if a profile needs model configuration (missing required model references) */
export function needsModelSetup(profile: EpisodeProfile | SpeakerProfile): boolean {
  if ('outline_llm' in profile) {
    const ep = profile as EpisodeProfile
    return !ep.outline_llm || !ep.transcript_llm
  }
  const sp = profile as SpeakerProfile
  return !sp.voice_model
}
