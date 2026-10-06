import apiClient from './client'
import {
  Transformation,
  CreateTransformationRequest,
  UpdateTransformationRequest,
  ExecuteTransformationRequest,
  ExecuteTransformationResponse,
  DefaultPrompt,
  PreviewTransformationRequest,
  PreviewTransformationResponse,
  EstimateTransformationCostRequest,
  EstimateTransformationCostResponse,
  TransformationResultHistory,
} from '@/lib/types/transformations'

export const transformationsApi = {
  list: async () => {
    const response = await apiClient.get<Transformation[]>('/transformations')
    return response.data
  },

  get: async (id: string) => {
    const response = await apiClient.get<Transformation>(`/transformations/${id}`)
    return response.data
  },

  create: async (data: CreateTransformationRequest) => {
    const response = await apiClient.post<Transformation>('/transformations', data)
    return response.data
  },

  update: async (id: string, data: UpdateTransformationRequest) => {
    const response = await apiClient.put<Transformation>(`/transformations/${id}`, data)
    return response.data
  },

  delete: async (id: string) => {
    await apiClient.delete(`/transformations/${id}`)
  },

  execute: async (data: ExecuteTransformationRequest) => {
    const response = await apiClient.post<ExecuteTransformationResponse>('/transformations/execute', data)
    return response.data
  },

  preview: async (data: PreviewTransformationRequest) => {
    const response = await apiClient.post<PreviewTransformationResponse>('/transformations/preview', data)
    return response.data
  },

  estimateCost: async (data: EstimateTransformationCostRequest) => {
    const response = await apiClient.post<EstimateTransformationCostResponse>('/transformations/estimate-cost', data)
    return response.data
  },

  getResultHistory: async (transformationId: string, sourceId: string) => {
    const response = await apiClient.get<TransformationResultHistory>(
      `/transformations/${transformationId}/results/${sourceId}`
    )
    return response.data
  },

  getDefaultPrompt: async () => {
    const response = await apiClient.get<DefaultPrompt>('/transformations/default-prompt')
    return response.data
  },

  updateDefaultPrompt: async (prompt: { transformation_instructions: string }) => {
    const response = await apiClient.put<DefaultPrompt>('/transformations/default-prompt', prompt)
    return response.data
  }
}