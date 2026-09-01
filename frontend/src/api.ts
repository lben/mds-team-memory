async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(url, { credentials: 'same-origin', ...options })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export const api = {
  get: <T>(url: string) => request<T>(url),
  post: <T>(url: string, body?: unknown) =>
    request<T>(url, {
      method: 'POST',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  postForm: <T>(url: string, form: FormData) => request<T>(url, { method: 'POST', body: form }),
  put: <T>(url: string, body: unknown) =>
    request<T>(url, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  patch: <T>(url: string, body: unknown) =>
    request<T>(url, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  delete: <T>(url: string) => request<T>(url, { method: 'DELETE' }),
}

export interface Item {
  id: string
  kind: string
  title: string | null
  body: string
  visibility: string
  author: string
  author_verified: boolean
  author_id: string
  is_mine: boolean
  parent_id: string | null
  group_id: string | null
  contributors: number
  group_size: number
  helped: number
  marked_helped: boolean
  endorsed: boolean
  endorsements: number
  endorsed_by_me: boolean
  question_status: string | null
  accepted_answer_id: string | null
  correction_state: string | null
  source_document_id: string | null
  source_passage_id: string | null
  created_at: string
  updated_at: string
  snippet?: string
  answer_count?: number
  question?: { id: string; body: string; status: string | null } | null
}

export interface Corroboration {
  group_size: number
  contributors: number
}
