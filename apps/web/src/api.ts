import type {
  AppConfig,
  AgentThread,
  AgentThreadDetail,
  Attachment,
  AuthResponse,
  AuthorityRecord,
  CivicComment,
  CivicCase,
  CivicPost,
  CivicNotification,
  FeedPage,
  PublicPostAttachment,
  Checkpoint,
  FollowSubject,
  PreparationApproval,
  ShareSnapshot,
  TicketPreparation,
  LiveEndpointProfile,
  ResearchAnswer,
  ResearchHistory,
  TaskEvent,
  TicketDetail,
  TicketStatus,
  UsageSummary,
  User,
  SourceEvidence,
} from './types'

function resolveApiUrl(value: string | undefined): string {
  const configured = value?.trim().replace(/\/$/, '') ?? ''
  if (!configured) return ''

  try {
    const configuredUrl = new URL(configured, window.location.origin)
    const loopbackHosts = new Set(['localhost', '127.0.0.1', '[::1]'])
    const pageIsRemote = !loopbackHosts.has(window.location.hostname)
    if (pageIsRemote && loopbackHosts.has(configuredUrl.hostname)) return ''
    return configuredUrl.origin === window.location.origin && configuredUrl.pathname === '/' ? '' : configured
  } catch {
    // A malformed build-time override should not take the whole auth screen offline.
    return ''
  }
}

// Same-origin is the safe default: Vite proxies it locally and deployed hosts
// can route /api without exposing a user's browser to a machine-local URL.
const API_URL = resolveApiUrl(import.meta.env.VITE_API_URL as string | undefined)
const TOKEN_KEY = 'civitas.access_token'

export class ApiError extends Error {
  readonly status: number
  readonly code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export type AgentStreamEvent = {
  event: 'plan' | 'status' | 'tool_call' | 'tool_result' | 'message' | 'error' | 'done'
  data: Record<string, unknown>
}

export function getToken(): string | null {
  try { return window.localStorage.getItem(TOKEN_KEY) } catch { return null }
}

export function setToken(token: string): void {
  try { window.localStorage.setItem(TOKEN_KEY, token) } catch { throw new ApiError(0, 'This browser could not save your session. Enable site storage and try again.') }
}

export function clearToken(): void {
  try { window.localStorage.removeItem(TOKEN_KEY) } catch { /* storage may be blocked; the in-memory session still clears */ }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'The CivitasX service is unavailable. Is the local API running?')
  }

  if (!response.ok) {
    if (response.status === 401) {
      clearToken()
      window.dispatchEvent(new Event('civitas:session-expired'))
    }
    let detail = `The request could not be completed (${response.status}).`
    let code: string | undefined
    try {
      const body = (await response.json()) as { detail?: string; code?: string }
      detail = body.detail ?? detail
      code = body.code
    } catch {
      // Keep the status message when the service returned non-JSON content.
    }
    const safeDetail = response.status >= 500
      ? 'The civic service is having trouble right now. Your local draft is still safe; try again shortly.'
      : response.status === 429
        ? 'Too many actions in a short period. Please wait a moment and try again.'
        : response.status === 404
          ? 'That civic record is no longer available.'
          : detail
    throw new ApiError(response.status, safeDetail, code)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

async function requestText(path: string): Promise<string> {
  const headers = new Headers({ Accept: 'text/plain' })
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let response: Response
  try { response = await fetch(`${API_URL}${path}`, { headers }) } catch { throw new ApiError(0, 'The CivitasX service is unavailable. Is the local API running?') }
  if (!response.ok) {
    if (response.status === 401) {
      clearToken()
      window.dispatchEvent(new Event('civitas:session-expired'))
    }
    throw new ApiError(response.status, `Request failed (${response.status})`)
  }
  return response.text()
}

export async function getConfig(): Promise<AppConfig> {
  return request<AppConfig>('/api/config')
}

export async function register(name: string, email: string, password: string): Promise<User> {
  const result = await request<AuthResponse>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ name, email, password }),
  })
  setToken(result.access_token)
  return result.user
}

export async function login(email: string, password: string): Promise<User> {
  const result = await request<AuthResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  setToken(result.access_token)
  return result.user
}

export async function recoverPassword(email: string, recoveryCode: string, password: string): Promise<User> {
  const result = await request<AuthResponse>('/api/auth/recover', {
    method: 'POST',
    body: JSON.stringify({ email, recovery_code: recoveryCode, password }),
  })
  setToken(result.access_token)
  return result.user
}

export async function exchangeCognitoCode(code: string, redirectUri: string): Promise<User> {
  const result = await request<{ access_token: string }>('/api/auth/cognito/exchange', {
    method: 'POST',
    body: JSON.stringify({ code, redirect_uri: redirectUri }),
  })
  setToken(result.access_token)
  return getMe()
}

export async function logout(): Promise<void> {
  try {
    await request<void>('/api/auth/logout', { method: 'POST' })
  } finally {
    clearToken()
  }
}

export async function getMe(): Promise<User> {
  return request<User>('/api/me')
}

export async function getCases(): Promise<CivicCase[]> {
  const result = await request<{ items: CivicCase[] }>('/api/cases')
  return result.items
}

export async function createCase(goal: string, title?: string): Promise<CivicCase> {
  return request<CivicCase>('/api/cases', {
    method: 'POST',
    body: JSON.stringify({ goal, title }),
  })
}

export async function getCase(caseId: string): Promise<CivicCase> {
  return request<CivicCase>(`/api/cases/${encodeURIComponent(caseId)}`)
}

export async function updateCase(
  caseId: string,
  input: { goal?: string; title?: string; notes?: string; version: number },
): Promise<CivicCase> {
  return request<CivicCase>(`/api/cases/${encodeURIComponent(caseId)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export async function getEvents(caseId: string): Promise<TaskEvent[]> {
  const result = await request<{ items: TaskEvent[] }>(`/api/cases/${encodeURIComponent(caseId)}/events`)
  return result.items
}

export async function getUsage(caseId: string): Promise<UsageSummary> {
  return request<UsageSummary>(`/api/cases/${encodeURIComponent(caseId)}/usage`)
}

export async function runResearch(
  question: string,
  options: { caseId?: string; maxSources?: number } = {},
): Promise<ResearchAnswer> {
  return request<ResearchAnswer>('/api/research', {
    method: 'POST',
    body: JSON.stringify({
      question,
      case_id: options.caseId,
      max_sources: options.maxSources,
    }),
  })
}

export async function getCaseResearch(caseId: string): Promise<ResearchHistory> {
  return request<ResearchHistory>(`/api/cases/${encodeURIComponent(caseId)}/research`)
}

export async function getAuthorities(query?: string): Promise<AuthorityRecord[]> {
  const suffix = query ? `?query=${encodeURIComponent(query)}` : ''
  const result = await request<{ items: AuthorityRecord[] }>(`/api/authorities${suffix}`)
  return result.items
}

export async function getFeedPage(params: {
  sort?: 'recent' | 'popular' | 'nearby' | 'following' | 'recommended'
  locality?: string
  visibility?: string
  status?: TicketStatus
  authorityId?: string
  topic?: string
  cursor?: string
  limit?: number
} = {}): Promise<FeedPage> {
  const search = new URLSearchParams()
  if (params.sort) search.set('sort', params.sort)
  if (params.locality) search.set('locality', params.locality)
  if (params.visibility) search.set('visibility', params.visibility)
  if (params.status) search.set('status', params.status)
  if (params.authorityId) search.set('authority_id', params.authorityId)
  if (params.topic) search.set('topic', params.topic)
  if (params.cursor) search.set('cursor', params.cursor)
  if (params.limit) search.set('limit', String(params.limit))
  const suffix = search.toString() ? `?${search.toString()}` : ''
  return request<FeedPage>(`/api/feed${suffix}`)
}

export async function getFeed(params: {
  sort?: 'recent' | 'popular' | 'nearby' | 'following' | 'recommended'
  locality?: string
  visibility?: string
  status?: TicketStatus
  authorityId?: string
  topic?: string
} = {}): Promise<CivicPost[]> {
  return (await getFeedPage(params)).items
}

export async function getFeedPost(postId: string, locality?: string): Promise<CivicPost> {
  const suffix = locality ? `?locality=${encodeURIComponent(locality)}` : ''
  return request<CivicPost>(`/api/feed/${encodeURIComponent(postId)}${suffix}`)
}

export async function getFeedPostAttachments(postId: string, locality?: string): Promise<PublicPostAttachment[]> {
  const suffix = locality ? `?locality=${encodeURIComponent(locality)}` : ''
  const result = await request<{ items: PublicPostAttachment[] }>(`/api/feed/${encodeURIComponent(postId)}/attachments${suffix}`)
  return result.items
}

export async function downloadFeedPostAttachment(postId: string, attachmentId: string, locality?: string): Promise<Blob> {
  const search = new URLSearchParams()
  if (locality) search.set('locality', locality)
  const suffix = search.toString() ? `?${search.toString()}` : ''
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let response: Response
  try {
    response = await fetch(`${API_URL}/api/feed/${encodeURIComponent(postId)}/attachments/${encodeURIComponent(attachmentId)}${suffix}`, { headers })
  } catch (error) {
    throw error instanceof DOMException && error.name === 'AbortError' ? error : new ApiError(0, 'The civic image could not be loaded.')
  }
  if (!response.ok) throw new ApiError(response.status, `The civic image could not be loaded (${response.status}).`)
  return response.blob()
}

export async function getFeedComments(postId: string): Promise<CivicComment[]> {
  const result = await request<{ items: CivicComment[] }>(`/api/feed/${encodeURIComponent(postId)}/comments`)
  return result.items
}

export async function getFeedPostEvidence(postId: string, locality?: string): Promise<SourceEvidence[]> {
  const suffix = locality ? `?locality=${encodeURIComponent(locality)}` : ''
  const result = await request<{ items: SourceEvidence[] }>(`/api/feed/${encodeURIComponent(postId)}/evidence${suffix}`)
  return result.items
}

export async function addFeedComment(postId: string, body: string, parentId?: string | null): Promise<CivicComment> {
  return request<CivicComment>(`/api/feed/${encodeURIComponent(postId)}/comments`, {
    method: 'POST',
    body: JSON.stringify({ body, parent_id: parentId ?? null }),
  })
}

export async function voteFeedPost(postId: string, value: -1 | 0 | 1): Promise<{ post_id: string; value: -1 | 0 | 1; upvotes: number; downvotes: number; vote_score: number }> {
  return request(`/api/feed/${encodeURIComponent(postId)}/vote`, {
    method: 'POST',
    body: JSON.stringify({ value }),
  })
}

export async function followFeedPost(postId: string, following: boolean): Promise<{ post_id: string; following: boolean }> {
  return request(`/api/feed/${encodeURIComponent(postId)}/follow`, {
    method: 'POST',
    body: JSON.stringify({ following }),
  })
}

export async function saveFeedPost(postId: string, saved: boolean): Promise<{ post_id: string; saved: boolean }> {
  return request(`/api/feed/${encodeURIComponent(postId)}/save`, {
    method: 'POST',
    body: JSON.stringify({ saved }),
  })
}

export async function muteFeedPost(postId: string, muted: boolean): Promise<{ post_id: string; muted: boolean }> {
  return request(`/api/feed/${encodeURIComponent(postId)}/mute`, {
    method: 'POST',
    body: JSON.stringify({ muted }),
  })
}

export async function shareFeedPost(postId: string): Promise<ShareSnapshot> {
  return request<ShareSnapshot>(`/api/feed/${encodeURIComponent(postId)}/share`, { method: 'POST' })
}

export async function reportFeedPost(postId: string, reason: string, details?: string): Promise<void> {
  await request(`/api/feed/${encodeURIComponent(postId)}/report`, {
    method: 'POST',
    body: JSON.stringify({ reason, details }),
  })
}

export async function reportFeedComment(postId: string, commentId: string, reason: string, details?: string): Promise<void> {
  await request(`/api/feed/${encodeURIComponent(postId)}/comments/${encodeURIComponent(commentId)}/report`, {
    method: 'POST',
    body: JSON.stringify({ reason, details }),
  })
}

export async function editFeedComment(postId: string, commentId: string, body: string): Promise<CivicComment> {
  return request<CivicComment>(`/api/feed/${encodeURIComponent(postId)}/comments/${encodeURIComponent(commentId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ body }),
  })
}

export async function deleteFeedComment(postId: string, commentId: string): Promise<void> {
  await request<void>(`/api/feed/${encodeURIComponent(postId)}/comments/${encodeURIComponent(commentId)}`, { method: 'DELETE' })
}

export async function followSubject(subjectType: 'topic' | 'authority' | 'locality', value: string, following: boolean): Promise<FollowSubject> {
  return request<FollowSubject>('/api/subjects/follows', {
    method: 'POST',
    body: JSON.stringify({ subject_type: subjectType, value, following }),
  })
}

export async function getSubjectFollows(): Promise<FollowSubject[]> {
  const result = await request<{ items: FollowSubject[] }>('/api/subjects/follows')
  return result.items
}

export async function getLiveConnectors(): Promise<LiveEndpointProfile[]> {
  const result = await request<{ items: LiveEndpointProfile[] }>('/api/live/connectors')
  return result.items
}

export async function getNotifications(): Promise<CivicNotification[]> {
  const result = await request<{ items: CivicNotification[] }>('/api/notifications')
  return result.items
}

export async function markNotification(notificationId: string, read = true): Promise<void> {
  await request<void>(`/api/notifications/${encodeURIComponent(notificationId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ read }),
  })
}

export async function markAllNotificationsRead(): Promise<void> {
  await request<void>('/api/notifications/read-all', { method: 'POST' })
}

export async function getThreads(): Promise<AgentThread[]> {
  const result = await request<{ items: AgentThread[] }>('/api/agent/threads')
  return result.items
}

export async function createThread(input: { title?: string; goal?: string; caseId?: string }): Promise<AgentThreadDetail> {
  return request<AgentThreadDetail>('/api/agent/threads', {
    method: 'POST',
    body: JSON.stringify({ title: input.title, goal: input.goal, case_id: input.caseId }),
  })
}

export async function getThread(threadId: string): Promise<AgentThreadDetail> {
  return request<AgentThreadDetail>(`/api/agent/threads/${encodeURIComponent(threadId)}`)
}

export async function updateThread(threadId: string, input: { title?: string; status?: 'active' | 'waiting_for_user' | 'complete' | 'archived' }): Promise<AgentThreadDetail> {
  return request<AgentThreadDetail>(`/api/agent/threads/${encodeURIComponent(threadId)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export async function sendAgentMessage(threadId: string, content: string, attachmentIds: string[] = [], signal?: AbortSignal, clientMessageId?: string): Promise<AgentThreadDetail> {
  return request<AgentThreadDetail>(`/api/agent/threads/${encodeURIComponent(threadId)}/messages`, {
    method: 'POST',
    signal,
    body: JSON.stringify({ content, attachment_ids: attachmentIds, client_message_id: clientMessageId }),
  })
}

export async function sendAgentMessageStream(
  threadId: string,
  content: string,
  attachmentIds: string[] = [],
  onStatus?: (label: string) => void,
  signal?: AbortSignal,
  onEvent?: (event: AgentStreamEvent) => void,
  clientMessageId?: string,
): Promise<AgentThreadDetail> {
  const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream' })
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let response: Response
  try {
    response = await fetch(`${API_URL}/api/agent/threads/${encodeURIComponent(threadId)}/messages/stream`, {
      method: 'POST', headers, signal,
      body: JSON.stringify({ content, attachment_ids: attachmentIds, client_message_id: clientMessageId }),
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'The CivitasX service is unavailable. Your local draft is safe; start the API and try again.')
  }
  if (!response.ok) {
    if (response.status === 401) {
      clearToken()
      window.dispatchEvent(new Event('civitas:session-expired'))
    }
    let detail = `The request could not be completed (${response.status}).`
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* keep friendly status */ }
    throw new ApiError(response.status, response.status >= 500 ? 'The civic service is having trouble right now. Try again shortly.' : detail)
  }
  if (!response.body) return getThread(threadId)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const handleEvent = (event: string) => {
    const lines = event.split(/\r?\n/)
    const eventName = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() as AgentStreamEvent['event'] | undefined
    const dataLines = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).replace(/^ /, ''))
    if (!dataLines.length) return
    try {
      const payload = JSON.parse(dataLines.join('\n').trim() || '{}') as Record<string, unknown>
      if (eventName) onEvent?.({ event: eventName, data: payload })
      const label = typeof payload.label === 'string' ? payload.label : undefined
      if (label) onStatus?.(label)
    } catch { /* malformed progress event is ignored; the final thread is authoritative */ }
  }
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      buffer += decoder.decode(chunk.value, { stream: true })
      const events = buffer.split(/\r?\n\r?\n/)
      buffer = events.pop() ?? ''
      for (const event of events) handleEvent(event)
    }
    buffer += decoder.decode()
    if (buffer.trim()) handleEvent(buffer)
  } finally {
    reader.releaseLock()
  }
  return getThread(threadId)
}

export async function applyWorkspaceChange(
  threadId: string,
  proposalId: string,
): Promise<AgentThreadDetail> {
  return request<AgentThreadDetail>(
    `/api/agent/threads/${encodeURIComponent(threadId)}/workspace/apply`,
    {
      method: 'POST',
      body: JSON.stringify({ proposal_id: proposalId }),
    },
  )
}

export async function uploadAgentAttachment(threadId: string, file: File, signal?: AbortSignal): Promise<Attachment> {
  const token = getToken()
  const headers = new Headers()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let response: Response
  try {
    response = await fetch(`${API_URL}/api/agent/threads/${encodeURIComponent(threadId)}/attachments`, {
      method: 'POST',
      headers,
      signal,
      body: (() => { const form = new FormData(); form.append('file', file); return form })(),
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'The CivitasX service is unavailable. Is the local API running?')
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try { detail = ((await response.json()) as { detail?: string }).detail ?? detail } catch { /* keep status */ }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as Attachment
}

export async function deleteAgentAttachment(attachmentId: string): Promise<void> {
  await request<void>(`/api/agent/attachments/${encodeURIComponent(attachmentId)}`, { method: 'DELETE' })
}

export async function getThreadAttachments(threadId: string): Promise<Attachment[]> {
  const result = await request<{ items: Attachment[] }>(`/api/agent/threads/${encodeURIComponent(threadId)}/attachments`)
  return result.items
}

export async function createTicket(input: { threadId?: string; caseId?: string; title: string; description: string; authorityId?: string | null; locality?: string | null }): Promise<TicketDetail> {
  return request<TicketDetail>('/api/tickets', {
    method: 'POST',
    body: JSON.stringify({ thread_id: input.threadId, case_id: input.caseId, title: input.title, description: input.description, authority_id: input.authorityId, locality: input.locality, visibility: 'private' }),
  })
}

export async function getTicket(ticketId: string): Promise<TicketDetail> {
  return request<TicketDetail>(`/api/tickets/${encodeURIComponent(ticketId)}`)
}

export async function exportTicket(ticketId: string): Promise<string> {
  return requestText(`/api/tickets/${encodeURIComponent(ticketId)}/export`)
}

export async function updateTicketStatus(ticketId: string, status: TicketStatus, note?: string): Promise<TicketDetail> {
  return request<TicketDetail>(`/api/tickets/${encodeURIComponent(ticketId)}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status, note }),
  })
}

export async function publishTicket(ticketId: string, input: { title: string; body: string; locality?: string | null; visibility: 'nearby' | 'locality' | 'citywide'; displayName?: string; attachmentIds?: string[] }): Promise<CivicPost> {
  let redactionContentHash: string | undefined
  if (globalThis.crypto?.subtle) {
    const bytes = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${input.title}\n${input.body}`))
    redactionContentHash = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, '0')).join('')
  }
  return request<CivicPost>(`/api/tickets/${encodeURIComponent(ticketId)}/publish`, {
    method: 'POST',
    body: JSON.stringify({ title: input.title, body: input.body, locality: input.locality, visibility: input.visibility, display_name: input.displayName, redaction_approved: true, redaction_content_hash: redactionContentHash, attachment_ids: input.attachmentIds ?? [] }),
  })
}

export async function prepareTicket(ticketId: string, fields: Record<string, string> = {}, attachmentIds: string[] = []): Promise<TicketPreparation> {
  return request<TicketPreparation>(`/api/tickets/${encodeURIComponent(ticketId)}/prepare`, {
    method: 'POST',
    body: JSON.stringify({ fields, attachment_ids: attachmentIds }),
  })
}

export async function getTicketPreparation(ticketId: string): Promise<TicketPreparation> {
  return request<TicketPreparation>(`/api/tickets/${encodeURIComponent(ticketId)}/preparation`)
}

export async function approveTicketPreparation(ticketId: string, contentHash: string): Promise<PreparationApproval> {
  return request<PreparationApproval>(`/api/tickets/${encodeURIComponent(ticketId)}/preparation/approve`, {
    method: 'POST',
    body: JSON.stringify({ content_hash: contentHash }),
  })
}

export async function getTicketCheckpoints(ticketId: string): Promise<Checkpoint[]> {
  const result = await request<{ items: Checkpoint[] }>(`/api/tickets/${encodeURIComponent(ticketId)}/checkpoints`)
  return result.items
}

export async function recordTicketOutcome(ticketId: string, input: { status: string; externalReferenceId?: string; acknowledgement?: string; trackingUrl?: string; submittedContentHash?: string; note?: string }): Promise<TicketDetail> {
  return request<TicketDetail>(`/api/tickets/${encodeURIComponent(ticketId)}/outcome`, {
    method: 'POST',
    body: JSON.stringify({ status: input.status, external_reference_id: input.externalReferenceId, acknowledgement: input.acknowledgement, tracking_url: input.trackingUrl, submitted_content_hash: input.submittedContentHash, note: input.note }),
  })
}
