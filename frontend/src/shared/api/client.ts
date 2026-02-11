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
  const raw = await res.text()
  if (!raw || !raw.trim()) {
    throw new ApiError(`Empty response (${res.status})`, 'EMPTY_RESPONSE', { status: res.status, path })
  }

  let body: ApiResponse<T>
  try {
    body = JSON.parse(raw) as ApiResponse<T>
  } catch {
    throw new ApiError(`Invalid JSON response (${res.status})`, 'INVALID_JSON', {
      status: res.status,
      path,
      preview: raw.slice(0, 240),
    })
  }

  if (!res.ok) {
    throw new ApiError(`HTTP ${res.status}`, 'HTTP_ERROR', body)
  }
  if (!body || typeof body !== 'object' || !('ok' in body)) {
    throw new ApiError('Malformed API body', 'MALFORMED_API_BODY', { status: res.status, path, body })
  }
  if (!body.ok) {
    throw new ApiError(body.error.message_ko ?? 'API Error', body.error.code ?? 'API_ERROR', body.error.details)
  }
  return body.data
}
