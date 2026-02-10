import type { ApiResponse } from './types'

export class ApiError extends Error {
  code: string
  details: unknown

  constructor(message: string, code: string, details: unknown) {
    super(message)
    this.code = code
    this.details = details
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: 'application/json' } })
  const body = (await res.json()) as ApiResponse<T>

  if (!res.ok) {
    throw new ApiError(`HTTP ${res.status}`, 'HTTP_ERROR', body)
  }
  if (!body.ok) {
    throw new ApiError(body.error.message_ko ?? 'API Error', body.error.code ?? 'API_ERROR', body.error.details)
  }
  return body.data
}

