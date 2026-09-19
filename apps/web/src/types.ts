export type User = {
  id: string
  name: string
  email: string
  created_at?: string | null
}

export type CaseStatus = 'saved'

export type CivicCase = {
  id: string
  title: string
  goal: string
  notes: string
  status: CaseStatus
  created_at: string
  updated_at: string
  version: number
}

export type TaskEvent = {
  id: string
  case_id: string
  type: string
  message: string
  created_at: string
}

export type UsageSummary = {
  case_id: string
  estimated_cost_usd: number
  reserved_cost_usd: number
  input_tokens: number
  output_tokens: number
  browser_seconds: number
  items: Array<{
    id: string
    kind: string
    estimated_cost_usd: number
    created_at: string
  }>
}

export type AppConfig = {
  auth: {
    mode: 'local' | 'cognito'
    local_recovery_enabled?: boolean
    cognito_domain: string | null
    client_id: string | null
    region: string | null
    user_pool_id: string | null
  }
  capabilities: {
    phase: number
    research: boolean
    submission: boolean
    feed: boolean
    agent: boolean
    tickets: boolean
    attachments: boolean
    live_sources?: boolean
  }
  limits: {
    global_budget_usd: number
    browser_concurrency: number
    max_browser_actions_per_attempt: number
    max_browser_seconds_per_attempt: number
  }
}

export type SourceEvidence = {
  source_id: string
  title: string
  authority: string
  status: 'draft' | 'proposed' | 'adopted' | 'historical' | 'unknown'
  page: number | null
  published_at: string | null
  retrieved_at: string | null
  url: string | null
  passage: string
  original_passage: string | null
  translation_language: string | null
  content_hash: string | null
  source_kind: 'official' | 'official_reference' | 'uploaded' | 'fixture'
  extraction_method: string | null
  extraction_status: 'complete' | 'partial' | 'unreadable' | 'unknown'
}

export type AuthorityRecord = {
  authority_id: string
  name: string
  short_name: string
  responsibilities: string[]
  contact_route: string
  url: string
  verified_at: string
  source_ids: string[]
  status: 'current' | 'historical' | 'ambiguous' | 'unknown'
}

export type ResearchFact = {
  fact_id: string
  claim: string
  supporting_source_ids: string[]
  supporting_pages: number[]
  claim_status: 'supported' | 'conflicting' | 'insufficient'
}

export type ResearchAnswer = {
  query: string
  route: 'transport' | 'budget' | 'planning' | 'civic_service' | 'general'
  explanation: string
  facts: ResearchFact[]
  uncertainties: string[]
  sources: SourceEvidence[]
  authority_matches: AuthorityRecord[]
  coverage: {
    documents_considered: number
    pages_considered: number
    hits_returned: number
    retrieval_mode: 'hybrid_offline' | 'hybrid_bedrock' | 'keyword'
    source_quality: 'official' | 'official_reference' | 'mixed' | 'none'
    stale_source_ids: string[]
    unreadable_source_ids: string[]
    conflicts: string[]
    missing: string[]
  }
  calculations: Array<{ expression: string; result: string; source_ids: string[] }>
  checked_at: string
  generated_by: 'extractive' | 'bedrock-grounded'
}

export type ResearchHistory = {
  items: ResearchAnswer[]
}

export type AuthResponse = {
  user: User
  access_token: string
  token_type: 'bearer'
}

export type MessagePart = {
  type: 'text' | 'attachment' | 'citation' | 'action' | 'tool' | 'command' | 'status'
  text?: string | null
  attachment_id?: string | null
  data?: Record<string, unknown>
}

export type AgentMessage = {
  id: string
  thread_id: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  parts: MessagePart[]
  created_at: string
}

export type AgentThread = {
  id: string
  case_id: string | null
  title: string
  status: 'active' | 'waiting_for_user' | 'complete' | 'archived'
  created_at: string
  updated_at: string
  message_count: number
  ticket_id: string | null
}

export type AgentThreadDetail = {
  thread: AgentThread
  messages: AgentMessage[]
}

export type Attachment = {
  id: string
  thread_id: string | null
  case_id: string | null
  owner_id: string
  filename: string
  content_type: string
  size_bytes: number
  sha256: string
  storage_key: string
  visibility: 'private' | 'public_redacted'
  created_at: string
}

export type TicketStatus =
  | 'draft'
  | 'needs_information'
  | 'ready_for_review'
  | 'submitted'
  | 'awaiting_confirmation'
  | 'acknowledged'
  | 'in_progress'
  | 'resolved_pending_confirmation'
  | 'resolved'
  | 'not_solved'
  | 'reopened'
  | 'outcome_unknown'

export type ComplaintTicket = {
  id: string
  civitas_ticket_id: string
  thread_id: string | null
  case_id: string | null
  owner_id: string
  title: string
  description: string
  authority_id: string | null
  locality: string | null
  status: TicketStatus
  visibility: 'private' | 'nearby' | 'locality' | 'citywide'
  external_reference_id: string | null
  public_post_id: string | null
  created_at: string
  updated_at: string
}

export type TicketDetail = {
  ticket: ComplaintTicket
  history: Array<{
    id: string
    ticket_id: string
    status: TicketStatus
    note: string | null
    created_at: string
  }>
}

export type CivicPost = {
  id: string
  ticket_id: string
  civitas_ticket_id: string
  author_id: string | null
  author_name: string
  title: string
  body: string
  locality: string | null
  visibility: 'nearby' | 'locality' | 'citywide'
  status: TicketStatus
  authority_id: string | null
  authority_name: string | null
  vote_score: number
  upvotes: number
  downvotes: number
  comment_count: number
  evidence_count: number
  is_demo: boolean
  created_at: string
  updated_at: string
  user_vote: -1 | 0 | 1
  is_following: boolean
  is_owner: boolean
  is_saved: boolean
  is_muted: boolean
  is_locked: boolean
  moderation_state: 'visible' | 'hidden' | 'locked'
  ranking_score: number | null
  ranking_reasons: string[]
}

export type FeedPage = {
  items: CivicPost[]
  next_cursor: string | null
}

export type CivicComment = {
  id: string
  post_id: string
  author_id: string | null
  author_name: string
  body: string
  parent_id: string | null
  created_at: string
  updated_at: string
  is_demo: boolean
  is_owner: boolean
  moderation_state: 'visible' | 'hidden'
  is_deleted: boolean
}

export type CivicNotification = {
  id: string
  owner_id: string
  kind: string
  message: string
  post_id: string | null
  ticket_id: string | null
  read: boolean
  created_at: string
}

export type FollowSubject = {
  subject_type: 'topic' | 'authority' | 'locality'
  value: string
  following: boolean
  created_at: string
}

export type ShareSnapshot = {
  token: string
  post_id: string
  url: string
  expires_at: string
  revoked: boolean
}

export type TicketPreparation = {
  id: string
  ticket_id: string
  civitas_ticket_id: string
  authority_id: string | null
  authority_name: string | null
  contact_route: string | null
  intake_url: string | null
  destination: string | null
  fields: Record<string, string>
  required_fields: string[]
  missing_fields: string[]
  attachment_ids: string[]
  content_hash: string
  status: 'needs_information' | 'ready_for_review' | 'approved' | 'blocked'
  submission_enabled: boolean
  created_at: string
  updated_at: string
  approved_at: string | null
  approval_expires_at: string | null
}

export type PreparationApproval = {
  preparation_id: string
  ticket_id: string
  destination: string
  content_hash: string
  approved_at: string
  expires_at: string
  valid: boolean
  submission_enabled: boolean
}

export type Checkpoint = {
  id: string
  ticket_id: string
  preparation_id: string | null
  phase: 'prepared' | 'reviewed' | 'awaiting_user' | 'submission_started' | 'outcome_unknown' | 'complete'
  summary: string
  created_at: string
}

export type LiveEndpointProfile = {
  endpoint_id: string
  authority_id: string
  authority_name: string
  title: string
  url: string
  source_kind: 'official' | 'official_reference'
  transport: 'html' | 'pdf' | 'json' | 'csv' | 'api'
  status: 'ready' | 'requires_api_key' | 'approval_required' | 'consent_required' | 'browser_only' | 'offline' | 'disabled' | 'unknown'
  priority: 'P0' | 'P1' | 'P2'
  access_mode: 'public_read' | 'api_key' | 'approval_required' | 'consent_required' | 'browser_only' | 'documentation'
  capabilities: string[]
  docs_url: string | null
  refreshable: boolean
  consent_scope: string | null
  requires_env: string | null
  last_checked_at: string | null
  last_error: string | null
  source_ids: string[]
  source_extraction_status: 'complete' | 'partial' | 'unreadable' | 'unknown'
}
