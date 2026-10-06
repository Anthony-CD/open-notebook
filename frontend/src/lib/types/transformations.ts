export interface Transformation {
  id: string
  name: string
  title: string
  description: string
  prompt: string
  apply_default: boolean
  model_id: string | null
  created: string
  updated: string
}

export interface CreateTransformationRequest {
  name: string
  title: string
  description: string
  prompt: string
  apply_default?: boolean
  model_id?: string | null
}

export interface UpdateTransformationRequest {
  name?: string
  title?: string
  description?: string
  prompt?: string
  apply_default?: boolean
  model_id?: string | null
}

export interface ExecuteTransformationRequest {
  transformation_id: string
  input_text: string
  model_id?: string | null
}

export type CostStatus = 'known' | 'free' | 'unknown'

export interface ExecuteTransformationResponse {
  output: string
  transformation_id: string
  model_id: string | null
  input_tokens?: number | null
  output_tokens?: number | null
  cost_status?: CostStatus | null
  actual_cost_usd?: number | null
}

export interface DefaultPrompt {
  transformation_instructions: string
}

export interface PreviewTransformationRequest {
  prompt: string
  sample_text?: string | null
}

export interface PreviewTransformationResponse {
  system_prompt: string
  sample_input: string
}

export interface EstimateTransformationCostRequest {
  transformation_id: string
  input_text: string
  model_id?: string | null
}

export interface EstimateTransformationCostResponse {
  model_id: string | null
  provider: string | null
  model_name: string | null
  cost_status: CostStatus
  estimated_cost_usd: number | null
  estimated_input_tokens: number
  estimated_output_tokens: number
  is_estimate: boolean
}

export interface TransformationResultVersion {
  version: number
  content: string
  created: string | null
  is_latest: boolean
}

export interface TransformationResultHistory {
  transformation_id: string
  source_id: string
  versions: TransformationResultVersion[]
}
