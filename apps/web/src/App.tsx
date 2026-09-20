import { FormEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  addFeedComment,
  ApiError,
  clearToken,
  createThread,
  createTicket,
  deleteAgentAttachment,
  deleteFeedComment,
  editFeedComment,
  exportTicket,
  followSubject,
  getConfig,
  getFeedPage,
  getFeedPost,
  getFeedPostAttachments,
  downloadFeedPostAttachment,
  getFeedComments,
  getAuthorities,
  getSubjectFollows,
  getMe,
  getNotifications,
  getThread,
  getThreadAttachments,
  getThreads,
  getToken,
  login,
  exchangeCognitoCode,
  logout,
  publishTicket,
  reportFeedComment,
  followFeedPost,
  register,
  recoverPassword,
  sendAgentMessageStream,
  applyWorkspaceChange,
  markAllNotificationsRead,
  markNotification,
  updateThread,
  shareFeedPost,
  prepareTicket,
  approveTicketPreparation,
  getTicketPreparation,
  submitTicket,
  resumeAgentRun,
  cancelAgentRun,
  updateTicketStatus,
  uploadAgentAttachment,
  voteFeedPost,
  confirmCognitoRegistration,
  confirmCognitoPasswordReset,
  loginWithCognito,
  registerWithCognito,
  startCognitoPasswordReset,
} from './api'
import type { CognitoClientConfig } from './api'
import type {
  AgentRun,
  AgentMessage,
  AgentLocation,
  AgentThread,
  AgentThreadDetail,
  AppConfig,
  Attachment,
  CivicComment,
  CivicPost,
  CivicNotification,
  MessagePart,
  PublicPostAttachment,
  TicketDetail,
  TicketPreparation,
  TicketStatus,
  User,
  AuthorityRecord,
} from './types'

type Surface = 'feed' | 'agent' | 'analytics'
type FeedSortValue = 'recent' | 'popular' | 'nearby' | 'following' | 'recommended'
type AnalyticsScope = 'organization' | 'office-holder'
type AnalyticsPeriod = '12m' | '90d' | 'all'

const FEED_SORT_OPTIONS: Array<{ value: FeedSortValue; label: string }> = [
  { value: 'recommended', label: 'Best' },
  { value: 'recent', label: 'Recent' },
  { value: 'popular', label: 'Popular' },
  { value: 'nearby', label: 'Nearby' },
  { value: 'following', label: 'Following' },
]

function feedSortFromQuery(value: string | null): FeedSortValue | null {
  return FEED_SORT_OPTIONS.some((option) => option.value === value) ? value as FeedSortValue : null
}

function surfaceFromLocation(): Surface {
  if (window.location.pathname.startsWith('/analytics')) return 'analytics'
  return window.location.pathname.startsWith('/agent') ? 'agent' : 'feed'
}

function threadFromLocation(): string | null {
  return new URLSearchParams(window.location.hash.replace(/^#/, '')).get('thread')
}

function feedPostFromLocation(): string | null {
  const match = window.location.pathname.match(/^\/feed\/post\/([^/]+)$/)
  if (match) {
    try { return decodeURIComponent(match[1]) } catch { return match[1] }
  }
  return new URLSearchParams(window.location.hash.replace(/^#/, '')).get('post')
}

function readFeedSearchQuery(): string {
  return new URLSearchParams(window.location.search).get('q') ?? ''
}

const LOCALITIES = ['All Bengaluru', 'Indiranagar', 'Jayanagar', 'Malleshwaram', 'Koramangala', 'Whitefield', 'Hebbal', 'HSR Layout', 'Bellandur', 'Marathahalli', 'Electronic City', 'BTM Layout', 'Yelahanka', 'Rajajinagar', 'Banashankari', 'Kalyan Nagar', 'KR Puram', 'JP Nagar', 'Nagarbhavi', 'Vijayanagar']
const TICKET_STATUS_OPTIONS: Array<{ value: TicketStatus; label: string }> = [
  { value: 'draft', label: 'Draft' }, { value: 'needs_information', label: 'Needs information' }, { value: 'ready_for_review', label: 'Ready for review' },
  { value: 'submitted', label: 'Submitted' }, { value: 'awaiting_confirmation', label: 'Awaiting confirmation' }, { value: 'acknowledged', label: 'Acknowledged' },
  { value: 'in_progress', label: 'In progress' }, { value: 'resolved_pending_confirmation', label: 'Resolved pending confirmation' }, { value: 'resolved', label: 'Resolved' },
  { value: 'not_solved', label: 'Not solved' }, { value: 'reopened', label: 'Reopened' }, { value: 'outcome_unknown', label: 'Outcome unknown' },
]
const AUTHORITY_NAMES: Record<string, string> = {
  gba: 'Greater Bengaluru Authority',
  bda: 'Bangalore Development Authority',
  bmrcl: 'Bangalore Metro Rail Corporation Limited',
}

const starterPrompts = [
  'What is the official record for safer walking routes near my locality?',
  'There is a broken streetlight near Indiranagar. Help me file a complaint.',
  'Explain this government notice and tell me which authority handles it.',
]

type ProfilePreferences = { locality?: string; displayName?: string; remember?: boolean; activityNotifications?: boolean; onboardingSeen?: boolean }

function readStorageValue(key: string): string | null {
  try {
    const value = window.localStorage.getItem(key)
    if (value) return value
  } catch { /* try session storage below */ }
  try { return window.sessionStorage.getItem(key) } catch { return null }
}

function readStoredProfilePreferences(userId: string): ProfilePreferences {
  const key = `civitas.profile.preferences.${userId}`
  try {
    const raw = readStorageValue(key)
    return raw ? JSON.parse(raw) as ProfilePreferences : {}
  } catch { return {} }
}

function readFeedPreferences(userId: string): { sort?: 'recent' | 'popular' | 'nearby' | 'following' | 'recommended'; locality?: string } {
  try {
    const key = `civitas.feed.preferences.${userId}`
    const raw = readStorageValue(key)
    if (!raw) return {}
    const value = JSON.parse(raw) as { sort?: string; locality?: string }
    const sort = value.sort === 'popular' || value.sort === 'nearby' || value.sort === 'following' || value.sort === 'recent' || value.sort === 'recommended' ? value.sort : undefined
    return { sort, locality: typeof value.locality === 'string' ? value.locality : undefined }
  } catch { return {} }
}

function readProfileLocality(userId: string): string | undefined {
  const value = readStoredProfilePreferences(userId)
  return value.locality || undefined
}

function readActivityPreference(userId: string): boolean {
  const value = readStoredProfilePreferences(userId)
  return value.activityNotifications !== false
}

async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return
  }
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  textarea.remove()
  if (!copied) throw new Error('Copy is unavailable in this browser')
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value))
}

function formatPostAge(value: string): string {
  const postedAt = new Date(value)
  if (Number.isNaN(postedAt.getTime())) return formatDate(value)

  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const startOfPostedDay = new Date(postedAt)
  startOfPostedDay.setHours(0, 0, 0, 0)
  const daysOld = Math.floor((startOfToday.getTime() - startOfPostedDay.getTime()) / 86_400_000)

  if (daysOld <= 0) return 'today'
  if (daysOld === 1) return 'yesterday'
  return `${daysOld} days old`
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { hour: 'numeric', minute: '2-digit' }).format(new Date(value))
}

function initials(name: string): string {
  return name.split(/\s+/).map((part) => part[0]).join('').slice(0, 2).toUpperCase()
}

function statusLabel(status: TicketStatus): string {
  return status.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function withoutRedditSource(value: string): string {
  return value
    .replace(/\s*submitted by\s+\/u\/\S+\s*(?:\[link\]\s*\[comments\])?/gi, '')
    .replace(/\s*(?:Source:\s*)?https?:\/\/(?:www\.)?(?:reddit\.com|redd\.it)\/\S+/gi, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function redactPreview(value: string): string {
  return value
    .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, '[redacted email]')
    .replace(/(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)/g, '[redacted phone]')
    .replace(/\b(?:aadhaar|passport|pan)\s*[:#-]?\s*[A-Za-z0-9 -]{6,20}\b/gi, '[redacted ID]')
}

function detectRedactionRisks(title: string, body: string): string[] {
  const combined = `${title}\n${body}`
  const risks: string[] = []
  if (/\b(?:\d{6}|\d{4}[ -]?\d{4}[ -]?\d{4})\b/.test(combined)) risks.push('Possible government ID or numeric identifier')
  if (/\b(?:flat|house|door|plot|survey|parcel|address|road|street|lane)\b/i.test(combined)) risks.push('Exact location or address')
  if (/\b(?:signature|signed|receipt|invoice|account|bank|upi|ifsc)\b/i.test(combined)) risks.push('Signature, receipt, or financial detail')
  if (/\b(?:face|photo|image|child|minor)\b/i.test(combined)) risks.push('Photo may show a face or vulnerable person')
  if (risks.length === 0) risks.push('Review the wording for names, faces, IDs, exact locations, and private attachments')
  return risks
}

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [user, setUser] = useState<User | null>(null)
  const [surface, setSurface] = useState<Surface>(() => surfaceFromLocation())
  const [threads, setThreads] = useState<AgentThread[]>([])
  const [activeThreadId, setActiveThreadId] = useState<string | null>(() => threadFromLocation())
  const [activePostId, setActivePostId] = useState<string | null>(() => feedPostFromLocation())
  const [agentPrompt, setAgentPrompt] = useState<string | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  const [authNotice, setAuthNotice] = useState<string | null>(null)
  const [isBooting, setIsBooting] = useState(true)

  useEffect(() => {
    const root = document.documentElement
    const keyboardNavigationKeys = new Set([
      'Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
      'Home', 'End', 'PageUp', 'PageDown', 'Escape',
    ])
    const markKeyboardNavigation = (event: KeyboardEvent) => {
      if (keyboardNavigationKeys.has(event.key)) root.dataset.inputModality = 'keyboard'
    }
    const clearKeyboardNavigation = () => {
      delete root.dataset.inputModality
    }
    window.addEventListener('keydown', markKeyboardNavigation, true)
    window.addEventListener('pointerdown', clearKeyboardNavigation, true)
    window.addEventListener('touchstart', clearKeyboardNavigation, true)
    return () => {
      window.removeEventListener('keydown', markKeyboardNavigation, true)
      window.removeEventListener('pointerdown', clearKeyboardNavigation, true)
      window.removeEventListener('touchstart', clearKeyboardNavigation, true)
    }
  }, [])

  const refreshThreads = useCallback(async () => {
    const nextThreads = await getThreads()
    setThreads(nextThreads)
    return nextThreads
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const nextConfig = await getConfig()
        if (cancelled) return
        setConfig(nextConfig)
        if (getToken()) {
          try {
            const nextUser = await getMe()
            if (!cancelled) {
              setUser(nextUser)
              await refreshThreads()
            }
          } catch (error) {
            if (error instanceof ApiError && error.status === 401) clearToken()
            else throw error
          }
        }
      } catch (error) {
        if (!cancelled) setBootError(error instanceof Error ? error.message : 'Unable to start CivitasX')
      } finally {
        if (!cancelled) setIsBooting(false)
      }
    })()
    return () => { cancelled = true }
  }, [refreshThreads])

  useEffect(() => {
    const expireSession = () => {
      setUser(null)
      setThreads([])
      setActiveThreadId(null)
      setActivePostId(null)
      window.history.replaceState({}, '', '/')
      setAuthNotice('Your session expired. Sign in again to continue; local drafts remain on this device.')
    }
    window.addEventListener('civitas:session-expired', expireSession)
    return () => window.removeEventListener('civitas:session-expired', expireSession)
  }, [])

  const handleAuth = async (nextUser: User) => {
    setUser(nextUser)
    setBootError(null)
    setAuthNotice(null)
    await refreshThreads()
  }

  const handleSignOut = async () => {
    let signOutWarning: string | null = null
    try { await logout() } catch (error) { signOutWarning = error instanceof Error ? error.message : 'The server could not confirm sign out.' }
    setUser(null)
    setThreads([])
    setActiveThreadId(null)
    setActivePostId(null)
    window.history.replaceState({}, '', '/')
    setAuthNotice(signOutWarning ? `${signOutWarning} You are signed out on this device.` : null)
  }

  const navigateSurface = (nextSurface: Surface) => {
    if (nextSurface === 'agent' && surface !== 'agent') {
      setActiveThreadId(null)
      setAgentPrompt(null)
    }
    setSurface(nextSurface)
    setActivePostId(null)
    const nextPath = nextSurface === 'agent' ? '/agent' : nextSurface === 'analytics' ? '/analytics' : '/feed'
    const currentHash = window.location.hash
    const nextHash = nextSurface === 'agent'
      ? (currentHash.startsWith('#thread=') ? currentHash : '')
      : ''
    if (window.location.pathname !== nextPath || currentHash !== nextHash) window.history.pushState({}, '', `${nextPath}${window.location.search}${nextHash}`)
  }

  useEffect(() => {
    const handleNavigation = () => {
      setSurface(surfaceFromLocation())
      setActiveThreadId(threadFromLocation())
      setActivePostId(feedPostFromLocation())
    }
    window.addEventListener('popstate', handleNavigation)
    return () => window.removeEventListener('popstate', handleNavigation)
  }, [])

  useEffect(() => {
    if (user && window.location.pathname === '/' && !window.location.hash) {
      const nextPath = surface === 'agent' ? '/agent' : surface === 'analytics' ? '/analytics' : '/feed'
      window.history.replaceState({}, '', `${nextPath}${window.location.search}`)
    }
  }, [surface, user])

  useEffect(() => {
    if (!user) {
      document.title = window.location.pathname === '/explore' ? 'CivitasX — Explore a sample issue' : 'CivitasX — Your civic record'
      return
    }
    document.title = surface === 'agent'
      ? 'CivitasX — Private Agent'
      : surface === 'analytics'
        ? 'CivitasX — Public Accountability'
        : 'CivitasX — Public civic feed'
  }, [surface, user])

  const capabilities = config?.capabilities ?? { phase: 0, research: false, submission: false, feed: false, agent: false, tickets: false, attachments: false, live_sources: false }
  useEffect(() => {
    if (capabilities && surface === 'feed' && !capabilities.feed && capabilities.agent) setSurface('agent')
    if (capabilities && surface === 'agent' && !capabilities.agent && capabilities.feed) setSurface('feed')
  }, [capabilities, surface])

  if (isBooting) return <LoadingScreen label="Opening the civic network" />
  if (bootError) return <StartupError message={bootError} onRetry={() => window.location.reload()} />
  if (!user || !config || !capabilities) return <AuthScreen config={config} onAuthenticated={handleAuth} notice={authNotice} />

  return (
    <div className={`app-shell ${surface === 'agent' ? 'agent-surface' : ''} ${surface === 'analytics' ? 'analytics-surface' : ''}`}>
      <ScoreRail surface={surface} onSurface={navigateSurface} capabilities={config.capabilities} />
      <main className="app-main">
        <TopBar user={user} surface={surface} onSurface={navigateSurface} onSignOut={() => void handleSignOut()} />
        <GlobalSearch surface={surface} onSurface={navigateSurface} />
        {surface === 'feed' && capabilities.feed && <FeedSortMenu user={user} />}
        {surface === 'analytics' ? <AnalyticsView /> : surface === 'feed' ? capabilities.feed ? (
          <FeedView user={user} capabilities={capabilities} postId={activePostId} onOpenPost={(postId) => { setSurface('feed'); setActivePostId(postId); window.history.pushState({}, '', `/feed/post/${encodeURIComponent(postId)}${window.location.search}`) }} onClosePost={() => { setActivePostId(null); window.history.replaceState({}, '', `/feed${window.location.search}`) }} onOpenAgent={(prompt) => { navigateSurface('agent'); setAgentPrompt(prompt ?? null); setActiveThreadId(null) }} />
        ) : <CapabilityUnavailable label="The public Feed is not enabled for this deployment." /> : capabilities.agent ? (
          <AgentView
            capabilities={capabilities}
            ownerId={user.id}
            ownerName={user.name}
            threads={threads}
            activeThreadId={activeThreadId}
            initialPrompt={agentPrompt}
            onInitialPromptConsumed={() => setAgentPrompt(null)}
            onThreadsChanged={refreshThreads}
            onSelectThread={setActiveThreadId}
          />
        ) : (
          <CapabilityUnavailable label="The private Agent is not enabled for this deployment." />
        )}
      </main>
    </div>
  )
}

function LoadingScreen({ label }: { label: string }) {
  return <main className="full-screen-state" role="status" aria-live="polite"><div className="loading-mark" aria-hidden="true"><span /><span /><span /></div><p className="eyebrow">CIVITASX / LOCAL NETWORK</p><h1>{label}</h1><p className="muted">Your civic work stays on this local record.</p></main>
}

function StartupError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <main className="full-screen-state error-state" role="alert"><div className="state-symbol" aria-hidden="true">!</div><p className="eyebrow">CIVITASX / SERVICE PAUSED</p><h1>We could not open the network.</h1><p className="muted">{message}</p><button className="button button-dark" onClick={onRetry}>Try again</button></main>
}

function AuthScreen({ config, onAuthenticated, notice }: { config: AppConfig | null; onAuthenticated: (user: User) => Promise<void>; notice?: string | null }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showPreview, setShowPreview] = useState(() => window.location.pathname === '/explore')
  const [recoveryOpen, setRecoveryOpen] = useState(false)
  const [recoveryNotice, setRecoveryNotice] = useState<string | null>(null)
  const [cognitoConfirmationOpen, setCognitoConfirmationOpen] = useState(false)
  const [confirmationCode, setConfirmationCode] = useState('')
  const [isWorking, setIsWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const cognitoReady = config?.auth.mode === 'cognito' && Boolean(config.auth.cognito_domain && config.auth.client_id)
  const cognitoClientConfig: CognitoClientConfig = {
    userPoolId: config?.auth.user_pool_id ?? null,
    clientId: config?.auth.client_id ?? null,
  }
  const cognitoClientReady = config?.auth.mode === 'cognito' && Boolean(cognitoClientConfig.userPoolId && cognitoClientConfig.clientId)
  // Keep one stable OAuth callback so every SPA route is accepted by Cognito.
  const cognitoRedirectUri = window.location.origin
  const recoveryEnabled = config?.auth.local_recovery_enabled === true

  useEffect(() => {
    if (!cognitoReady) return
    const code = new URLSearchParams(window.location.search).get('code')
    if (!code) return
    setIsWorking(true)
    void exchangeCognitoCode(code, cognitoRedirectUri)
      .then(onAuthenticated)
      .catch((nextError) => setError(nextError instanceof Error ? nextError.message : 'Identity provider sign-in failed'))
      .finally(() => { setIsWorking(false); window.history.replaceState({}, '', window.location.pathname) })
  }, [cognitoReady, cognitoRedirectUri, onAuthenticated])

  const switchMode = (nextMode: 'login' | 'register') => {
    setMode(nextMode)
    setError(null)
    setRecoveryOpen(false)
    setRecoveryNotice(null)
    setCognitoConfirmationOpen(false)
    setConfirmationCode('')
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setIsWorking(true)
    setError(null)
    try {
      if (config?.auth.mode === 'cognito') {
        if (!cognitoClientReady) throw new Error('Cognito is selected, but the account service is not configured yet.')
        if (mode === 'register' && cognitoConfirmationOpen) {
          await confirmCognitoRegistration(email, confirmationCode, cognitoClientConfig)
          const nextUser = await loginWithCognito(email, password, cognitoClientConfig)
          await onAuthenticated(nextUser)
        } else if (mode === 'login') {
          const nextUser = await loginWithCognito(email, password, cognitoClientConfig)
          await onAuthenticated(nextUser)
        } else {
          const result = await registerWithCognito(name, email, password, cognitoClientConfig)
          if (result.userConfirmed) {
            const nextUser = await loginWithCognito(email, password, cognitoClientConfig)
            await onAuthenticated(nextUser)
          } else {
            setCognitoConfirmationOpen(true)
            setRecoveryNotice(`We sent a verification code to ${result.destination || email}.`)
          }
        }
      } else {
        const nextUser = mode === 'login' ? await login(email, password) : await register(name, email, password)
        await onAuthenticated(nextUser)
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Authentication failed')
    } finally {
      setIsWorking(false)
    }
  }

  useEffect(() => {
    const handleNavigation = () => setShowPreview(window.location.pathname === '/explore')
    window.addEventListener('popstate', handleNavigation)
    return () => window.removeEventListener('popstate', handleNavigation)
  }, [])

  const openPreview = () => {
    window.history.pushState({}, '', '/explore')
    setShowPreview(true)
  }
  const closePreview = () => {
    window.history.replaceState({}, '', '/')
    setShowPreview(false)
  }

  if (showPreview) return <SampleCivicPreview onBack={closePreview} onSignIn={closePreview} />

  return (
    <main className="auth-shell">
      <div className="auth-aside">
        <div className="brand-lockup"><BrandMark /><span>CivitasX</span></div>
        <div className="auth-aside-copy"><div className="auth-copy-text"><p className="eyebrow">The civic network</p><h1>See what needs attention.</h1><p className="auth-description">Find a local issue, check the record, and track what happens next.</p></div><div className="auth-hero-actions"><a className="button button-lime button-full auth-mobile-auth-link" href="#auth-card" onClick={(event) => { event.preventDefault(); window.history.replaceState({}, '', window.location.pathname + window.location.search); document.getElementById('auth-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }}>Sign in or create account <span aria-hidden="true">↓</span></a><button type="button" className="button button-outline button-on-ink auth-sample-link" onClick={openPreview}>Explore a sample issue <span aria-hidden="true">↗</span></button></div></div>
      </div>
      <div id="auth-card" className="auth-card-wrap">
        <div className="auth-card">
          {notice && <div className="inline-success" role="status">{notice}</div>}
          <>
            <div className="auth-tabs" role="group" aria-label="Account access"><button type="button" className={mode === 'login' ? 'active' : ''} aria-pressed={mode === 'login'} onClick={() => switchMode('login')}>Sign in</button><button type="button" className={mode === 'register' ? 'active' : ''} aria-pressed={mode === 'register'} onClick={() => switchMode('register')}>Create account</button></div>
            <div className={`auth-panel auth-panel-${recoveryOpen ? 'recovery' : mode}`} aria-label={recoveryOpen ? 'Password recovery form' : mode === 'login' ? 'Sign in form' : 'Create account form'}>
              <div key={`${mode}-${recoveryOpen ? 'recovery' : cognitoConfirmationOpen ? 'confirmation' : 'form'}`} className="auth-panel-content">
                {recoveryOpen && mode === 'login' && config?.auth.mode === 'cognito' ? <CognitoRecoveryForm initialEmail={email} config={cognitoClientConfig} onCancel={() => { setRecoveryOpen(false); setError(null) }} onAuthenticated={onAuthenticated} /> : recoveryOpen && mode === 'login' && recoveryEnabled ? <LocalRecoveryForm initialEmail={email} onCancel={() => setRecoveryOpen(false)} onAuthenticated={onAuthenticated} /> : cognitoConfirmationOpen && mode === 'register' && config?.auth.mode === 'cognito' ? <form onSubmit={(event) => void submit(event)} className="auth-form recovery-form">
                  <div className="recovery-heading"><p className="eyebrow">Verify your email</p><h3>Finish creating your account.</h3><p>Enter the verification code sent to {email}.</p></div>
                  <label htmlFor="auth-confirmation-code">Verification code<input id="auth-confirmation-code" name="confirmation_code" value={confirmationCode} onChange={(event) => setConfirmationCode(event.target.value)} autoComplete="one-time-code" inputMode="numeric" required maxLength={12} placeholder="Enter code" /></label>
                  {error && <div className="form-error" role="alert">{error}</div>}
                  <button className="button button-lime button-full" disabled={isWorking}>{isWorking ? 'Opening…' : 'Verify and sign in'}</button>
                  <button type="button" className="recovery-link" onClick={() => { setCognitoConfirmationOpen(false); setRecoveryNotice(null); setError(null) }}>Back to create account</button>
                </form> : <>
                <form onSubmit={(event) => void submit(event)} className="auth-form">
                  {mode === 'register' && <label htmlFor="auth-name">Name<input id="auth-name" name="name" value={name} onChange={(event) => setName(event.target.value)} autoComplete="name" required maxLength={120} placeholder="Your name" /></label>}
                  <label htmlFor="auth-email">Email<input id="auth-email" name="email" value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" required maxLength={254} placeholder="you@example.com" /></label>
                  <label htmlFor="auth-password">Password<div className="password-field"><input id="auth-password" name="password" value={password} onChange={(event) => setPassword(event.target.value)} type={showPassword ? 'text' : 'password'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required minLength={mode === 'login' ? 1 : 12} maxLength={256} aria-describedby={mode === 'register' ? 'password-help' : undefined} placeholder={mode === 'login' ? 'Your password' : '12 characters or more'} /><button type="button" className="password-toggle" aria-label={showPassword ? 'Hide password' : 'Show password'} title={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword} onClick={() => setShowPassword((current) => !current)}><PasswordVisibilityIcon visible={showPassword} /></button></div>{mode === 'register' && <small id="password-help">Use at least 12 characters. Your password stays with this local pilot account.</small>}</label>
                  {mode === 'login' && (config?.auth.mode === 'cognito' || recoveryEnabled) && <button type="button" className="recovery-link" onClick={() => { setRecoveryOpen(true); setError(null); setRecoveryNotice(null) }}>Forgot password?</button>}
                  {error && <div className="form-error" role="alert">{error}</div>}
                    <button className="button button-lime button-full auth-submit" disabled={isWorking}>{isWorking ? 'Opening…' : mode === 'login' ? 'Sign in' : 'Create account'}</button>
                </form>
                {recoveryNotice && <div className="auth-help" role="status">{recoveryNotice}</div>}
                </>}
              </div>
            </div>
          </>
        </div>
      </div>
    </main>
  )
}

function CognitoRecoveryForm({ initialEmail, config, onCancel, onAuthenticated }: { initialEmail: string; config: CognitoClientConfig; onCancel: () => void; onAuthenticated: (user: User) => Promise<void> }) {
  const [email, setEmail] = useState(initialEmail)
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [codeSent, setCodeSent] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (codeSent && password !== confirmation) { setError('The new passwords do not match.'); return }
    setWorking(true)
    setError(null)
    try {
      if (!codeSent) {
        const result = await startCognitoPasswordReset(email, config)
        setCodeSent(true)
        setNotice(`We sent a verification code to ${result.destination || email}.`)
      } else {
        await confirmCognitoPasswordReset(email, code, password, config)
        const user = await loginWithCognito(email, password, config)
        await onAuthenticated(user)
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Could not reset this password.')
    } finally {
      setWorking(false)
    }
  }

  return <form className="auth-form recovery-form" onSubmit={(event) => void submit(event)}><div className="recovery-heading"><p className="eyebrow">Account recovery</p><h3>Reset your password.</h3><p>{codeSent ? 'Enter the verification code and choose a new password.' : 'Enter your email and we will send you a verification code.'}</p></div><label htmlFor="reset-email">Email<input id="reset-email" name="email" value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" required maxLength={254} /></label>{codeSent && <><label htmlFor="reset-code">Verification code<input id="reset-code" name="code" value={code} onChange={(event) => setCode(event.target.value)} autoComplete="one-time-code" inputMode="numeric" required maxLength={12} /></label><label htmlFor="reset-password">New password<input id="reset-password" name="password" value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="new-password" required minLength={12} maxLength={256} aria-describedby="reset-password-help" /></label><small id="reset-password-help">Use at least 12 characters.</small><label htmlFor="reset-confirmation">Confirm new password<input id="reset-confirmation" name="confirmation" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} type="password" autoComplete="new-password" required minLength={12} maxLength={256} /></label></>}{error && <div className="form-error" role="alert">{error}</div>}{notice && <div className="auth-help" role="status">{notice}</div>}<button className="button button-lime button-full" disabled={working}>{working ? 'Resetting…' : codeSent ? 'Reset password and sign in' : 'Send reset code'}</button><button type="button" className="recovery-link" onClick={onCancel}>Back to sign in</button></form>
}

function LocalRecoveryForm({ initialEmail, onCancel, onAuthenticated }: { initialEmail: string; onCancel: () => void; onAuthenticated: (user: User) => Promise<void> }) {
  const [email, setEmail] = useState(initialEmail)
  const [recoveryCode, setRecoveryCode] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (password !== confirmation) { setError('The new passwords do not match.'); return }
    setWorking(true)
    setError(null)
    try {
      const user = await recoverPassword(email, recoveryCode, password)
      await onAuthenticated(user)
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Could not recover this account')
    } finally { setWorking(false) }
  }

  return <form className="auth-form recovery-form" onSubmit={(event) => void submit(event)}><div className="recovery-heading"><p className="eyebrow">Local pilot recovery</p><h3>Reset your password.</h3><p>Use the recovery code provided by the pilot administrator. The code is never shown or stored in the browser.</p></div><label htmlFor="recovery-email">Email<input id="recovery-email" name="email" value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" required maxLength={254} /></label><label htmlFor="recovery-code">Pilot recovery code<input id="recovery-code" name="recovery_code" value={recoveryCode} onChange={(event) => setRecoveryCode(event.target.value)} type="password" autoComplete="one-time-code" required minLength={8} maxLength={256} /></label><label htmlFor="recovery-password">New password<input id="recovery-password" name="new_password" value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="new-password" required minLength={12} maxLength={256} aria-describedby="recovery-password-help" /></label><small id="recovery-password-help">Use at least 12 characters.</small><label htmlFor="recovery-confirmation">Confirm new password<input id="recovery-confirmation" name="new_password_confirmation" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} type="password" autoComplete="new-password" required minLength={12} maxLength={256} /></label>{error && <div className="form-error" role="alert">{error}</div>}<button className="button button-lime button-full" disabled={working}>{working ? 'Resetting…' : 'Reset password and sign in'}</button><button type="button" className="recovery-link" onClick={onCancel}>Back to sign in</button></form>
}

function SampleCivicPreview({ onBack, onSignIn }: { onBack: () => void; onSignIn: () => void }) {
  return <main className="sample-preview-shell"><header className="sample-preview-header"><div className="brand-lockup"><BrandMark /><span>CivitasX</span></div><button type="button" className="button button-outline" onClick={onBack}>Back to sign in</button></header><section className="sample-preview-intro"><p className="eyebrow">A read-only Bengaluru example</p><h1>Understand an issue<br /><em>before you act.</em></h1><p>A civic signal, its evidence, and what to do next.</p></section><div className="sample-preview-grid"><article className="sample-signal-card"><div className="sample-signal-top"><span className="sample-chip">SAMPLE · NOT OFFICIAL</span><span className="status-chip working"><span className="dot dot-working" /> In progress</span></div><p className="eyebrow">Indiranagar · public civic signal</p><h2>Streetlight outage on 12th Main</h2><p>Residents report a dark stretch between 12th Main and the park entrance.</p><div className="sample-signal-meta"><span>Greater Bengaluru Authority</span><span>CivitasX ticket · CX-BLR-DEMO-0001</span></div><section className="sample-evidence"><div><span className="eyebrow">Evidence trail</span><strong>1 source passage checked</strong></div><p>Pilot source: ward-level road safety and public lighting programme. Verify the current authority route before submission.</p><small>Official reference · submission not confirmed</small></section></article><aside className="sample-preview-aside"><p className="eyebrow">How CivitasX works</p><ol><li><strong>Find</strong><span>Browse nearby issues.</span></li><li><strong>Check</strong><span>Ask Agent to explain the record and show its sources.</span></li><li><strong>Track</strong><span>Keep a private ticket and follow what happens next.</span></li></ol><button type="button" className="button button-lime button-full" onClick={onSignIn}>Sign in to save an issue <span aria-hidden="true">↗</span></button><p className="sample-preview-note">Seeded sample data — not a government acknowledgement or resolution.</p></aside></div></main>
}

function CapabilityUnavailable({ label }: { label: string }) {
  return <main className="full-screen-state capability-state"><div className="state-symbol" aria-hidden="true">·</div><p className="eyebrow">CIVITASX / CAPABILITY PAUSED</p><h1>Not enabled in this phase.</h1><p className="muted">{label}</p></main>
}

function ScoreRail({ surface, onSurface, capabilities }: { surface: Surface; onSurface: (surface: Surface) => void; capabilities: AppConfig['capabilities'] }) {
  return <aside className="score-rail" aria-label="CivitasX navigation"><button className="rail-brand" onClick={() => capabilities.feed ? onSurface('feed') : onSurface('analytics')} aria-label="Open Civic Feed" disabled={!capabilities.feed && surface !== 'analytics'}><BrandMark /></button><div className="rail-spine" aria-hidden="true"><span className={`rail-node ${surface === 'feed' ? 'active' : ''}`} /><span className="rail-line" /><span className={`rail-node ${surface === 'agent' ? 'active' : ''}`} /></div><nav className="rail-nav" aria-label="Primary">{capabilities.feed && <button type="button" aria-label="Open Feed" aria-current={surface === 'feed' ? 'page' : undefined} className={surface === 'feed' ? 'active' : ''} onClick={() => onSurface('feed')}><img className="rail-nav-art" src="/navigation/feed-button.svg" alt="" aria-hidden="true" /></button>}{capabilities.agent && <button type="button" aria-label="Open Agent" aria-current={surface === 'agent' ? 'page' : undefined} className={surface === 'agent' ? 'active' : ''} onClick={() => onSurface('agent')}><img className="rail-nav-art" src="/navigation/agent-button.svg" alt="" aria-hidden="true" /></button>}<button type="button" aria-label="Open Analytics" aria-current={surface === 'analytics' ? 'page' : undefined} className={`analytics-nav ${surface === 'analytics' ? 'active' : ''}`} onClick={() => onSurface('analytics')}><img className="rail-nav-art" src="/navigation/analytics-button.svg" alt="" aria-hidden="true" /></button></nav><div className="rail-bottom"><span className="rail-version">V0.3</span></div></aside>
}

function GlobalSearch({ surface, onSurface }: { surface: Surface; onSurface: (surface: Surface) => void }) {
  const [query, setQuery] = useState(readFeedSearchQuery)
  const [topbar, setTopbar] = useState<HTMLElement | null>(null)

  useEffect(() => {
    setTopbar(document.querySelector<HTMLElement>('.topbar'))
  }, [])

  useEffect(() => {
    const handleNavigation = () => setQuery(readFeedSearchQuery())
    window.addEventListener('popstate', handleNavigation)
    return () => window.removeEventListener('popstate', handleNavigation)
  }, [])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const params = new URLSearchParams(window.location.search)
    const nextQuery = query.trim()
    if (nextQuery) params.set('q', nextQuery)
    else params.delete('q')
    if (surface !== 'feed') onSurface('feed')
    const nextPath = `/feed${params.toString() ? `?${params.toString()}` : ''}`
    window.history.pushState({}, '', nextPath)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }

  const form = <form className="global-search" role="search" onSubmit={submit}><SearchIcon /><label className="sr-only" htmlFor="global-search-input">Search civic issues</label><input id="global-search-input" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={surface === 'analytics' ? 'Search issues, offices or areas…' : 'Search civic issues'} autoComplete="off" /></form>
  return topbar ? createPortal(form, topbar) : null
}

type AnalyticsTrendPoint = { label: string; filed: number; resolved: number }
type AnalyticsComparisonRow = { name: string; filed: string; resolved: string; satisfaction: number; resolvedRate: number; medianDays: number }

const ANALYTICS_PERIOD_OPTIONS: Array<{ value: AnalyticsPeriod; label: string }> = [
  { value: '12m', label: 'Last 12 months' },
  { value: '90d', label: 'Last 90 days' },
  { value: 'all', label: 'All time' },
]

const ANALYTICS_TRENDS: Record<AnalyticsPeriod, AnalyticsTrendPoint[]> = {
  '12m': [
    { label: 'Oct 2025', filed: 640, resolved: 310 }, { label: 'Nov 2025', filed: 890, resolved: 460 },
    { label: 'Dec 2025', filed: 870, resolved: 560 }, { label: 'Jan 2026', filed: 1120, resolved: 720 },
    { label: 'Feb 2026', filed: 1030, resolved: 610 }, { label: 'Mar 2026', filed: 1150, resolved: 770 },
    { label: 'Apr 2026', filed: 1280, resolved: 780 }, { label: 'May 2026', filed: 1480, resolved: 930 },
    { label: 'Jun 2026', filed: 1340, resolved: 820 }, { label: 'Jul 2026', filed: 1300, resolved: 850 },
    { label: 'Aug 2026', filed: 1400, resolved: 1020 }, { label: 'Sep 2026', filed: 1320, resolved: 910 },
  ],
  '90d': [
    { label: 'Jul 2026', filed: 1300, resolved: 850 }, { label: 'Aug 2026', filed: 1400, resolved: 1020 },
    { label: 'Sep 2026', filed: 1320, resolved: 910 },
  ],
  all: [
    { label: '2023', filed: 6840, resolved: 4210 }, { label: '2024', filed: 9180, resolved: 6020 },
    { label: '2025', filed: 11370, resolved: 7770 }, { label: '2026', filed: 12480, resolved: 8916 },
  ],
}

const ANALYTICS_SNAPSHOTS: Record<AnalyticsPeriod, { filed: string; resolved: string; resolvedRate: string; resolvedDelta: string }> = {
  '12m': { filed: '12,480', resolved: '8,916', resolvedRate: '71%', resolvedDelta: '+8% vs previous 12 months' },
  '90d': { filed: '4,020', resolved: '2,780', resolvedRate: '69%', resolvedDelta: '+5% vs previous 90 days' },
  all: { filed: '39,870', resolved: '27,916', resolvedRate: '70%', resolvedDelta: '+11% since launch' },
}

const ANALYTICS_AUTHORITIES: AnalyticsComparisonRow[] = [
  { name: 'Greater Bengaluru Authority', filed: '3,420', resolved: '2,438', satisfaction: 72, resolvedRate: 71, medianDays: 15 },
  { name: 'Bangalore Metro Rail Corporation', filed: '2,184', resolved: '1,602', satisfaction: 69, resolvedRate: 73, medianDays: 17 },
  { name: 'Bangalore Development Authority', filed: '1,980', resolved: '1,306', satisfaction: 63, resolvedRate: 66, medianDays: 26 },
  { name: 'Ward offices', filed: '1,754', resolved: '1,120', satisfaction: 66, resolvedRate: 64, medianDays: 24 },
  { name: 'Water Board', filed: '1,142', resolved: '450', satisfaction: 58, resolvedRate: 39, medianDays: 38 },
]

const ANALYTICS_OFFICE_HOLDERS: AnalyticsComparisonRow[] = [
  { name: 'Ward officer', filed: '4,980', resolved: '3,187', satisfaction: 64, resolvedRate: 64, medianDays: 24 },
  { name: 'Department head', filed: '4,220', resolved: '3,038', satisfaction: 72, resolvedRate: 72, medianDays: 16 },
  { name: 'Commissioner', filed: '3,280', resolved: '2,558', satisfaction: 78, resolvedRate: 78, medianDays: 11 },
]

const ANALYTICS_TIME_BUCKETS = [
  { label: '0–7', days: 'days', value: 2340 }, { label: '8–14', days: 'days', value: 3120 },
  { label: '15–30', days: 'days', value: 2460 }, { label: '31–90', days: 'days', value: 1620 },
  { label: '90+', days: 'days', value: 580 },
]

function chartPath(values: number[], width: number, height: number, padding: number, max: number): string {
  const plotWidth = width - padding * 2
  const plotHeight = height - padding * 2
  return values.map((value, index) => {
    const x = padding + (index / Math.max(values.length - 1, 1)) * plotWidth
    const y = height - padding - (value / max) * plotHeight
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
  }).join(' ')
}

function AnalyticsTrendChart({ period, highlighted, onHighlight }: { period: AnalyticsPeriod; highlighted: string; onHighlight: (label: string) => void }) {
  const data = ANALYTICS_TRENDS[period]
  const width = 760
  const height = 280
  const padding = 42
  const max = Math.max(...data.flatMap((point) => [point.filed, point.resolved]), 1)
  const highlightedPoint = data.find((point) => point.label === highlighted) ?? data[data.length - 1]
  const pointPosition = (index: number, value: number) => ({
    x: padding + (index / Math.max(data.length - 1, 1)) * (width - padding * 2),
    y: height - padding - (value / max) * (height - padding * 2),
  })
  return <div className="analytics-chart-wrap"><div className="analytics-chart-meta"><div className="analytics-legend" aria-label="Chart legend"><span><i className="analytics-legend-dot filed" />Filed</span><span><i className="analytics-legend-dot resolved" />Resolved</span></div><p className="analytics-hover-readout" aria-live="polite"><strong>{highlightedPoint.label}</strong><span>{highlightedPoint.filed.toLocaleString('en-IN')} filed · {highlightedPoint.resolved.toLocaleString('en-IN')} resolved</span></p></div><svg className="analytics-line-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="analytics-trend-title analytics-trend-description"><title id="analytics-trend-title">Complaints filed compared with complaints resolved</title><desc id="analytics-trend-description">A navy line shows complaints filed and a coral line shows complaints resolved over the selected period.</desc>{[0, .25, .5, .75, 1].map((ratio) => { const y = height - padding - ratio * (height - padding * 2); return <g key={ratio}><line x1={padding} x2={width - padding} y1={y} y2={y} className="analytics-grid-line" /><text x={padding - 10} y={y + 4} textAnchor="end" className="analytics-axis-label">{Math.round(max * ratio).toLocaleString('en-IN')}</text></g> })}<path d={chartPath(data.map((point) => point.filed), width, height, padding, max)} className="analytics-line filed" /><path d={chartPath(data.map((point) => point.resolved), width, height, padding, max)} className="analytics-line resolved" />{data.map((point, index) => { const filed = pointPosition(index, point.filed); const resolved = pointPosition(index, point.resolved); const active = point.label === highlightedPoint.label; const showLabel = data.length <= 6 || index % 2 === 0 || index === data.length - 1; return <g key={point.label} className={active ? 'analytics-point-group active' : 'analytics-point-group'}><circle cx={filed.x} cy={filed.y} r={active ? 5 : 4} className="analytics-point filed" tabIndex={0} aria-label={`${point.label}: ${point.filed.toLocaleString('en-IN')} complaints filed`} onMouseEnter={() => onHighlight(point.label)} onFocus={() => onHighlight(point.label)}><title>{point.label}: {point.filed.toLocaleString('en-IN')} filed</title></circle><circle cx={resolved.x} cy={resolved.y} r={active ? 5 : 4} className="analytics-point resolved" tabIndex={0} aria-label={`${point.label}: ${point.resolved.toLocaleString('en-IN')} complaints resolved`} onMouseEnter={() => onHighlight(point.label)} onFocus={() => onHighlight(point.label)}><title>{point.label}: {point.resolved.toLocaleString('en-IN')} resolved</title></circle>{showLabel && <text x={filed.x} y={height - 13} textAnchor="middle" className="analytics-axis-label">{point.label}</text>}</g> })}</svg></div>
}

function AnalyticsView() {
  const [locality, setLocality] = useState('All Bengaluru')
  const [period, setPeriod] = useState<AnalyticsPeriod>('12m')
  const [issueType, setIssueType] = useState('All issue types')
  const [scope, setScope] = useState<AnalyticsScope>('organization')
  const [highlightedMonth, setHighlightedMonth] = useState('Jul 2026')
  const [activeRow, setActiveRow] = useState<string | null>(null)
  const [activeTimeBucket, setActiveTimeBucket] = useState('8–14')
  const snapshot = ANALYTICS_SNAPSHOTS[period]
  const rows = scope === 'organization' ? ANALYTICS_AUTHORITIES : ANALYTICS_OFFICE_HOLDERS
  const trend = ANALYTICS_TRENDS[period]
  const currentHighlightedMonth = trend.some((point) => point.label === highlightedMonth) ? highlightedMonth : trend[trend.length - 1].label
  const maxTimeBucket = Math.max(...ANALYTICS_TIME_BUCKETS.map((bucket) => bucket.value))

  const selectScope = (nextScope: AnalyticsScope) => {
    setScope(nextScope)
    setActiveRow(null)
  }

  return <div className="analytics-view">
    <section className="analytics-hero" aria-labelledby="analytics-title">
      <div className="analytics-hero-copy"><p className="eyebrow">Public accountability · illustrative data</p><h1 id="analytics-title">How power <em>performs.</em></h1><p>See what was reported, what changed, and how residents felt about the outcome.</p></div>
      <div className="analytics-filters" aria-label="Analytics filters">
        <label><span>Area</span><select value={locality} onChange={(event) => setLocality(event.target.value)}><option>All Bengaluru</option><option>Indiranagar</option><option>Jayanagar</option><option>Whitefield</option><option>HSR Layout</option></select></label>
        <label><span>Period</span><select value={period} onChange={(event) => { setPeriod(event.target.value as AnalyticsPeriod); setHighlightedMonth('') }}>{ANALYTICS_PERIOD_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        <label><span>Issue type</span><select value={issueType} onChange={(event) => setIssueType(event.target.value)}><option>All issue types</option><option>Roads & lighting</option><option>Transport</option><option>Water & sanitation</option><option>Planning & permits</option></select></label>
        <div className="analytics-scope-control" role="group" aria-label="Compare by"><span>Compare by</span><div><button type="button" className={scope === 'organization' ? 'active' : ''} aria-pressed={scope === 'organization'} onClick={() => selectScope('organization')}>Organisation</button><button type="button" className={scope === 'office-holder' ? 'active' : ''} aria-pressed={scope === 'office-holder'} onClick={() => selectScope('office-holder')}>Office-holder</button></div></div>
      </div>
    </section>

    <section className="analytics-overview" aria-label="Accountability overview"><article className="analytics-stat"><span className="analytics-stat-label">Complaints filed</span><strong>{snapshot.filed}</strong><span className="analytics-stat-note">Across {locality === 'All Bengaluru' ? 'Bengaluru' : locality}</span></article><article className="analytics-stat"><span className="analytics-stat-label">Resolved</span><strong>{snapshot.resolvedRate}</strong><span className="analytics-stat-note">{snapshot.resolved} cases · {snapshot.resolvedDelta}</span></article></section>

    <section className="analytics-panel analytics-trend-section" aria-labelledby="analytics-trend-heading"><div className="analytics-section-heading"><div><p className="eyebrow">The record over time</p><h2 id="analytics-trend-heading">Filed vs resolved</h2><p>Monthly complaint volume for {locality === 'All Bengaluru' ? 'Bengaluru' : locality}, with closure status kept separate from filing activity.</p></div><span className="analytics-period-stamp">{ANALYTICS_PERIOD_OPTIONS.find((option) => option.value === period)?.label}</span></div><AnalyticsTrendChart period={period} highlighted={currentHighlightedMonth} onHighlight={setHighlightedMonth} /></section>

    <a className="analytics-scroll-cue" href="#analytics-comparison"><span aria-hidden="true">↓</span><span>Scroll for the public record</span></a>

    <section id="analytics-comparison" className="analytics-panel analytics-comparison-section" aria-labelledby="analytics-comparison-heading"><div className="analytics-section-heading"><div><p className="eyebrow">The response in context</p><h2 id="analytics-comparison-heading">Who is responding?</h2><p>Compare volume, confirmed resolution, and resident satisfaction in one view.</p></div><span className="analytics-selection-note" aria-live="polite">{activeRow ? `Showing ${activeRow}` : 'Select a row to trace it through the report card.'}</span></div><div className="analytics-table" role="table" aria-label={`Complaint performance by ${scope === 'organization' ? 'organisation' : 'office-holder'}`}><div className="analytics-table-row analytics-table-head" role="row"><span role="columnheader">{scope === 'organization' ? 'Organisation' : 'Office-holder'}</span><span role="columnheader">Filed</span><span role="columnheader">Resolved</span><span role="columnheader">Satisfaction</span></div>{rows.map((row) => <button type="button" role="row" key={row.name} className={`analytics-table-row ${activeRow === row.name ? 'active' : ''}`} aria-pressed={activeRow === row.name} onClick={() => setActiveRow(row.name)} onMouseEnter={() => setActiveRow(row.name)}><span role="cell" className="analytics-name">{row.name}</span><span role="cell">{row.filed}</span><span role="cell">{row.resolved}</span><span role="cell" className="analytics-satisfaction-cell"><span className="analytics-progress"><i style={{ width: `${row.satisfaction}%` }} /></span><strong>{row.satisfaction}%</strong></span></button>)}</div></section>

    <section className="analytics-panel analytics-time-section" aria-labelledby="analytics-time-heading"><div className="analytics-section-heading"><div><p className="eyebrow">The time it takes</p><h2 id="analytics-time-heading">Time to close</h2><p>Complaints grouped by the number of days between filing and confirmed resolution.</p></div><span className="analytics-selection-note" aria-live="polite">{activeTimeBucket} days is the largest group</span></div><div className="analytics-bars" role="img" aria-label="Complaint counts by time to close">{ANALYTICS_TIME_BUCKETS.map((bucket) => <button type="button" key={bucket.label} className={`analytics-bar-group ${activeTimeBucket === bucket.label ? 'active' : ''}`} onMouseEnter={() => setActiveTimeBucket(bucket.label)} onFocus={() => setActiveTimeBucket(bucket.label)} aria-label={`${bucket.value.toLocaleString('en-IN')} complaints closed in ${bucket.label} days`}><span className="analytics-bar-value">{bucket.value.toLocaleString('en-IN')}</span><span className="analytics-bar" style={{ height: `${Math.max((bucket.value / maxTimeBucket) * 100, 8)}%` }} /><span className="analytics-bar-label">{bucket.label}<small>{bucket.days}</small></span></button>)}</div></section>

    <section id="analytics-satisfaction" className="analytics-lower-grid"><article className="analytics-panel analytics-satisfaction-section" aria-labelledby="analytics-satisfaction-heading"><div className="analytics-section-heading"><div><p className="eyebrow">The resident response</p><h2 id="analytics-satisfaction-heading">How did residents feel?</h2><p>Feedback recorded after a complaint was marked resolved.</p></div></div><div className="analytics-satisfaction-bar" role="img" aria-label="68 percent satisfied, 22 percent mixed, 10 percent not satisfied"><span className="satisfied" style={{ width: '68%' }} /><span className="mixed" style={{ width: '22%' }} /><span className="not-satisfied" style={{ width: '10%' }} /></div><div className="analytics-satisfaction-legend"><span><strong>68%</strong> Satisfied</span><span><strong>22%</strong> Mixed</span><span><strong>10%</strong> Not satisfied</span></div></article><article className="analytics-panel analytics-office-section" aria-labelledby="analytics-office-heading"><div className="analytics-section-heading"><div><p className="eyebrow">The office lens</p><h2 id="analytics-office-heading">By office</h2><p>Resolution performance by public office role.</p></div></div><div className="analytics-office-table"><div className="analytics-office-row analytics-office-head"><span>{scope === 'organization' ? 'Organisation' : 'Role'}</span><span>Resolved rate</span><span>Median days</span></div>{rows.slice(0, 3).map((row) => <div className="analytics-office-row" key={row.name}><span>{row.name}</span><span className="analytics-rate"><strong>{row.resolvedRate}%</strong><i style={{ width: `${row.resolvedRate}%` }} /></span><span>{row.medianDays} days</span></div>)}</div></article></section>

    <footer className="analytics-footnote">Illustrative data · Last updated Sep 2026 · Resolution means status-confirmed closure</footer>
  </div>
}

function FeedSortMenu({ user }: { user: User }) {
  const preferences = readFeedPreferences(user.id)
  const [open, setOpen] = useState(false)
  const [sort, setSort] = useState<FeedSortValue>(() => feedSortFromQuery(new URLSearchParams(window.location.search).get('sort')) ?? preferences.sort ?? 'recommended')
  const [locality, setLocality] = useState(() => new URLSearchParams(window.location.search).get('locality') ?? preferences.locality ?? '')
  const [status, setStatus] = useState<TicketStatus | ''>(() => new URLSearchParams(window.location.search).get('status') as TicketStatus | '' || '')
  const [authority, setAuthority] = useState(() => new URLSearchParams(window.location.search).get('authority') ?? '')
  const menuRef = useRef<HTMLDivElement | null>(null)
  const selectedSort = FEED_SORT_OPTIONS.find((option) => option.value === sort)?.label ?? 'Best'
  const activeFilterCount = [locality, status, authority].filter(Boolean).length

  const syncFromLocation = () => {
    const params = new URLSearchParams(window.location.search)
    setSort(feedSortFromQuery(params.get('sort')) ?? preferences.sort ?? 'recommended')
    setLocality(params.has('locality') ? params.get('locality') ?? '' : preferences.locality ?? '')
    setStatus(params.has('status') ? params.get('status') as TicketStatus : '')
    setAuthority(params.has('authority') ? params.get('authority') ?? '' : '')
  }

  useEffect(() => {
    const handleNavigation = () => syncFromLocation()
    window.addEventListener('popstate', handleNavigation)
    return () => window.removeEventListener('popstate', handleNavigation)
  }, [preferences.locality, preferences.sort])

  useEffect(() => {
    if (!open) return
    const closeOnOutsideClick = (event: PointerEvent) => { if (menuRef.current && !menuRef.current.contains(event.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', closeOnOutsideClick)
    return () => document.removeEventListener('pointerdown', closeOnOutsideClick)
  }, [open])

  const apply = (next: { sort?: FeedSortValue; locality?: string; status?: TicketStatus | ''; authority?: string }) => {
    const params = new URLSearchParams(window.location.search)
    params.set('sort', next.sort ?? sort)
    params.set('locality', next.locality ?? locality)
    params.set('status', next.status ?? status)
    params.set('authority', next.authority ?? authority)
    window.history.pushState({}, '', `${window.location.pathname}?${params.toString()}${window.location.hash}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }

  const clearFilters = () => {
    setLocality('')
    setStatus('')
    setAuthority('')
    apply({ locality: '', status: '', authority: '' })
  }

  return <div ref={menuRef} className="feed-sort-menu"><button type="button" className="feed-sort-trigger" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen((current) => !current)}><span>{selectedSort}</span>{activeFilterCount > 0 && <span className="feed-filter-count">{activeFilterCount}</span>}<span className="menu-chevron" aria-hidden="true" /></button>{open && <div className="feed-filter-menu" aria-label="Sort and filter posts"><p className="feed-menu-heading">Sort by</p><div className="feed-sort-options">{FEED_SORT_OPTIONS.map((option) => <button key={option.value} type="button" className={sort === option.value ? 'active' : ''} disabled={option.value === 'nearby' && !locality} onClick={() => { setSort(option.value); apply({ sort: option.value }); setOpen(false) }}>{option.label}</button>)}</div><div className="feed-menu-divider" /><p className="feed-menu-heading">Filter posts</p><label className="feed-menu-field"><span>Locality</span><select value={locality || 'All Bengaluru'} onChange={(event) => { const value = event.target.value === 'All Bengaluru' ? '' : event.target.value; setLocality(value); apply({ locality: value }) }}><option>All Bengaluru</option>{LOCALITIES.filter((item) => item !== 'All Bengaluru').map((item) => <option key={item}>{item}</option>)}</select></label><label className="feed-menu-field"><span>Status</span><select value={status} onChange={(event) => { const value = event.target.value as TicketStatus | ''; setStatus(value); apply({ status: value }) }}><option value="">All statuses</option>{TICKET_STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><label className="feed-menu-field"><span>Authority</span><select value={authority} onChange={(event) => { const value = event.target.value; setAuthority(value); apply({ authority: value }) }}><option value="">All authorities</option>{Object.entries(AUTHORITY_NAMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{activeFilterCount > 0 && <button type="button" className="feed-menu-clear" onClick={clearFilters}>Clear filters</button>}</div>}</div>
}

function TopBar({ user, surface, onSurface, onSignOut }: { user: User; surface: Surface; onSurface: (surface: Surface) => void; onSignOut: () => void }) {
  const [showMenu, setShowMenu] = useState(false)
  const [showInbox, setShowInbox] = useState(false)
  const [notifications, setNotifications] = useState<CivicNotification[]>([])
  const [inboxError, setInboxError] = useState<string | null>(null)
  const [online, setOnline] = useState(() => navigator.onLine)
  const [activityNotifications, setActivityNotifications] = useState(() => readActivityPreference(user.id))
  const topbarRef = useRef<HTMLElement | null>(null)
  const accountButtonRef = useRef<HTMLButtonElement | null>(null)
  const accountDialogRef = useRef<HTMLDivElement | null>(null)
  const inboxButtonRef = useRef<HTMLButtonElement | null>(null)
  const inboxDialogRef = useRef<HTMLDivElement | null>(null)
  const unreadCount = notifications.filter((item) => !item.read).length
  const refreshNotifications = useCallback(async () => {
    if (!activityNotifications) { setNotifications([]); return }
    try { setInboxError(null); setNotifications(await getNotifications()) } catch (error) { setInboxError(error instanceof Error ? error.message : 'Activity is temporarily unavailable') }
  }, [activityNotifications])
  useEffect(() => {
    void refreshNotifications()
    const onlineHandler = () => setOnline(true)
    const offlineHandler = () => setOnline(false)
    window.addEventListener('online', onlineHandler)
    window.addEventListener('offline', offlineHandler)
    return () => { window.removeEventListener('online', onlineHandler); window.removeEventListener('offline', offlineHandler) }
  }, [refreshNotifications])
  useEffect(() => {
    if (!showInbox && !showMenu) return
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setShowInbox(false); setShowMenu(false) } }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [showInbox, showMenu])
  useEffect(() => {
    const closeOnOutsideClick = (event: PointerEvent) => { if (topbarRef.current && !topbarRef.current.contains(event.target as Node)) { setShowInbox(false); setShowMenu(false) } }
    document.addEventListener('pointerdown', closeOnOutsideClick)
    return () => document.removeEventListener('pointerdown', closeOnOutsideClick)
  }, [])
  const openInbox = async () => { setShowInbox((current) => !current); await refreshNotifications() }
  const markRead = async (item: CivicNotification) => {
    try {
      if (!item.read) { await markNotification(item.id); setNotifications((current) => current.map((candidate) => candidate.id === item.id ? { ...candidate, read: true } : candidate)) }
      setInboxError(null)
      if (item.post_id) { onSurface('feed'); window.history.pushState({}, '', `/feed/post/${encodeURIComponent(item.post_id)}${window.location.search}`); window.dispatchEvent(new PopStateEvent('popstate')); setShowInbox(false) }
    } catch (error) { setInboxError(error instanceof Error ? error.message : 'Could not update this notification') }
  }
  const markAllRead = async () => {
    try { await markAllNotificationsRead(); setNotifications((current) => current.map((item) => ({ ...item, read: true }))); setInboxError(null) }
    catch (error) { setInboxError(error instanceof Error ? error.message : 'Could not mark activity as read') }
  }
  useEffect(() => {
    if (!showMenu) return
    const dialog = accountDialogRef.current
    const first = dialog?.querySelector<HTMLElement>('button, input, select, textarea')
    first?.focus()
    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !dialog) return
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>('button, input, select, textarea')).filter((item) => !item.hasAttribute('disabled'))
      if (!focusable.length) return
      const firstFocusable = focusable[0]; const lastFocusable = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === firstFocusable) { event.preventDefault(); lastFocusable.focus() }
      else if (!event.shiftKey && document.activeElement === lastFocusable) { event.preventDefault(); firstFocusable.focus() }
    }
    window.addEventListener('keydown', trap)
    return () => { window.removeEventListener('keydown', trap); accountButtonRef.current?.focus() }
  }, [showMenu])
  useEffect(() => {
    if (!showInbox) return
    const dialog = inboxDialogRef.current
    const first = dialog?.querySelector<HTMLElement>('button, a[href]')
    first?.focus()
    const trap = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !dialog) return
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>('button, a[href]')).filter((item) => !item.hasAttribute('disabled'))
      if (!focusable.length) return
      const firstFocusable = focusable[0]; const lastFocusable = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === firstFocusable) { event.preventDefault(); lastFocusable.focus() }
      else if (!event.shiftKey && document.activeElement === lastFocusable) { event.preventDefault(); firstFocusable.focus() }
    }
    window.addEventListener('keydown', trap)
    return () => { window.removeEventListener('keydown', trap); inboxButtonRef.current?.focus() }
  }, [showInbox])
  const surfaceLabel = surface === 'feed' ? 'Public civic feed' : surface === 'agent' ? 'Private agent workspace' : 'Public accountability'
  return <header ref={topbarRef} className="topbar"><div className="topbar-context"><span className="context-kicker">CIVITASX</span><span className="context-slash">/</span><span>{surfaceLabel}</span></div><div className="topbar-actions"><div className={`connection-state ${online ? 'online' : 'offline'}`} aria-live="polite"><span className="dot" />{online ? 'On this device' : 'Offline · drafts stay safe'}</div><div className="inbox-menu"><button ref={inboxButtonRef} className="inbox-button" onClick={() => void openInbox()} aria-expanded={showInbox} aria-haspopup="dialog" aria-controls="activity-popover" aria-label={`Activity${unreadCount ? `, ${unreadCount} unread` : ''}`}>◎{unreadCount > 0 && <span className="inbox-count">{unreadCount}</span>}</button>{showInbox && <div id="activity-popover" ref={inboxDialogRef} className="notification-popover" role="dialog" aria-modal="true" aria-label="Activity"><div className="notification-heading"><span className="eyebrow">Activity</span><span>{notifications.length ? `${notifications.length} updates` : 'Quiet for now'}</span></div>{notifications.length > 0 && <button className="mark-read-button" onClick={() => void markAllRead()}>Mark all read</button>}{inboxError ? <p className="muted" role="alert">{inboxError}</p> : notifications.length === 0 ? <p className="muted">Follow a civic post to receive useful updates here.</p> : <div className="notification-list">{notifications.slice(0, 8).map((item) => <button className={`notification-item ${item.read ? '' : 'unread'}`} key={item.id} onClick={() => void markRead(item)}><span className="dot dot-lime" /><span><span className="notification-message">{item.message}</span><small>{formatTime(item.created_at)}{item.post_id ? ' · Open post' : ''}</small></span></button>)}</div>}</div>}</div><div className="account-menu"><button ref={accountButtonRef} className="account-button" aria-label="Open account settings" onClick={() => setShowMenu((current) => !current)} aria-expanded={showMenu} aria-haspopup="dialog" aria-controls="account-popover"><span className="avatar">{initials(user.name)}</span><span className="account-name">{user.name}</span></button>{showMenu && <div id="account-popover" ref={accountDialogRef} className="account-popover" role="dialog" aria-modal="true" aria-label="Account settings"><div className="account-profile-summary"><span className="avatar account-profile-avatar">{initials(user.name)}</span><div><strong>{user.name}</strong><span>{user.email}</span></div></div><PrivacyPreferences userId={user.id} onActivityNotificationsChange={setActivityNotifications} onClose={() => setShowMenu(false)} /><button type="button" className="account-signout" onClick={onSignOut}><span className="account-signout-icon" aria-hidden="true" />Sign out</button></div>}</div></div></header>
}

function PrivacyPreferences({ userId, onActivityNotificationsChange, onClose }: { userId: string; onActivityNotificationsChange?: (enabled: boolean) => void; onClose: () => void }) {
  const key = `civitas.profile.preferences.${userId}`
  const [locality, setLocality] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [remember, setRemember] = useState(true)
  const [activityNotifications, setActivityNotifications] = useState(true)
  const [storageError, setStorageError] = useState<string | null>(null)

  useEffect(() => {
    const value = readStoredProfilePreferences(userId)
    setLocality(value.locality ?? '')
    setDisplayName(value.displayName ?? '')
    setRemember(value.remember !== false)
    setActivityNotifications(value.activityNotifications !== false)
  }, [userId])

  const save = () => {
    const next = { locality, displayName, remember, activityNotifications, onboardingSeen: true }
    const feedKey = `civitas.feed.preferences.${userId}`
    try {
      setStorageError(null)
      if (remember) {
        window.localStorage.setItem(key, JSON.stringify(next))
        window.sessionStorage.removeItem(key)
      } else {
        window.localStorage.removeItem(key)
        window.sessionStorage.setItem(key, JSON.stringify(next))
        window.localStorage.removeItem(feedKey)
      }
      onActivityNotificationsChange?.(activityNotifications)
      onClose()
    } catch {
      setStorageError('These choices could not be saved in this browser. They will remain active for this session.')
    }
  }

  const clear = () => {
    try { window.localStorage.removeItem(key); window.sessionStorage.removeItem(key) } catch { /* best effort */ }
    setLocality('')
    setDisplayName('')
    setRemember(true)
    setActivityNotifications(true)
    onActivityNotificationsChange?.(true)
  }

  return (
    <div className="privacy-preferences" role="group" aria-label="Privacy and memory preferences">
      <p className="eyebrow">Your local memory</p>
      <p className="muted">Saved here on this device. Turn off remembering to keep these choices only for this session.</p>
      {storageError && <p className="inline-error" role="alert">{storageError}</p>}
      <label className="privacy-field">
        <span>Default locality</span>
        <select value={locality} onChange={(event) => setLocality(event.target.value)}>
          <option value="">Ask each time</option>
          {LOCALITIES.filter((item) => item !== 'All Bengaluru').map((item) => <option key={item}>{item}</option>)}
        </select>
      </label>
      <label className="privacy-field">
        <span>Public display name</span>
        <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Use a pseudonym" />
      </label>
      <label className="privacy-toggle-row">
        <span>Remember these choices</span>
        <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
        <span className="privacy-toggle" aria-hidden="true"><span /></span>
      </label>
      <label className="privacy-toggle-row">
        <span>Activity notifications</span>
        <input type="checkbox" checked={activityNotifications} onChange={(event) => setActivityNotifications(event.target.checked)} />
        <span className="privacy-toggle" aria-hidden="true"><span /></span>
      </label>
      <div className="privacy-actions">
        <button type="button" className="privacy-clear" onClick={clear}>Clear saved choices</button>
        <button type="button" className="privacy-cancel" onClick={onClose}>Cancel</button>
        <button type="button" className="privacy-save button-dark" onClick={save}>Save</button>
      </div>
    </div>
  )
}

function FeedView({ user, capabilities, postId, onOpenPost, onClosePost, onOpenAgent }: { user: User; capabilities: AppConfig['capabilities']; postId: string | null; onOpenPost: (postId: string) => void; onClosePost: () => void; onOpenAgent: (prompt?: string) => void }) {
  const [sort, setSort] = useState<'recent' | 'popular' | 'nearby' | 'following' | 'recommended'>(() => readFeedPreferences(user.id).sort ?? 'recommended')
  const [locality, setLocality] = useState(() => readFeedPreferences(user.id).locality || readProfileLocality(user.id) || '')
  const [statusFilter, setStatusFilter] = useState<TicketStatus | ''>('')
  const [authorityFilter, setAuthorityFilter] = useState('')
  const [topic, setTopic] = useState(readFeedSearchQuery)
  const [topicInput, setTopicInput] = useState(readFeedSearchQuery)
  const [localityFollowing, setLocalityFollowing] = useState(false)
  const [subjectFollowing, setSubjectFollowing] = useState(false)
  const [posts, setPosts] = useState<CivicPost[]>([])
  const [authorities, setAuthorities] = useState<AuthorityRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [detailPost, setDetailPost] = useState<CivicPost | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const loadSequence = useRef(0)
  const load = useCallback(async () => {
    const sequence = ++loadSequence.current
    setLoading(true)
    setError(null)
    setNextCursor(null)
    try {
      const page = await getFeedPage({ sort, locality: locality || undefined, status: statusFilter || undefined, authorityId: authorityFilter || undefined, topic: topic || undefined })
      if (sequence === loadSequence.current) {
        setPosts(page.items)
        setNextCursor(page.next_cursor)
      }
    } catch (nextError) {
      if (sequence === loadSequence.current) setError(nextError instanceof Error ? nextError.message : 'Could not load the Feed')
    } finally {
      if (sequence === loadSequence.current) setLoading(false)
    }
  }, [authorityFilter, locality, sort, statusFilter, topic])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (sort === 'nearby' && !locality) setSort('recommended')
  }, [locality, sort])
  useEffect(() => { if (capabilities.research) void getAuthorities().then(setAuthorities).catch(() => setAuthorities([])); else setAuthorities([]) }, [capabilities.research])
  useEffect(() => {
    const handle = window.setTimeout(() => setTopic(topicInput.trim()), 300)
    return () => window.clearTimeout(handle)
  }, [topicInput])
  useEffect(() => {
    const handleNavigation = () => {
      const params = new URLSearchParams(window.location.search)
      const nextSort = feedSortFromQuery(params.get('sort'))
      if (nextSort) setSort(nextSort)
      if (params.has('locality')) setLocality(params.get('locality') ?? '')
      if (params.has('status')) setStatusFilter(params.get('status') as TicketStatus | '')
      if (params.has('authority')) setAuthorityFilter(params.get('authority') ?? '')
      const nextQuery = readFeedSearchQuery()
      setTopicInput(nextQuery)
      setTopic(nextQuery)
    }
    window.addEventListener('popstate', handleNavigation)
    return () => window.removeEventListener('popstate', handleNavigation)
  }, [])
  useEffect(() => {
    if (!postId) {
      setDetailPost(null)
      setDetailLoading(false)
      setDetailError(null)
      return
    }
    let active = true
    const cached = posts.find((post) => post.id === postId)
    if (cached) { setDetailPost(cached); setDetailLoading(false) }
    else { setDetailPost(null); setDetailLoading(true) }
    setDetailError(null)
    void getFeedPost(postId, locality || undefined).then((next) => {
      if (!active) return
      setDetailPost(next)
      setDetailLoading(false)
    }).catch((nextError) => {
      if (!active) return
      if (!cached) setDetailPost(null)
      setDetailLoading(false)
      setDetailError(nextError instanceof Error ? nextError.message : 'Could not open this civic post')
    })
    return () => { active = false }
  }, [locality, postId])
  useEffect(() => {
    const profile = readStoredProfilePreferences(user.id)
    try {
      const key = `civitas.feed.preferences.${user.id}`
      if (profile.remember === false) { window.localStorage.removeItem(key); window.sessionStorage.setItem(key, JSON.stringify({ sort, locality })) }
      else { window.localStorage.setItem(key, JSON.stringify({ sort, locality })); window.sessionStorage.removeItem(key) }
    } catch { /* local preference memory is best effort */ }
  }, [locality, sort, user.id])
  useEffect(() => {
    let active = true
    void getSubjectFollows().then((items) => {
      if (!active) return
      const selectedLocality = locality || 'Bengaluru'
      setLocalityFollowing(items.some((item) => item.subject_type === 'locality' && item.value.toLocaleLowerCase() === selectedLocality.toLocaleLowerCase()))
      const subject = topic ? { type: 'topic', value: topic } : authorityFilter ? { type: 'authority', value: authorityFilter } : null
      setSubjectFollowing(Boolean(subject && items.some((item) => item.subject_type === subject.type && item.value.toLocaleLowerCase() === subject.value.toLocaleLowerCase())))
    }).catch(() => {
      if (active) { setLocalityFollowing(false); setSubjectFollowing(false) }
    })
    return () => { active = false }
  }, [authorityFilter, locality, topic])
  const toggleLocalityFollow = async () => {
    const value = locality || 'Bengaluru'
    try { const result = await followSubject('locality', value, !localityFollowing); setLocalityFollowing(result.following); setError(null) } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not follow locality') }
  }
  const followSubjectFilter = async () => {
    const subject = topic ? { type: 'topic' as const, value: topic } : authorityFilter ? { type: 'authority' as const, value: authorityFilter } : null
    if (!subject) return
    try { const result = await followSubject(subject.type, subject.value, !subjectFollowing); setSubjectFollowing(result.following); setError(null) } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not follow this filter') }
  }
  const loadMore = async () => {
    if (!nextCursor || loadingMore) return
    setLoadingMore(true)
    try {
      const page = await getFeedPage({ sort, locality: locality || undefined, status: statusFilter || undefined, authorityId: authorityFilter || undefined, topic: topic || undefined, cursor: nextCursor })
      setPosts((current) => [...current, ...page.items])
      setNextCursor(page.next_cursor)
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Could not load more civic signals')
    } finally {
      setLoadingMore(false)
    }
  }
  const subjectLabel = topic ? 'topic' : authorityFilter ? 'authority' : ''
  const updatePost = (postId: string, patch: Partial<CivicPost>) => {
    setPosts((current) => current.map((post) => post.id === postId ? { ...post, ...patch } : post))
    setDetailPost((current) => current?.id === postId ? { ...current, ...patch } : current)
  }
  const nearbyExplanation = locality ? `Nearby uses your selected locality (${locality}) and citywide posts.` : 'Nearby uses the locality you select; no precise location is collected.'
  if (postId) return <PostDetailPage post={detailPost} loading={detailLoading} error={detailError} agentEnabled={capabilities.agent} onBack={onClosePost} onUpdate={updatePost} onOpenAgent={onOpenAgent} />
  return <div className="feed-view"><section className="feed-hero"><div><p className="eyebrow">Bengaluru / public civic feed</p><h1>What needs<br /><em>attention?</em></h1><p className="feed-lede">Public issues, evidence, and community context. Official submission is always reviewed separately.</p></div><div className="feed-index"><span>OPEN SIGNALS</span><strong>{String(posts.length).padStart(2, '0')}</strong><span>IN THIS VIEW</span></div></section><div className="feed-toolbar"><div className="feed-tabs" aria-label="Feed sort"><button type="button" aria-pressed={sort === 'recommended'} className={sort === 'recommended' ? 'active' : ''} onClick={() => setSort('recommended')}>For you</button><button type="button" aria-pressed={sort === 'recent'} className={sort === 'recent' ? 'active' : ''} onClick={() => setSort('recent')}>Recent</button><button type="button" aria-pressed={sort === 'nearby'} className={sort === 'nearby' ? 'active' : ''} disabled={!locality} onClick={() => setSort('nearby')} aria-label={locality ? 'Nearby' : 'Nearby, select a locality first'}>Nearby</button><button type="button" aria-pressed={sort === 'following'} className={sort === 'following' ? 'active' : ''} onClick={() => setSort('following')}>Following</button><button type="button" aria-pressed={sort === 'popular'} className={sort === 'popular' ? 'active' : ''} onClick={() => setSort('popular')}>Popular</button></div><div className="feed-filters"><label className="sr-only" htmlFor="locality-filter">Locality</label><select id="locality-filter" value={locality || 'All Bengaluru'} onChange={(event) => { setLocality(event.target.value === 'All Bengaluru' ? '' : event.target.value); setLocalityFollowing(false) }}>{LOCALITIES.map((item) => <option key={item}>{item}</option>)}</select><button className="filter-follow" disabled={!locality} onClick={() => void toggleLocalityFollow()}>{localityFollowing ? 'Following locality' : 'Follow locality'}</button><label className="sr-only" htmlFor="status-filter">Status</label><select id="status-filter" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as TicketStatus | '')}><option value="">All statuses</option>{TICKET_STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select><label className="sr-only" htmlFor="authority-filter">Authority</label><select id="authority-filter" value={authorityFilter} onChange={(event) => { setAuthorityFilter(event.target.value); setSubjectFollowing(false) }}><option value="">All authorities</option>{authorities.map((authority) => <option key={authority.authority_id} value={authority.authority_id}>{authority.short_name || authority.name}</option>)}</select><label className="sr-only" htmlFor="topic-filter">Search Feed</label><input id="topic-filter" value={topicInput} onChange={(event) => { setTopicInput(event.target.value); setSubjectFollowing(false) }} placeholder="Search issues" />{subjectLabel && <button className="filter-follow" onClick={() => void followSubjectFilter()}>{subjectFollowing ? `Following ${subjectLabel}` : `Follow ${subjectLabel}`}</button>}</div></div><p className="feed-sort-help">{sort === 'recommended' ? 'For you: locality, follows, fresh discussion, evidence, and unresolved issues.' : sort === 'nearby' ? nearbyExplanation : sort === 'popular' ? 'Popular: votes and discussion, with official status preserved.' : sort === 'following' ? 'Following: people, authorities, and subjects you follow.' : 'Recent: chronological.'}</p><div className="feed-create"><div><span className="feed-create-mark" aria-hidden="true">+</span><strong>Have an issue to report?</strong><span>Describe what happened. Attach photos or documents.</span></div><button className="button button-dark" disabled={!capabilities.agent} onClick={() => onOpenAgent()}>{capabilities.agent ? 'Open private Agent' : 'Agent unavailable'} <span aria-hidden="true">↗</span></button></div>{error && <div className="inline-error" role="alert">{error}</div>}{loading ? <div className="feed-loading"><div className="loading-mark small" aria-hidden="true"><span /><span /><span /></div><span>Loading civic signals…</span></div> : posts.length === 0 ? <div className="empty-cases"><div className="empty-score"><span /><span /><span /></div><div><h3>No public signals match this view.</h3><p>Open Agent to create the first reviewed ticket.</p></div></div> : <><div className="feed-list">{posts.map((post) => <FeedPost key={post.id} post={post} agentEnabled={capabilities.agent} onUpdate={(patch) => updatePost(post.id, patch)} onOpenDetail={() => onOpenPost(post.id)} onOpenAgent={() => onOpenAgent(`Ticket ${post.civitas_ticket_id}: ${post.title}\n\n${post.body}`)} />)}</div>{nextCursor && <button className="button button-outline feed-load-more" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? 'Loading more…' : 'Load more civic signals'}</button>}</>}<p className="feed-footnote">Ticket IDs track CivitasX cases; they become government references only after connector-confirmed submission.</p></div>
}

function PostDetailPage({ post, loading, error, agentEnabled, onBack, onUpdate, onOpenAgent }: { post: CivicPost | null; loading: boolean; error: string | null; agentEnabled: boolean; onBack: () => void; onUpdate: (postId: string, patch: Partial<CivicPost>) => void; onOpenAgent: (prompt?: string) => void }) {

  useEffect(() => {
    if (!post) return
    const previousTitle = document.title
    document.title = `${post.title} — CivitasX`
    return () => { document.title = previousTitle }
  }, [post?.id, post?.title])

  return (
    <main className="post-detail-page">
      <header className="post-detail-page-header">
        <button type="button" className="post-back" onClick={onBack}><span aria-hidden="true">←</span> Back to civic feed</button>
        <span className="post-route-label">Public civic signal{post ? ' · ' + post.civitas_ticket_id : ''}</span>
      </header>
      {loading && !post ? (
        <div className="post-detail-loading" role="status"><div className="loading-mark small" aria-hidden="true"><span /><span /><span /></div><p>Opening this civic record…</p></div>
      ) : error && !post ? (
        <div className="post-detail-error" role="alert"><strong>This civic record could not be opened.</strong><p>{error}</p><button type="button" className="button button-outline" onClick={onBack}>Return to feed</button></div>
      ) : post ? (
        <div className="post-detail-layout">
          <section className="post-detail-main">
            <FeedPost
              post={post}
              agentEnabled={agentEnabled}
              commentsInitiallyOpen
              truncateBody={false}
              showMedia={false}
              onUpdate={(patch) => onUpdate(post.id, patch)}
              onOpenDetail={onBack}
              onOpenAgent={() => onOpenAgent('Ticket ' + post.civitas_ticket_id + ': ' + post.title + '\n\n' + post.body)}
            />
          </section>
          <aside className="post-detail-aside">
            <div className="post-detail-aside-card"><p className="eyebrow">A civic record, not a promise</p><h2>Read the evidence. Add local context.</h2><p>This page keeps the public snapshot, discussion, and source trail together. Official submission remains a separate reviewed step.</p><button type="button" className="button button-dark button-full" disabled={!agentEnabled} onClick={() => onOpenAgent('Ticket ' + post.civitas_ticket_id + ': ' + post.title + '\n\n' + post.body)}>{agentEnabled ? 'Ask Agent about this' : 'Agent unavailable'} <span aria-hidden="true">↗</span></button></div>
            <div className="post-detail-aside-note"><span className="eyebrow">Ticket ID</span><strong>{post.civitas_ticket_id}</strong><span>{post.locality ?? 'Bengaluru'} · {statusLabel(post.status)}</span><small>Ticket IDs track CivitasX cases; they become government references only after connector-confirmed submission.</small></div>
          </aside>
        </div>
      ) : null}
    </main>
  )
}


function PostActionIcon({ kind }: { kind: 'comment' | 'agent' | 'follow' | 'more' | 'share' }) {
  if (kind === 'comment') return <svg className="post-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5h16v10H9l-5 4v-4H4z" /></svg>
  if (kind === 'agent') return <svg className="post-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 17 2-5 8-8 2 2-8 8z" /><path d="m5 19 2-2M16 3v4M14 5h4M19 13v5M16.5 15.5h5" /></svg>
  if (kind === 'follow') return <svg className="post-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></svg>
  if (kind === 'share') return <svg className="post-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12v6h14v-6" /><path d="m12 16 0-11M8 9l4-4 4 4" /></svg>
  return <svg className="post-action-icon post-action-icon-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 9 7 7 7-7" /></svg>
}

function FeedPost({ post, onUpdate, onOpenDetail, onOpenAgent, agentEnabled = true, commentsInitiallyOpen = false, showMedia = true, truncateBody = true }: { post: CivicPost; onUpdate: (patch: Partial<CivicPost>) => void; onOpenDetail: () => void; onOpenAgent: () => void; agentEnabled?: boolean; commentsInitiallyOpen?: boolean; showMedia?: boolean; truncateBody?: boolean }) {
  const [commentsOpen] = useState(commentsInitiallyOpen)
  const [comments, setComments] = useState<CivicComment[]>([])
  const [comment, setComment] = useState('')
  const [replyTo, setReplyTo] = useState<CivicComment | null>(null)
  const [busy, setBusy] = useState(false)
  const [followBusy, setFollowBusy] = useState(false)
  const [shareCopied, setShareCopied] = useState(false)
  const [showReason, setShowReason] = useState(false)
  const [feedback, setFeedback] = useState<{ kind: 'success' | 'error'; message: string } | null>(null)
  const [media, setMedia] = useState<PublicPostAttachment[]>([])
  const [mediaUrls, setMediaUrls] = useState<Record<string, string>>({})
  const body = withoutRedditSource(post.body)
  const [previewBody, setPreviewBody] = useState(body)
  const bodyRef = useRef<HTMLParagraphElement | null>(null)
  const statusTone = post.status === 'resolved' ? 'resolved' : post.status === 'not_solved' ? 'attention' : post.status === 'in_progress' ? 'working' : 'neutral'
  const rootComments = comments.filter((item) => !item.parent_id || !comments.some((parent) => parent.id === item.parent_id))
  const loadComments = useCallback(async () => {
    try { setComments(await getFeedComments(post.id)) }
    catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not load comments' }) }
  }, [post.id])
  useEffect(() => {
    if (commentsInitiallyOpen) void loadComments()
  }, [commentsInitiallyOpen, loadComments])
  useEffect(() => {
    if (!showMedia) {
      setMedia([])
      setMediaUrls({})
      return
    }
    let active = true
    let objectUrls: string[] = []
    setMedia([])
    setMediaUrls({})
    void getFeedPostAttachments(post.id, post.locality || undefined).then(async (items) => {
      if (!active) return
      const images = items.filter((item) => item.content_type.startsWith('image/')).slice(0, 4)
      setMedia(images)
      const loaded = await Promise.allSettled(images.map(async (item) => {
        const blob = await downloadFeedPostAttachment(post.id, item.id, post.locality || undefined)
        return [item.id, URL.createObjectURL(blob)] as const
      }))
      const urls: Record<string, string> = {}
      for (const result of loaded) if (result.status === 'fulfilled') urls[result.value[0]] = result.value[1]
      objectUrls = Object.values(urls)
      if (!active) { objectUrls.forEach((url) => URL.revokeObjectURL(url)); return }
      setMediaUrls(urls)
    }).catch(() => {
      if (active) { setMedia([]); setMediaUrls({}) }
    })
    return () => { active = false; objectUrls.forEach((url) => URL.revokeObjectURL(url)) }
  }, [post.id, post.locality, showMedia])
  useLayoutEffect(() => {
    if (!truncateBody) {
      setPreviewBody(body)
      return
    }
    const element = bodyRef.current
    if (!element || !element.clientWidth) return
    const styles = getComputedStyle(element)
    const lineHeight = Number.parseFloat(styles.lineHeight)
    const maxHeight = (Number.isFinite(lineHeight) ? lineHeight : 21) * 6 + 1
    const measure = document.createElement('p')
    measure.className = element.className
    Object.assign(measure.style, {
      position: 'absolute',
      left: '-100000px',
      top: '0',
      width: `${element.clientWidth}px`,
      maxHeight: 'none',
      height: 'auto',
      overflow: 'visible',
      visibility: 'hidden',
    })
    document.body.appendChild(measure)
    const fits = (value: string) => {
      measure.textContent = value
      return measure.scrollHeight <= maxHeight
    }
    if (fits(body)) {
      setPreviewBody(body)
      measure.remove()
      return
    }
    let low = 0
    let high = body.length
    while (low < high) {
      const middle = Math.ceil((low + high) / 2)
      if (fits(`${body.slice(0, middle).trimEnd()}...`)) low = middle
      else high = middle - 1
    }
    setPreviewBody(`${body.slice(0, low).trimEnd()}...`)
    measure.remove()
  }, [body, truncateBody])
  const vote = async (value: -1 | 0 | 1) => {
    setBusy(true)
    const previous = { user_vote: post.user_vote, vote_score: post.vote_score, upvotes: post.upvotes, downvotes: post.downvotes }
    onUpdate({ user_vote: value, vote_score: previous.vote_score - previous.user_vote + value, upvotes: post.upvotes + (value === 1 ? 1 : 0) - (previous.user_vote === 1 ? 1 : 0), downvotes: post.downvotes + (value === -1 ? 1 : 0) - (previous.user_vote === -1 ? 1 : 0) })
    try { const result = await voteFeedPost(post.id, value); onUpdate({ user_vote: result.value, vote_score: result.vote_score, upvotes: result.upvotes, downvotes: result.downvotes }) } catch (error) { onUpdate(previous); setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not record vote' }) } finally { setBusy(false) }
  }
  const submitComment = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!comment.trim() || post.is_locked) return
    try { const next = await addFeedComment(post.id, comment, replyTo?.id); setComments((current) => [...current, next]); setComment(''); setReplyTo(null); onUpdate({ comment_count: post.comment_count + 1 }) } catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not add comment' }) }
  }
  const toggleFollow = async () => {
    setFollowBusy(true)
    try { const result = await followFeedPost(post.id, !post.is_following); onUpdate({ is_following: result.following }) } catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not update follow' }) } finally { setFollowBusy(false) }
  }
  const share = async () => {
    try { const result = await shareFeedPost(post.id); await copyText(`${window.location.origin}${result.url}`); setShareCopied(true); window.setTimeout(() => setShareCopied(false), 2000) } catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not create or copy share link' }) }
  }
  const askAgent = () => { if (agentEnabled) onOpenAgent(); else setFeedback({ kind: 'error', message: 'The private Agent is not enabled in this phase.' }) }
  const visibleMedia = media.filter((item) => mediaUrls[item.id])
  return <article className={`feed-post ${post.is_demo ? 'demo-post' : ''}`}><div className="post-votes"><button aria-label={`Upvote ${post.title}`} className={post.user_vote === 1 ? 'selected' : ''} disabled={busy} onClick={() => void vote(post.user_vote === 1 ? 0 : 1)}>▲</button><strong>{post.vote_score}</strong><button aria-label={`Downvote ${post.title}`} className={post.user_vote === -1 ? 'selected down' : ''} disabled={busy} onClick={() => void vote(post.user_vote === -1 ? 0 : -1)}>▼</button></div><div className="post-body"><div className="post-topline"><span className={`status-chip ${statusTone}`}><span className={`dot dot-${statusTone}`} /> {statusLabel(post.status)}</span><button className="post-ticket post-ticket-link" onClick={onOpenDetail} aria-label={`Open ticket ${post.civitas_ticket_id}`}>{post.civitas_ticket_id}</button><span className="post-time">{formatPostAge(post.created_at)}</span></div><h2><button className="post-title-link" onClick={onOpenDetail} aria-label={post.title}>{post.title}</button></h2>{visibleMedia.length > 0 && <div className={`post-media post-media-${visibleMedia.length}`} aria-label={`${visibleMedia.length} image${visibleMedia.length === 1 ? '' : 's'} attached to this post`}>{visibleMedia.map((item) => <button type="button" className="post-media-image" key={item.id} onClick={onOpenDetail} aria-label={`Open ${post.title}`}><img src={mediaUrls[item.id]} alt={`${post.title} — ${item.filename}`} loading="lazy" /></button>)}</div>}<p ref={bodyRef} className={`post-body-copy${truncateBody ? ' post-body-copy-truncated' : ''}`}>{previewBody}</p><div className="post-meta"><span>{post.author_name}</span><span>{post.locality ?? 'Bengaluru'}</span>{post.authority_name && <span>{post.authority_name}</span>}{post.evidence_count > 0 && <button className="evidence-link" onClick={onOpenDetail}>{post.evidence_count} evidence</button>}</div><div className="post-actions"><button type="button" className="post-action-pill" aria-label={`Open comments for ${post.title}`} onClick={onOpenDetail}><PostActionIcon kind="comment" />{post.comment_count} {post.comment_count === 1 ? 'Comment' : 'Comments'}</button><button type="button" className="post-action-pill" onClick={askAgent} disabled={!agentEnabled}><PostActionIcon kind="agent" />Ask Agent</button><button type="button" className={`post-action-pill ${post.is_following ? 'follow-active' : ''}`} aria-pressed={post.is_following} onClick={toggleFollow} disabled={followBusy}><PostActionIcon kind="follow" />Follow</button><button type="button" className={`post-action-pill share-action ${shareCopied ? 'share-action-copied' : ''}`} onClick={() => void share()}><PostActionIcon kind="share" /><span key={shareCopied ? 'copied' : 'share'} className="share-action-label">{shareCopied ? 'Link Copied!' : 'Share'}</span></button>{post.is_owner && <TicketStatusControl post={post} onUpdate={onUpdate} onFeedback={(message, kind) => setFeedback({ message, kind })} />}</div>{post.ranking_reasons.length > 0 && <div className="ranking-explain"><button onClick={() => setShowReason((current) => !current)}>{showReason ? 'Hide why this is here' : 'Why this is here'}</button>{showReason && <span>{post.ranking_reasons.join(' · ')}</span>}</div>}{feedback && <div className={feedback.kind === 'success' ? 'inline-success' : 'inline-error'} role={feedback.kind === 'error' ? 'alert' : 'status'}>{feedback.message}</div>}{commentsOpen && <div className="comments"><div className="comment-list">{comments.length === 0 ? <p className="muted">No comments yet. Add useful local context.</p> : rootComments.map((item) => <CommentThread key={item.id} item={item} comments={comments} onReply={setReplyTo} onRefresh={() => { void getFeedComments(post.id).then(setComments) }} />)}</div>{replyTo && <div className="reply-context">Replying to {replyTo.author_name}<button onClick={() => setReplyTo(null)} aria-label="Cancel reply">×</button></div>}<form className="comment-form" onSubmit={(event) => void submitComment(event)}><label className="sr-only" htmlFor={`comment-${post.id}`}>Add a comment</label><input id={`comment-${post.id}`} value={comment} onChange={(event) => setComment(event.target.value)} placeholder={post.is_locked ? 'This discussion is locked' : replyTo ? 'Reply with useful context…' : 'Add useful context…'} disabled={post.is_locked} /><button className="button button-dark" disabled={!comment.trim() || post.is_locked}>{replyTo ? 'Reply' : 'Comment'}</button></form></div>}</div></article>
}

function CommentThread({ item, comments, onReply, onRefresh }: { item: CivicComment; comments: CivicComment[]; onReply: (comment: CivicComment) => void; onRefresh: () => void }) {
  const [editing, setEditing] = useState(false)
  const [body, setBody] = useState(item.body)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const children = comments.filter((candidate) => candidate.parent_id === item.id)
  const save = async () => {
    try { await editFeedComment(item.post_id, item.id, body); setEditing(false); onRefresh() } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not edit comment') }
  }
  const remove = async () => {
    try { await deleteFeedComment(item.post_id, item.id); onRefresh() } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not delete comment') }
  }
  const report = async () => {
    try { await reportFeedComment(item.post_id, item.id, 'other'); setError('Report received') } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not report comment') }
  }
  return <div className={`comment-branch ${item.parent_id ? 'comment-branch-reply' : ''}`}><article className="comment"><div className="comment-avatar" aria-hidden="true">{initials(item.author_name)}</div><div className="comment-main"><div className="comment-author"><strong>{item.author_name}</strong>{item.is_owner && <span className="comment-owner">You</span>}<span className="comment-time">· {formatTime(item.created_at)}</span></div>{editing ? <div className="comment-edit"><input value={body} onChange={(event) => setBody(event.target.value)} /><button onClick={() => void save()}>Save</button><button onClick={() => setEditing(false)}>Cancel</button></div> : <p>{item.body}</p>}<div className="comment-tools"><button className="comment-action" onClick={() => onReply(item)}><CommentActionIcon kind="reply" />Reply</button>{item.is_owner && <><button className="comment-action" onClick={() => setEditing(true)}>Edit</button>{confirmDelete ? <><button className="comment-action confirm-delete" onClick={() => void remove()}>Confirm delete</button><button className="comment-action" onClick={() => setConfirmDelete(false)}>Cancel</button></> : <button className="comment-action" onClick={() => setConfirmDelete(true)}>Delete</button>}</>}<button className="comment-action" onClick={() => void report()}>Report</button><span className="comment-more" aria-hidden="true">•••</span></div>{error && <small className="comment-error" role="alert">{error}</small>}</div></article>{children.length > 0 && <div className="comment-children">{children.map((child) => <CommentThread key={child.id} item={child} comments={comments} onReply={onReply} onRefresh={onRefresh} />)}</div>}</div>
}

function CommentActionIcon({ kind }: { kind: 'reply' }) {
  if (kind === 'reply') return <svg className="comment-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5.5h14v9H10l-5 4v-4H5z" /></svg>
}

function TicketStatusControl({ post, onUpdate, onFeedback }: { post: CivicPost; onUpdate: (patch: Partial<CivicPost>) => void; onFeedback: (message: string, kind: 'success' | 'error') => void }) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLSpanElement | null>(null)
  useEffect(() => {
    if (!open) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    const outside = (event: PointerEvent) => { if (menuRef.current && !menuRef.current.contains(event.target as Node)) setOpen(false) }
    window.addEventListener('keydown', close); document.addEventListener('pointerdown', outside)
    return () => { window.removeEventListener('keydown', close); document.removeEventListener('pointerdown', outside) }
  }, [open])
  const setStatus = async (status: TicketStatus) => {
    try { const detail = await updateTicketStatus(post.ticket_id, status); setOpen(false); onUpdate({ status: detail.ticket.status }); onFeedback('Ticket status updated.', 'success') } catch (error) { onFeedback(error instanceof Error ? error.message : 'Could not update ticket status.', 'error') }
  }
  return <span ref={menuRef} className="post-status-control"><button aria-expanded={open} aria-haspopup="menu" aria-controls={`status-menu-${post.id}`} onClick={() => setOpen((current) => !current)}>Update status</button>{open && <span id={`status-menu-${post.id}`} className="status-menu" role="menu"><button role="menuitem" onClick={() => void setStatus('in_progress')}>In progress</button><button role="menuitem" onClick={() => void setStatus('resolved_pending_confirmation')}>Resolved pending confirmation</button><button role="menuitem" onClick={() => void setStatus('not_solved')}>Not solved</button><button role="menuitem" onClick={() => void setStatus('reopened')}>Reopen</button></span>}</span>
}

function AgentView({ capabilities, ownerId, ownerName, threads, activeThreadId, initialPrompt, onInitialPromptConsumed, onThreadsChanged, onSelectThread }: { capabilities: AppConfig['capabilities']; ownerId: string; ownerName: string; threads: AgentThread[]; activeThreadId: string | null; initialPrompt: string | null; onInitialPromptConsumed: () => void; onThreadsChanged: () => Promise<AgentThread[]>; onSelectThread: (threadId: string | null) => void }) {
  const [detail, setDetail] = useState<AgentThreadDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [prefill, setPrefill] = useState('')
  const [ticket, setTicket] = useState<TicketDetail | null>(null)
  const [ticketAction, setTicketAction] = useState<Record<string, unknown> | null>(null)
  const [threadQuery, setThreadQuery] = useState('')
  const [agentError, setAgentError] = useState<string | null>(null)
  const [creatingThread, setCreatingThread] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true)
  const pendingPromptRef = useRef<string | null>(null)
  const threadRequestRef = useRef(0)
  const openThread = useCallback(async (threadId: string) => {
    const requestId = ++threadRequestRef.current
    setLoading(true)
    setAgentError(null)
    try {
      const nextDetail = await getThread(threadId)
      if (requestId !== threadRequestRef.current) return
      setDetail(nextDetail)
      onSelectThread(threadId)
      window.history.replaceState({}, '', `${window.location.pathname}${window.location.search}#thread=${encodeURIComponent(threadId)}`)
    } catch (nextError) {
      if (requestId !== threadRequestRef.current) return
      setDetail((current) => current?.thread.id === threadId ? current : null)
      onSelectThread(null)
      setAgentError(nextError instanceof Error ? nextError.message : 'Could not open this conversation')
    } finally {
      if (requestId === threadRequestRef.current) setLoading(false)
    }
  }, [onSelectThread])
  useEffect(() => {
    if (initialPrompt || pendingPromptRef.current || loading || creatingThread) return
    if (activeThreadId) {
      if (detail?.thread.id !== activeThreadId) void openThread(activeThreadId)
      return
    }
  }, [activeThreadId, creatingThread, detail?.thread.id, initialPrompt, loading, openThread, threads])
  const newThread = async (goal = 'Explore a Bengaluru civic issue'): Promise<boolean> => {
    if (creatingThread) return false
    threadRequestRef.current += 1
    setLoading(false)
    setCreatingThread(true); setAgentError(null)
    try {
      const created = await createThread({ title: goal.slice(0, 70), goal })
      setDetail(created)
      onSelectThread(created.thread.id)
      window.history.replaceState({}, '', `${window.location.pathname}${window.location.search}#thread=${encodeURIComponent(created.thread.id)}`)
      await onThreadsChanged()
      return true
    } catch (nextError) { setAgentError(nextError instanceof Error ? nextError.message : 'Could not create a conversation') }
    finally { setCreatingThread(false) }
    return false
  }
  const handleAgentAction = async (action: Record<string, unknown>) => {
    if (action.action === 'workspace_change_approval') {
      const proposalId = typeof action.proposal_id === 'string' ? action.proposal_id : ''
      if (!detail || !proposalId) return
      setAgentError(null)
      try {
        setDetail(await applyWorkspaceChange(detail.thread.id, proposalId))
        await onThreadsChanged()
      } catch (nextError) {
        setAgentError(nextError instanceof Error ? nextError.message : 'Could not apply the approved workspace change')
      }
      return
    }
    if (!capabilities.tickets) {
      setAgentError('Tickets are not enabled in this phase.')
      return
    }
    setTicketAction(action)
  }
  const chooseStarter = (prompt: string) => {
    setPrefill(prompt)
    if (!detail) {
      pendingPromptRef.current = prompt
      void newThread(prompt).finally(() => { pendingPromptRef.current = null })
    }
  }
  useEffect(() => {
    if (!initialPrompt) return
    if (pendingPromptRef.current === initialPrompt) return
    pendingPromptRef.current = initialPrompt
    setPrefill(initialPrompt)
    void newThread(initialPrompt).then((created) => {
      if (created) onInitialPromptConsumed()
    }).finally(() => { pendingPromptRef.current = null })
  }, [initialPrompt, onInitialPromptConsumed])
  const [threadSort, setThreadSort] = useState<'recent' | 'title'>('recent')
  const visibleThreads = [...threads].filter((thread) => thread.title.toLowerCase().includes(threadQuery.toLowerCase())).sort((left, right) => threadSort === 'title' ? left.title.localeCompare(right.title) : right.updated_at.localeCompare(left.updated_at))
  const toggleSidebar = () => setSidebarCollapsed((current) => !current)
  return (
    <div className={`agent-view ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      {agentError && <div className="agent-error" role="alert">{agentError}<button onClick={() => setAgentError(null)}>Dismiss</button></div>}
      <aside id="conversation-list" className="thread-sidebar">
        <div className="thread-sidebar-top"><div><p className="eyebrow">Private workspace</p><h1>Agent</h1></div><button className="new-thread" aria-label="New conversation" disabled={creatingThread} onClick={() => void newThread()}>+</button></div>
        <button className="button button-lime button-full thread-new-button" disabled={creatingThread} onClick={() => void newThread()}>New conversation <span aria-hidden="true">↗</span></button>
        <div className="thread-filters"><label className="sr-only" htmlFor="thread-search">Search conversations</label><input id="thread-search" className="thread-search" value={threadQuery} onChange={(event) => setThreadQuery(event.target.value)} placeholder="Search conversations" /><label className="sr-only" htmlFor="thread-sort">Sort conversations</label><select id="thread-sort" value={threadSort} onChange={(event) => setThreadSort(event.target.value as 'recent' | 'title')}><option value="recent">Recent</option><option value="title">A–Z</option></select></div>
        <div className="thread-list">{visibleThreads.length === 0 ? <p className="muted">{threadQuery ? 'No matching conversations.' : 'Your conversations will appear here.'}</p> : visibleThreads.map((thread) => <ThreadRow key={thread.id} thread={thread} active={detail?.thread.id === thread.id} onOpen={() => void openThread(thread.id)} onRefresh={onThreadsChanged} onRenamed={(next) => { if (detail?.thread.id === thread.id) setDetail(next) }} onArchived={() => { if (detail?.thread.id === thread.id) { setDetail(null); onSelectThread(null) } }} />)}</div>
      </aside>
      <section className="chat-panel" aria-busy={loading || creatingThread}>
        <button type="button" className="sidebar-toggle-panel" aria-controls="conversation-list" aria-expanded={!sidebarCollapsed} aria-label={sidebarCollapsed ? 'Show older conversations' : 'Hide older conversations'} title={sidebarCollapsed ? 'Show older conversations' : 'Hide older conversations'} onClick={toggleSidebar}><span className="sidebar-toggle-icon" aria-hidden="true" /><span className="sidebar-toggle-label">{sidebarCollapsed ? 'Show chats' : 'Hide chats'}</span></button>
        <div className="mobile-thread-tools"><label className="sr-only" htmlFor="mobile-thread-select">Conversation</label><select id="mobile-thread-select" value={detail?.thread.id ?? ''} onChange={(event) => { if (event.target.value) void openThread(event.target.value) }}><option value="">Conversations</option>{threads.map((thread) => <option key={thread.id} value={thread.id}>{thread.title}</option>)}</select><button className="new-thread" aria-label="New conversation" disabled={creatingThread} onClick={() => void newThread()}>+</button></div>
        {loading && <span className="sr-only" role="status" aria-live="polite">Opening your conversation…</span>}
        {detail ? <ChatThread capabilities={capabilities} ownerInitials={initials(ownerName)} detail={detail} ticketId={ticket?.ticket.civitas_ticket_id ?? detail.thread.ticket_id} prefill={prefill} onPrefillConsumed={() => setPrefill('')} onDetail={setDetail} onThreadsChanged={onThreadsChanged} onTicketAction={handleAgentAction} onNewThread={() => void newThread()} /> : <ChatEmpty onStarter={chooseStarter} />}
      </section>
      {capabilities.tickets && (ticket || ticketAction) && createPortal(<TicketDrawer ownerId={ownerId} action={ticketAction} ticket={ticket} threadId={detail?.thread.id ?? null} onClose={() => { setTicket(null); setTicketAction(null) }} onTicket={setTicket} onThreadsChanged={onThreadsChanged} />, document.body)}
    </div>
  )
}

function ThreadRow({ thread, active, onOpen, onRefresh, onRenamed, onArchived }: { thread: AgentThread; active: boolean; onOpen: () => void; onRefresh: () => Promise<AgentThread[]>; onRenamed: (detail: AgentThreadDetail) => void; onArchived: () => void }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(thread.title)
  const [showActions, setShowActions] = useState(false)
  const [confirmArchive, setConfirmArchive] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const actionsRef = useRef<HTMLSpanElement | null>(null)

  useEffect(() => {
    if (!showActions && !confirmArchive) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowActions(false)
        setConfirmArchive(false)
      }
    }
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (actionsRef.current && !actionsRef.current.contains(event.target as Node)) {
        setShowActions(false)
        setConfirmArchive(false)
      }
    }
    window.addEventListener('keydown', closeOnEscape)
    document.addEventListener('pointerdown', closeOnOutsideClick)
    return () => {
      window.removeEventListener('keydown', closeOnEscape)
      document.removeEventListener('pointerdown', closeOnOutsideClick)
    }
  }, [confirmArchive, showActions])

  const saveRename = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const nextTitle = title.trim()
    if (!nextTitle || nextTitle === thread.title) { setEditing(false); return }
    setWorking(true); setError(null)
    try { const next = await updateThread(thread.id, { title: nextTitle }); onRenamed(next); setEditing(false); await onRefresh() }
    catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not rename conversation') }
    finally { setWorking(false) }
  }
  const archive = async () => {
    setWorking(true); setError(null)
    try { await updateThread(thread.id, { status: 'archived' }); onArchived(); await onRefresh() }
    catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not archive conversation') }
    finally { setWorking(false); setConfirmArchive(false) }
  }
  const beginRename = () => {
    setTitle(thread.title)
    setEditing(true)
    setShowActions(false)
    setConfirmArchive(false)
  }
  const requestArchive = () => {
    setShowActions(false)
    setConfirmArchive(true)
  }

  return (
    <div className={`thread-row ${active ? 'active' : ''}`}>
      {editing ? (
        <form className="thread-rename-form" onSubmit={(event) => void saveRename(event)}>
          <label className="sr-only" htmlFor={`rename-${thread.id}`}>Conversation title</label>
          <input id={`rename-${thread.id}`} value={title} onChange={(event) => setTitle(event.target.value)} autoFocus />
          <button type="submit" disabled={working || !title.trim()}>Save</button>
          <button type="button" onClick={() => { setTitle(thread.title); setEditing(false) }}>Cancel</button>
        </form>
      ) : (
        <button className="thread-select" onClick={onOpen}>
          <span className="thread-dot" />
          <span><strong>{thread.title}</strong><small>{thread.message_count} messages {thread.ticket_id ? `· ${thread.ticket_id}` : ''}</small></span>
        </button>
      )}
      {!editing && <span ref={actionsRef} className="thread-row-actions">
        <button
          type="button"
          className="thread-menu-toggle"
          aria-label={`Actions for ${thread.title}`}
          aria-expanded={showActions || confirmArchive}
          aria-haspopup="menu"
          onClick={(event) => { event.stopPropagation(); setConfirmArchive(false); setShowActions((current) => !current) }}
        >⋯</button>
        {showActions && <span className="thread-action-menu" role="menu">
          <button type="button" role="menuitem" onClick={beginRename}>Rename</button>
          <button type="button" role="menuitem" onClick={requestArchive}>Archive</button>
        </span>}
        {confirmArchive && <span className="thread-archive-confirm" role="group" aria-label={`Archive ${thread.title}`}>
          <span className="thread-archive-label">Archive?</span>
          <button type="button" className="confirm-archive" disabled={working} onClick={() => void archive()}>Confirm</button>
          <button type="button" onClick={() => setConfirmArchive(false)}>Cancel</button>
        </span>}
      </span>}
      {error && <span className="thread-row-error" role="alert">{error}</span>}
    </div>
  )
}
function ChatEmpty({ onStarter }: { onStarter: (prompt: string) => void }) {
  const [message, setMessage] = useState('')
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const prompt = message.trim()
    if (!prompt) return
    onStarter(prompt)
  }
  return <div className="chat-empty-shell"><div className="chat-empty"><div className="chat-empty-mark"><BrandMark /></div><p className="eyebrow">Your private agent</p><h1>What are we<br /><em>working on?</em></h1><p>Ask about a civic record, describe an issue, or prepare a complaint. You approve anything public or external.</p><div className="starter-prompts">{starterPrompts.map((prompt) => <button type="button" key={prompt} onClick={() => onStarter(prompt)}>{prompt}<span aria-hidden="true">↗</span></button>)}</div></div><div className="chat-composer-wrap chat-empty-composer-wrap"><form className="chat-composer" onSubmit={submit}><div className="composer-tools" aria-label="Message tools"><span className="attach-button composer-add-button empty-composer-add" aria-hidden="true">＋</span></div><label className="sr-only" htmlFor="empty-agent-message">Message the private Agent</label><textarea id="empty-agent-message" value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder="Tell the Agent what you want to understand or get done… Type / for commands." rows={3} /><button type="submit" className="send-button" disabled={!message.trim()} aria-label="Send message" title="Send message"><span aria-hidden="true">↑</span></button></form><div className="composer-hint-row"><span>Enter sends · Shift + Enter for a new line</span></div></div></div>
}

type StagedAttachment = { id: string; file: File; previewUrl: string | null; uploadedId?: string }
type PendingUserMessage = { content: string; attachments: StagedAttachment[]; createdAt: string }
type StoredAttachment = { key: string; threadId: string; id: string; filename: string; contentType: string; lastModified: number; file: Blob }
const ATTACHMENT_DB_NAME = 'civitasx-attachment-drafts'

function openAttachmentDraftDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') { reject(new Error('Attachment recovery is unavailable in this browser')); return }
    const request = indexedDB.open(ATTACHMENT_DB_NAME, 1)
    request.onupgradeneeded = () => { request.result.createObjectStore('files', { keyPath: 'key' }) }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('Could not open attachment recovery'))
  })
}

async function readAttachmentDrafts(threadId: string): Promise<StagedAttachment[]> {
  const db = await openAttachmentDraftDb()
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('files', 'readonly')
    const request = transaction.objectStore('files').getAll()
    request.onsuccess = () => {
      const restored = (request.result as StoredAttachment[]).filter((item) => item.threadId === threadId).map((item) => {
        const file = new File([item.file], item.filename, { type: item.contentType, lastModified: item.lastModified })
        return { id: item.id, file, previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : null }
      })
      resolve(restored)
    }
    request.onerror = () => reject(request.error ?? new Error('Could not restore attachments'))
    transaction.oncomplete = () => db.close()
    transaction.onerror = () => reject(transaction.error ?? new Error('Could not restore attachments'))
  })
}

async function saveAttachmentDrafts(threadId: string, attachments: StagedAttachment[]): Promise<void> {
  const db = await openAttachmentDraftDb()
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('files', 'readwrite')
    const store = transaction.objectStore('files')
    const existing = store.getAll()
    existing.onsuccess = () => {
      for (const item of existing.result as StoredAttachment[]) if (item.threadId === threadId) store.delete(item.key)
      for (const item of attachments) store.put({ key: `${threadId}:${item.id}`, threadId, id: item.id, filename: item.file.name, contentType: item.file.type, lastModified: item.file.lastModified, file: item.file } satisfies StoredAttachment)
    }
    existing.onerror = () => reject(existing.error ?? new Error('Could not save attachments'))
    transaction.oncomplete = () => { db.close(); resolve() }
    transaction.onerror = () => { db.close(); reject(transaction.error ?? new Error('Could not save attachments')) }
  })
}

async function clearAttachmentDrafts(threadId: string): Promise<void> {
  const db = await openAttachmentDraftDb()
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('files', 'readwrite')
    const store = transaction.objectStore('files')
    const existing = store.getAll()
    existing.onsuccess = () => { for (const item of existing.result as StoredAttachment[]) if (item.threadId === threadId) store.delete(item.key) }
    existing.onerror = () => reject(existing.error ?? new Error('Could not clear attachments'))
    transaction.oncomplete = () => { db.close(); resolve() }
    transaction.onerror = () => { db.close(); reject(transaction.error ?? new Error('Could not clear attachments')) }
  })
}

type LiveTurnItem = {
  id: string
  kind: 'status' | 'tool' | 'plan'
  label: string
  state: 'pending' | 'active' | 'completed' | 'error'
}

type TurnState = 'idle' | 'running' | 'stopped' | 'failed'

function formatLiveToolName(value: string): string {
  return value.replaceAll('_', ' ')
}

function hasProviderFailure(detail: AgentThreadDetail): boolean {
  const latestAssistant = [...detail.messages].reverse().find((message) => message.role === 'assistant')
  return Boolean(latestAssistant?.parts.some((part) => part.type === 'status' && part.data?.status === 'provider_error'))
}

function ChatThread({ capabilities, ownerInitials, detail, ticketId, prefill, onPrefillConsumed, onDetail, onThreadsChanged, onTicketAction, onNewThread }: { capabilities: AppConfig['capabilities']; ownerInitials: string; detail: AgentThreadDetail; ticketId: string | null; prefill: string; onPrefillConsumed: () => void; onDetail: (detail: AgentThreadDetail) => void; onThreadsChanged: () => Promise<AgentThread[]>; onTicketAction: (action: Record<string, unknown>) => void; onNewThread: () => void }) {
  const [message, setMessage] = useState(prefill)
  const [attachments, setAttachments] = useState<StagedAttachment[]>([])
  const [savedAttachments, setSavedAttachments] = useState<Attachment[]>([])
  const [uploadedAttachments, setUploadedAttachments] = useState<Record<string, Attachment>>({})
  const [isSending, setIsSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [phase, setPhase] = useState<string | null>(null)
  const [liveTurn, setLiveTurn] = useState<LiveTurnItem[]>([])
  const [turnState, setTurnState] = useState<TurnState>('idle')
  const [pendingUserMessage, setPendingUserMessage] = useState<PendingUserMessage | null>(null)
  const [lastFailedPrompt, setLastFailedPrompt] = useState<string | null>(null)
  const [draftRestored, setDraftRestored] = useState(false)
  const [attachmentsRestored, setAttachmentsRestored] = useState(false)
  const [streamingAssistant, setStreamingAssistant] = useState('')
  const [location, setLocation] = useState<AgentLocation | null>(null)
  const [actionsOpen, setActionsOpen] = useState(false)
  const [manualLocationOpen, setManualLocationOpen] = useState(false)
  const [manualLocationText, setManualLocationText] = useState('')
  const [locationPermissionMessage, setLocationPermissionMessage] = useState('')
  const fileRef = useRef<HTMLInputElement | null>(null)
  const formRef = useRef<HTMLFormElement | null>(null)
  const composerRef = useRef<HTMLDivElement | null>(null)
  const messageListRef = useRef<HTMLDivElement | null>(null)
  const scrollFrameRef = useRef<number | null>(null)
  const followOutputRef = useRef(true)
  const requestRef = useRef<AbortController | null>(null)
  const clientMessageIdRef = useRef<string | null>(null)
  const submittedContentRef = useRef<string | null>(null)
  const attachmentsRef = useRef<StagedAttachment[]>([])
  const streamingTargetRef = useRef('')
  const streamingVisibleRef = useRef('')
  const streamingDoneRef = useRef(false)
  const streamingTimerRef = useRef<number | null>(null)
  const draftKey = `civitas.agent.draft.${detail.thread.id}`
  const pumpStreamingAssistant = useCallback(() => {
    if (streamingTimerRef.current !== null) return
    const revealNextWord = () => {
      const target = streamingTargetRef.current
      const visible = streamingVisibleRef.current
      if (visible.length >= target.length) {
        streamingTimerRef.current = null
        if (streamingDoneRef.current) {
          streamingTargetRef.current = ''
          streamingVisibleRef.current = ''
          setStreamingAssistant('')
        }
        return
      }
      const remainder = target.slice(visible.length)
      const match = remainder.match(/^\s*\S+(?:\s+|$)/)
      const segment = match?.[0] ?? remainder
      const isUnfinishedWord = !streamingDoneRef.current
        && segment.length === remainder.length
        && !/\s$/.test(segment)
      if (isUnfinishedWord) return
      const nextVisible = visible + segment
      streamingVisibleRef.current = nextVisible
      setStreamingAssistant(nextVisible)
      streamingTimerRef.current = window.setTimeout(() => {
        streamingTimerRef.current = null
        revealNextWord()
      }, 24)
    }
    revealNextWord()
  }, [])
  const resetStreamingAssistant = useCallback(() => {
    if (streamingTimerRef.current !== null) window.clearTimeout(streamingTimerRef.current)
    streamingTimerRef.current = null
    streamingTargetRef.current = ''
    streamingVisibleRef.current = ''
    streamingDoneRef.current = false
    setStreamingAssistant('')
  }, [])
  const appendStreamingAssistant = useCallback((delta: string) => {
    streamingTargetRef.current += delta
    pumpStreamingAssistant()
  }, [pumpStreamingAssistant])
  const finishStreamingAssistant = useCallback((nextDetail: AgentThreadDetail) => {
    const assistant = [...nextDetail.messages].reverse().find((message) => message.role === 'assistant')
    if (assistant?.content) streamingTargetRef.current = assistant.content
    streamingDoneRef.current = true
    onDetail(nextDetail)
    pumpStreamingAssistant()
  }, [onDetail, pumpStreamingAssistant])
  useEffect(() => {
    if (prefill) { setMessage(prefill); onPrefillConsumed(); return }
    try { const saved = window.localStorage.getItem(draftKey); if (saved) { setMessage(saved); setDraftRestored(true) } else { setMessage(''); setDraftRestored(false) } } catch { setMessage('') }
  }, [draftKey, onPrefillConsumed, prefill])
  useEffect(() => { try { if (message.trim()) window.localStorage.setItem(draftKey, message); else window.localStorage.removeItem(draftKey) } catch { /* local draft is best effort */ } }, [draftKey, message])
  useEffect(() => {
    let active = true
    attachmentsRef.current.forEach((item) => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl) })
    attachmentsRef.current = []
    setAttachments([])
    setAttachmentsRestored(false)
    setUploadedAttachments({})
    void (async () => {
      try {
        const restored = await readAttachmentDrafts(detail.thread.id)
        if (active && restored.length) { setAttachments(restored); setDraftRestored(true) }
      } catch (nextError) { if (active && nextError instanceof Error && nextError.message !== 'Attachment recovery is unavailable in this browser') setError(nextError.message) }
      if (active) setAttachmentsRestored(true)
    })()
    return () => { active = false }
  }, [detail.thread.id])
  useEffect(() => {
    if (!attachmentsRestored) return
    void saveAttachmentDrafts(detail.thread.id, attachments).catch(() => setError('Could not save attachment recovery data; files remain staged for this session.'))
  }, [attachments, attachmentsRestored, detail.thread.id])
  useEffect(() => { attachmentsRef.current = attachments }, [attachments])
  useEffect(() => () => { attachmentsRef.current.forEach((item) => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl) }) }, [detail.thread.id])
  useEffect(() => { void getThreadAttachments(detail.thread.id).then(setSavedAttachments).catch(() => setSavedAttachments([])) }, [detail.thread.id])
  useEffect(() => {
    requestRef.current?.abort()
    requestRef.current = null
    clientMessageIdRef.current = null
    submittedContentRef.current = null
    followOutputRef.current = true
    setError(null)
    setPhase(null)
    setLiveTurn([])
    setTurnState('idle')
    setActionsOpen(false)
    setManualLocationOpen(false)
    setManualLocationText('')
    setLocationPermissionMessage('')
    setPendingUserMessage(null)
    setLastFailedPrompt(null)
    resetStreamingAssistant()
  }, [detail.thread.id, resetStreamingAssistant])
  useEffect(() => {
    if (!actionsOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setActionsOpen(false)
    }
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (composerRef.current && !composerRef.current.contains(event.target as Node)) {
        setActionsOpen(false)
      }
    }
    document.addEventListener('keydown', closeOnEscape)
    document.addEventListener('pointerdown', closeOnOutsidePointer)
    return () => {
      document.removeEventListener('keydown', closeOnEscape)
      document.removeEventListener('pointerdown', closeOnOutsidePointer)
    }
  }, [actionsOpen])
  const queueScrollToLatest = () => {
    const list = messageListRef.current
    if (!list || !followOutputRef.current || scrollFrameRef.current !== null) return
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null
      list.scrollTo({ top: list.scrollHeight, behavior: 'auto' })
    })
  }
  useEffect(() => {
    queueScrollToLatest()
  }, [detail.messages.length, liveTurn.length, pendingUserMessage?.createdAt, phase, streamingAssistant.length, turnState])
  useEffect(() => {
    const composer = composerRef.current
    if (!composer || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(queueScrollToLatest)
    observer.observe(composer)
    return () => observer.disconnect()
  }, [])
  useEffect(() => () => {
    if (scrollFrameRef.current !== null) window.cancelAnimationFrame(scrollFrameRef.current)
  }, [])
  const handleMessageScroll = () => {
    const list = messageListRef.current
    if (!list) return
    const distanceFromLatest = list.scrollHeight - list.scrollTop - list.clientHeight
    followOutputRef.current = distanceFromLatest < 96
  }
  const updateLiveStatus = (label: string) => {
    setPhase(label)
    setLiveTurn((current) => [...current.filter((item) => item.id !== 'turn-status'), { id: 'turn-status', kind: 'status', label, state: 'active' }])
  }
  const sendMessage = async (content: string) => {
    const submittedContent = content.trim()
    if (!submittedContent || isSending) return
    if (/^\/(?:new|reset)\s*$/i.test(submittedContent)) {
      setMessage('')
      onNewThread()
      return
    }
    followOutputRef.current = true
    setIsSending(true)
    setTurnState('running')
    setError(null)
    setLastFailedPrompt(null)
    setActionsOpen(false)
    setManualLocationOpen(false)
    setLocationPermissionMessage('')
    resetStreamingAssistant()
    const clientMessageId = clientMessageIdRef.current ?? (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`)
    clientMessageIdRef.current = clientMessageId
    submittedContentRef.current = submittedContent
    const controller = new AbortController()
    requestRef.current = controller
    const uploadedThisAttempt: Attachment[] = []
    const stagedAttachments = [...attachments]
    setPendingUserMessage({ content: submittedContent, attachments: stagedAttachments, createdAt: new Date().toISOString() })
    setMessage('')
    try { window.localStorage.removeItem(draftKey) } catch { /* ignore */ }
    setDraftRestored(false)
    let requestStarted = false
    let turnCompleted = false
    try {
      updateLiveStatus('Preparing your local evidence…')
      setLiveTurn([{ id: 'turn-status', kind: 'status', label: 'Preparing your local evidence…', state: 'active' }])
      const uploadResults = await Promise.all(stagedAttachments.map(async (staged) => {
        const existing = staged.uploadedId ? uploadedAttachments[staged.uploadedId] : undefined
        if (existing) return { staged, attachment: existing }
        const attachment = await uploadAgentAttachment(detail.thread.id, staged.file, controller.signal)
        uploadedThisAttempt.push(attachment)
        setUploadedAttachments((current) => ({ ...current, [attachment.id]: attachment }))
        setAttachments((current) => current.map((item) => item.id === staged.id ? { ...item, uploadedId: attachment.id } : item))
        return { staged, attachment }
      }))
      const uploaded = uploadResults.map((result) => result.attachment)
      if (uploadResults.length) {
        const uploadedByStagedId = new Map(uploadResults.map((result) => [result.staged.id, result.attachment.id]))
        setAttachments((current) => current.map((item) => {
          const uploadedId = uploadedByStagedId.get(item.id)
          return uploadedId ? { ...item, uploadedId } : item
        }))
      }
      if (uploaded.length) {
        setSavedAttachments((current) => {
          const unique = new Map<string, Attachment>()
          for (const item of [...uploaded, ...current]) unique.set(item.id, item)
          return [...unique.values()]
        })
      }
      requestStarted = true
      const next = await sendAgentMessageStream(
        detail.thread.id,
        submittedContent,
        uploaded.map((item) => item.id),
        updateLiveStatus,
        controller.signal,
        (event) => {
          if (event.event === 'message' && typeof event.data.delta === 'string') {
            appendStreamingAssistant(event.data.delta)
          }
          if (event.event === 'plan' && Array.isArray(event.data.steps)) {
            const planItems = event.data.steps.flatMap((raw, index) => {
              if (!raw || typeof raw !== 'object') return []
              const step = raw as Record<string, unknown>
              const state: LiveTurnItem['state'] = step.state === 'completed' || step.state === 'error' || step.state === 'active' || step.state === 'pending'
                ? step.state
                : 'pending'
              return [{ id: `plan:${String(step.id ?? index)}`, kind: 'plan' as const, label: String(step.label ?? 'Agent step'), state }]
            })
            setLiveTurn((current) => [...current.filter((item) => item.kind !== 'plan'), ...planItems])
          }
          if (event.event === 'tool_call' && typeof event.data.tool_name === 'string') {
            const toolName = event.data.tool_name
            const toolId = typeof event.data.tool_call_id === 'string' ? event.data.tool_call_id : `${toolName}:${String(event.data.iteration ?? '0')}`
            setLiveTurn((current) => [...current.filter((item) => item.id !== toolId), { id: toolId, kind: 'tool', label: toolName, state: 'active' }])
          }
          if (event.event === 'tool_result' && typeof event.data.tool_name === 'string') {
            const toolName = event.data.tool_name
            const toolId = typeof event.data.tool_call_id === 'string' ? event.data.tool_call_id : `${toolName}:${String(event.data.iteration ?? '0')}`
            const state = event.data.status === 'error' ? 'error' : 'completed'
            setLiveTurn((current) => {
              const existing = current.some((item) => item.id === toolId)
              if (!existing) return [...current, { id: toolId, kind: 'tool', label: toolName, state }]
              return current.map((item) => item.id === toolId ? { ...item, state } : item)
            })
          }
        },
        clientMessageId,
        location,
        'auto',
      )
      setPendingUserMessage(null)
      finishStreamingAssistant(next)
      if (hasProviderFailure(next)) {
        throw new Error('The configured providers did not complete this turn. Retry when a provider is reachable.')
      }
      clientMessageIdRef.current = null
      submittedContentRef.current = null
      setAttachments([])
      setLocation(null)
      void clearAttachmentDrafts(detail.thread.id).catch(() => undefined)
      setUploadedAttachments({})
      stagedAttachments.forEach((item) => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl) })
      setDraftRestored(false)
      setTurnState('idle')
      setLiveTurn([])
      setLastFailedPrompt(null)
      turnCompleted = true
      await onThreadsChanged()
    } catch (nextError) {
      const stopped = nextError instanceof DOMException && nextError.name === 'AbortError'
      if (!requestStarted && uploadedThisAttempt.length) {
        await Promise.allSettled(uploadedThisAttempt.map((attachment) => deleteAgentAttachment(attachment.id)))
        const removed = new Set(uploadedThisAttempt.map((attachment) => attachment.id))
        setUploadedAttachments((current) => Object.fromEntries(Object.entries(current).filter(([id]) => !removed.has(id))))
        setAttachments((current) => current.map((item) => removed.has(item.uploadedId ?? '') ? { ...item, uploadedId: undefined } : item))
      }
      if (!stopped) {
        clientMessageIdRef.current = null
        submittedContentRef.current = null
      }
      setLastFailedPrompt(submittedContent)
      setTurnState(stopped ? 'stopped' : 'failed')
      resetStreamingAssistant()
      setPhase(stopped ? 'Response stopped · edit or retry' : 'Turn failed · retry when ready')
      setError(stopped ? 'Response stopped. You can edit or retry this turn.' : nextError instanceof Error ? nextError.message : 'Could not send the message')
    } finally {
      requestRef.current = null
      setIsSending(false)
      if (turnCompleted) setPhase(null)
    }
  }
  const send = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    await sendMessage(message)
  }
  const retryLastTurn = () => {
    if (lastFailedPrompt) void sendMessage(lastFailedPrompt)
  }
  const addFiles = async (files: FileList | null) => {
    if (!files) return
    setError(null)
    const available = Math.max(0, 4 - attachments.length)
    if (available === 0) { setError('You can stage up to 4 files per message.'); return }
    for (const file of Array.from(files).slice(0, available)) {
      if (file.size > 15 * 1024 * 1024) { setError(`${file.name} is larger than the 15 MB upload limit.`); continue }
      const previewUrl = file.type.startsWith('image/') ? URL.createObjectURL(file) : null
      setAttachments((current) => [...current, { id: `${file.name}-${file.lastModified}-${crypto.randomUUID?.() ?? Math.random()}`, file, previewUrl }])
    }
    if (files.length > available) setError('Only 4 files can be staged per message.')
  }
  const captureLocation = () => {
    setActionsOpen(false)
    if (!navigator.geolocation) {
      setManualLocationOpen(true)
      setLocationPermissionMessage('This browser does not support device location. Add a landmark or address below instead.')
      setError(null)
      return
    }
    setError(null)

    const startLocationRequest = () => {
      setLocationPermissionMessage('Waiting for browser permission…')
      navigator.geolocation.getCurrentPosition(
        (position) => {
          setLocation({
            label: 'Current device location',
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            accuracy_m: position.coords.accuracy,
            source: 'browser',
          })
          setManualLocationOpen(false)
          setLocationPermissionMessage('')
        },
        (positionError) => {
          setManualLocationOpen(true)
          setLocationPermissionMessage(
            positionError.code === 1
              ? 'Location access is blocked for this site. Allow Location for localhost in the browser site settings, then retry—or add a landmark below.'
              : 'Device location is unavailable right now. Retry, or add a landmark or address below.',
          )
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
      )
    }

    try {
      const permissions = navigator.permissions
      if (!permissions?.query) {
        startLocationRequest()
        return
      }
      void permissions.query({ name: 'geolocation' }).then((permission) => {
        if (permission.state === 'denied') {
          setManualLocationOpen(true)
          setLocationPermissionMessage(
            'Location access is blocked for this site. Allow Location for localhost in the browser site settings, then retry—or add a landmark below.',
          )
          return
        }
        startLocationRequest()
      }).catch(startLocationRequest)
    } catch {
      startLocationRequest()
    }
  }
  const openManualLocation = () => {
    setActionsOpen(false)
    setError(null)
    setLocationPermissionMessage('')
    setManualLocationOpen(true)
  }
  const attachManualLocation = () => {
    const value = manualLocationText.trim()
    if (!value) {
      setError('Enter a street, sector, landmark, or address first.')
      return
    }
    setLocation({ label: value, address: value, source: 'user' })
    setManualLocationOpen(false)
    setManualLocationText('')
    setLocationPermissionMessage('')
    setError(null)
  }
  const openFilePicker = () => {
    setActionsOpen(false)
    fileRef.current?.click()
  }
  const removeStagedAttachment = async (attachment: StagedAttachment) => {
    if (attachment.uploadedId) {
      try {
        await deleteAgentAttachment(attachment.uploadedId)
        setUploadedAttachments((current) => {
          const next = { ...current }
          delete next[attachment.uploadedId as string]
          return next
        })
      } catch (nextError) {
        setError(nextError instanceof Error ? nextError.message : 'Could not remove the uploaded draft')
        return
      }
    }
    if (attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl)
    setAttachments((current) => current.filter((item) => item.id !== attachment.id))
  }
  const linkedTicketId = ticketId
  const commandOptions = [
    ['/research', 'Search official records'],
    ['/complaint', 'Prepare a complaint ticket'],
    ['/sources', 'Show the evidence trail'],
    ['/memory', 'Explain local preferences'],
    ['/new', 'Start a fresh thread'],
    ['/help', 'Show all commands'],
  ] as const
  const commandPaletteOpen = message.startsWith('/') && !message.includes(' ') && !isSending
  const pendingMessage: AgentMessage | null = pendingUserMessage ? {
    id: `pending-${pendingUserMessage.createdAt}`,
    thread_id: detail.thread.id,
    role: 'user',
    content: pendingUserMessage.content,
    parts: pendingUserMessage.attachments.map((attachment) => ({ type: 'attachment', attachment_id: attachment.id, text: attachment.file.name })),
    created_at: pendingUserMessage.createdAt,
  } : null
  let latestAssistantIndex = -1
  for (let index = detail.messages.length - 1; index >= 0; index -= 1) {
    if (detail.messages[index].role === 'assistant') { latestAssistantIndex = index; break }
  }
  const hideFinalWhileStreaming = Boolean(
    streamingAssistant
    && streamingDoneRef.current
    && latestAssistantIndex >= 0
    && detail.messages[latestAssistantIndex].content === streamingTargetRef.current,
  )
  const renderedMessages = hideFinalWhileStreaming
    ? detail.messages.filter((_, index) => index !== latestAssistantIndex)
    : detail.messages
  return (
    <div className="chat-thread">
      <div ref={messageListRef} className="message-list" onScroll={handleMessageScroll}>
        {detail.messages.length === 0 && !pendingMessage && <div className="assistant-message welcome"><span className="message-avatar">CX</span><div><strong>I’m ready when you are.</strong><p>Research a record, explain an official document, or prepare a complaint. You approve anything public or external.</p><div className="welcome-hints"><button onClick={() => setMessage('/research safer walking routes near my locality')}>Research a record</button><button onClick={() => setMessage('/complaint broken streetlight near Indiranagar')}>Prepare a complaint</button></div></div></div>}
        {renderedMessages.map((item) => <MessageBubble key={item.id} message={item} userInitials={ownerInitials} ticketId={linkedTicketId} onReuse={setMessage} onTicketAction={onTicketAction} />)}
        {pendingMessage && <MessageBubble key={pendingMessage.id} message={pendingMessage} userInitials={ownerInitials} ticketId={linkedTicketId} onReuse={setMessage} onTicketAction={onTicketAction} />}
        {streamingAssistant && <StreamingAssistant content={streamingAssistant} />}
        {turnState !== 'idle' && !streamingAssistant && <HermesLiveTurn state={turnState} phase={phase} items={liveTurn} onRetry={lastFailedPrompt ? retryLastTurn : undefined} />}
      </div>
      <div ref={composerRef} className="chat-composer-wrap">
        {phase && <div className="composer-privacy"><strong className="agent-phase">{phase}</strong></div>}
        {commandPaletteOpen && <div className="hermes-command-menu" role="group" aria-label="Agent commands">{commandOptions.map(([command, description]) => <button key={command} type="button" onClick={() => setMessage(`${command} `)}><code>{command}</code><span>{description}</span></button>)}</div>}
        {draftRestored && <div className="draft-restored" role="status">Continue your saved request · attachments are stored privately until you send.</div>}
        {savedAttachments.length > 0 && <div className="attachment-history"><span className="eyebrow">Sent thread evidence</span>{savedAttachments.map((attachment) => <span className="attachment-chip saved" key={attachment.id}><span aria-hidden="true">⊙</span>{attachment.filename}</span>)}</div>}
        {attachments.length > 0 && <div className="attachment-tray">{attachments.map((attachment) => <span className="attachment-chip" key={attachment.id}>{attachment.previewUrl && <img className="attachment-thumb" src={attachment.previewUrl} alt={`Preview of ${attachment.file.name}`} />}{attachment.file.name}<button type="button" aria-label={`Remove ${attachment.file.name}`} onClick={() => void removeStagedAttachment(attachment)}>×</button></span>)}</div>}
        {error && <div className="inline-error chat-inline-error" role="alert"><span>{error}</span>{lastFailedPrompt && !isSending && <button type="button" className="inline-error-action" onClick={retryLastTurn}>Retry this turn</button>}</div>}
        {manualLocationOpen && <div className="location-fallback" role="group" aria-label="Add a location manually">
          <div className="location-fallback-copy"><strong>{locationPermissionMessage ? 'Device location needs attention' : 'Add a location manually'}</strong><span>{locationPermissionMessage || 'Use a street, sector, landmark, or full address.'}</span></div>
          <div className="location-fallback-controls"><input value={manualLocationText} onChange={(event) => { setManualLocationText(event.target.value); setError(null) }} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); attachManualLocation() } }} placeholder="e.g. HSR Layout, near Microsoft office" aria-label="Street, landmark, or address" autoFocus /><button type="button" className="button button-dark" onClick={attachManualLocation}>Attach</button><button type="button" className="button button-outline location-retry" onClick={captureLocation}>Retry device location</button><button type="button" className="location-fallback-cancel" onClick={() => { setManualLocationOpen(false); setManualLocationText(''); setLocationPermissionMessage('') }}>Cancel</button></div>
        </div>}
        <form ref={formRef} className="chat-composer" onSubmit={(event) => void send(event)}>
          <div className="composer-tools" aria-label="Message tools">
            <div className="composer-action-menu-wrap">
              <button type="button" className="attach-button composer-add-button" onClick={() => setActionsOpen((current) => !current)} disabled={isSending} aria-expanded={actionsOpen} aria-haspopup="menu" aria-label="Add to message" title="Add to message">＋</button>
              {actionsOpen && <div className="composer-action-menu" role="menu" aria-label="Add to message">
                {capabilities.attachments && <button type="button" role="menuitem" onClick={openFilePicker}><span className="composer-action-icon" aria-hidden="true">＋</span><span><strong>Attach file</strong><small>Add a photo or document</small></span></button>}
                <button type="button" role="menuitem" onClick={captureLocation}><span className="composer-action-icon" aria-hidden="true">⌖</span><span><strong>Use current location</strong><small>Ask for device location access</small></span></button>
                <button type="button" role="menuitem" onClick={openManualLocation}><span className="composer-action-icon" aria-hidden="true">⌂</span><span><strong>Type a location</strong><small>Add a street, landmark, or address</small></span></button>
              </div>}
            </div>
            {location && <span className="attachment-chip saved location-chip"><span aria-hidden="true">⌖</span>Location attached<button type="button" aria-label="Remove attached location" onClick={() => setLocation(null)}>×</button></span>}
            <input ref={fileRef} type="file" multiple accept="image/*,audio/*,.pdf,.txt,.csv,.doc,.docx" onChange={(event) => void addFiles(event.target.files)} className="sr-only" />
          </div>
          <textarea value={message} onChange={(event) => { const nextValue = event.target.value; setMessage(nextValue); setDraftRestored(false); if (submittedContentRef.current && nextValue.trim() !== submittedContentRef.current) { clientMessageIdRef.current = null; submittedContentRef.current = null }; if (lastFailedPrompt && nextValue.trim() !== lastFailedPrompt) setLastFailedPrompt(null) }} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); formRef.current?.requestSubmit() } }} placeholder="Tell the Agent what you want to understand or get done… Type / for commands." rows={3} />
          <button type={isSending ? 'button' : 'submit'} className={`send-button ${isSending ? 'stop-button' : ''}`} onClick={isSending ? () => requestRef.current?.abort() : undefined} disabled={!isSending && !message.trim()} aria-label={isSending ? 'Stop response' : 'Send message'} title={isSending ? 'Stop response' : 'Send message'}>{isSending ? <><span aria-hidden="true">■</span><span className="sr-only">Stop response</span></> : <><span aria-hidden="true">↑</span><span className="sr-only">Send message</span></>}</button>
        </form>
        <div className="composer-hint-row"><span>{isSending ? 'Stop anytime · edit and send again' : 'Enter sends · Shift + Enter for a new line'}</span></div>
      </div>
    </div>
  )
}

function HermesLiveTurn({ state, phase, items, onRetry }: { state: Exclude<TurnState, 'idle'>; phase: string | null; items: LiveTurnItem[]; onRetry?: () => void }) {
  const activeTool = items.some((item) => item.kind === 'tool' && item.state === 'active')
  const stateLabel = state === 'running' ? 'Thinking' : state === 'stopped' ? 'Response stopped' : 'Turn failed'
  if (state === 'running') return <div className="agent-thinking" role="status" aria-live="polite"><span className="message-avatar">CX</span><div className="agent-thinking-copy"><strong>{stateLabel}<span className="thinking-dots" aria-hidden="true">...</span></strong><span>{activeTool ? `Using ${formatLiveToolName(items.find((item) => item.kind === 'tool' && item.state === 'active')?.label ?? 'a civic tool')}` : phase ?? 'Working through the request'}</span></div></div>
  return <div className={`agent-turn-state ${state}`} role="status" aria-live="polite"><strong>{stateLabel}</strong><span>{phase ?? (state === 'stopped' ? 'Your draft is preserved.' : 'You can retry this turn.')}</span>{onRetry && <button type="button" className="message-reuse assistant-reuse" onClick={onRetry}>Retry this turn</button>}</div>
}

type AssistantContentSegment = { kind: 'thinking' | 'answer'; content: string }

function splitAssistantThinking(content: string): AssistantContentSegment[] {
  const segments: AssistantContentSegment[] = []
  const thinkingPattern = /<thinking\b[^>]*>([\s\S]*?)(?:<\/thinking>|$)/gi
  let cursor = 0
  let match: RegExpExecArray | null

  while ((match = thinkingPattern.exec(content))) {
    const before = content.slice(cursor, match.index)
    if (before.trim()) segments.push({ kind: 'answer', content: before })
    const thinking = match[1] ?? ''
    if (thinking.trim()) segments.push({ kind: 'thinking', content: thinking })
    cursor = thinkingPattern.lastIndex
  }

  const after = content.slice(cursor)
  if (after.trim()) segments.push({ kind: 'answer', content: after })
  return segments.length > 0 ? segments : [{ kind: 'answer', content }]
}

function MarkdownContent({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ table: ({ children }) => <div className="markdown-table-wrap"><table>{children}</table></div> }}>{content}</ReactMarkdown>
}

function AssistantMarkdown({ content }: { content: string }) {
  return <div className="assistant-markdown">{splitAssistantThinking(content).map((segment, index) => segment.kind === 'thinking' ? <div className="assistant-thinking-text" key={`thinking-${index}`}><MarkdownContent content={segment.content} /></div> : <MarkdownContent content={segment.content} key={`answer-${index}`} />)}</div>
}

function StreamingAssistant({ content }: { content: string }) {
  const segments = splitAssistantThinking(content)
  return <div className="assistant-message streaming-message" aria-live="polite"><span className="message-avatar">CX</span><div className="assistant-message-stack"><div className="message-content">{segments.map((segment, index) => <p className={segment.kind === 'thinking' ? 'assistant-thinking-text' : undefined} key={`${segment.kind}-${index}`}>{segment.content}{index === segments.length - 1 && <span className="streaming-cursor" aria-hidden="true" />}</p>)}</div></div></div>
}

function MessageBubble({ message, userInitials, ticketId, onReuse, onTicketAction }: { message: AgentMessage; userInitials: string; ticketId: string | null; onReuse: (content: string) => void; onTicketAction: (action: Record<string, unknown>) => void }) {
  if (message.role === 'user') {
    const attachments = message.parts.filter((part) => part.type === 'attachment')
    return <div className="user-message"><div className="message-content"><p>{message.content}</p></div><span className="message-avatar message-avatar-user" aria-hidden="true">{userInitials}</span>{attachments.length > 0 && <div className="message-supporting user-message-attachments">{attachments.map((part) => <span className="message-attachment" key={part.attachment_id}>{part.text ?? 'Attached evidence'}</span>)}<button className="message-reuse" onClick={() => onReuse(message.content)}>Edit / use again</button></div>}</div>
  }
  const citations = message.parts.filter((part) => part.type === 'citation')
  const actions = message.parts.filter((part) => part.type === 'action')
  const assistantContent = message.content.trim()
    || (message.parts.some((part) => part.type === 'status' && part.data?.status === 'provider_error')
      ? 'The agent could not complete this turn. Your message is saved; please retry.'
      : 'The agent returned an empty response. Please retry this turn.')
  return <div className="assistant-message"><span className="message-avatar">CX</span><div className="assistant-message-stack"><div className="message-content"><AssistantMarkdown content={assistantContent} /></div>{citations.length > 0 && <div className="message-supporting message-citations"><p className="eyebrow">Source passages · verified record</p>{citations.map((part, index) => { const passage = typeof part.data?.passage === 'string' ? part.data.passage : ''; const original = typeof part.data?.original_passage === 'string' ? part.data.original_passage : ''; const title = typeof part.data?.title === 'string' ? part.data.title : part.text ?? 'Source passage'; const authority = typeof part.data?.authority === 'string' ? part.data.authority : 'Official authority'; const url = typeof part.data?.url === 'string' ? part.data.url : ''; const publishedAt = typeof part.data?.published_at === 'string' ? part.data.published_at : ''; const retrievedAt = typeof part.data?.retrieved_at === 'string' ? part.data.retrieved_at : ''; const dateBasis = typeof part.data?.date_basis === 'string' ? part.data.date_basis : ''; return <details key={`${part.attachment_id}-${index}`}><summary>{title}</summary><div className="citation-meta"><span>{authority}</span><span>Page {typeof part.data?.page === 'number' ? part.data.page : '—'}</span>{publishedAt ? <span>Published {formatDate(publishedAt)}</span> : <span>Checked {retrievedAt ? formatDate(retrievedAt) : 'unknown'}{dateBasis === 'retrieved_at_fallback' ? ' · no publication date' : ''}</span>}{url && <a href={url} target="_blank" rel="noreferrer">Official page ↗</a>}</div><p>{passage || 'No extractable passage was available for this source.'}</p>{original && <small>Original text: {original}</small>}</details> })}</div>}{actions.map((part, index) => <AgentAction key={`${part.type}-${index}`} part={part} ticketId={ticketId} onTicketAction={onTicketAction} />)}</div></div>
}

function AgentAction({ part, ticketId, onTicketAction }: { part: MessagePart; ticketId: string | null; onTicketAction: (action: Record<string, unknown>) => void }) {
  const action = part.data?.action
  if (action === 'workspace_change_approval') return <div className="agent-action workspace-change-action"><span className="action-mark">✦</span><div><strong>{part.text ?? 'Approve a local workspace change'}</strong><p>Codex can apply this request inside the local workspace after your explicit approval. Files, credentials, and external services remain outside the action.</p></div><button className="button button-dark" onClick={() => onTicketAction(part.data ?? {})}>Approve and apply</button></div>
  if (action === 'create_ticket') return <div className="agent-action"><span className="action-mark">→</span><div><strong>{part.text ?? 'Create a draft complaint ticket'}</strong><p>{ticketId ? `Linked to ${ticketId}. Review or continue in the ticket drawer.` : 'Review the description and authority before anything is submitted.'}</p>{typeof part.data?.authority_name === 'string' && <small className="action-authority">Route suggestion · {part.data.authority_name}</small>}</div>{ticketId ? <button className="button button-outline" disabled>Ticket linked</button> : <button className="button button-dark" onClick={() => onTicketAction(part.data ?? {})}>Create ticket</button>}</div>
  if (action === 'publish_preview') return <div className="agent-action"><span className="action-mark">□</span><div><strong>Public posting requires a redaction preview</strong><p>Choose the audience only after private details have been removed.</p></div></div>
  if (action === 'submission_blocked') return <div className="agent-action"><span className="action-mark">‖</span><div><strong>{part.text ?? 'Government submission is waiting for a verified connector'}</strong><p>Prepare and approve the local payload first. Live portals, login, OTP, CAPTCHA, and attestations stay user-controlled until credentials are configured.</p></div><button className="button button-outline" disabled>Local only</button></div>
  if (action === 'document_comparison') {
    const comparison = part.data?.comparison as { summary?: string; uncertainties?: string[]; changes?: Array<{ page?: number; change_type?: string; before?: string; after?: string }> } | undefined
    return <div className="agent-action comparison-card"><span className="action-mark">≈</span><div><strong>{part.text ?? 'What changed?'}</strong><p>{comparison?.summary ?? 'Source-linked document comparison'}</p>{comparison?.uncertainties?.map((item) => <small className="action-authority" key={item}>{item}</small>)}{comparison?.changes?.slice(0, 5).map((change, index) => <details key={`${change.page}-${index}`}><summary>Page {change.page} · {change.change_type}</summary><p><b>Earlier:</b> {change.before}</p><p><b>Current:</b> {change.after}</p></details>)}</div></div>
  }
  return <div className="agent-action"><span className="action-mark">?</span><div><strong>{part.text ?? 'More information needed'}</strong></div></div>
}

function TicketDrawer({ ownerId, action, ticket, threadId, onClose, onTicket, onThreadsChanged }: { ownerId: string; action: Record<string, unknown> | null; ticket: TicketDetail | null; threadId: string | null; onClose: () => void; onTicket: (ticket: TicketDetail) => void; onThreadsChanged: () => Promise<AgentThread[]> }) {
  const [title, setTitle] = useState(String(action?.title ?? ticket?.ticket.title ?? ''))
  const [description, setDescription] = useState(String(action?.description ?? ticket?.ticket.description ?? ''))
  const [locality, setLocality] = useState(String(action?.locality ?? ticket?.ticket.locality ?? ''))
  const [isWorking, setIsWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showPublish, setShowPublish] = useState(false)
  const [publicTitle, setPublicTitle] = useState('')
  const [publicBody, setPublicBody] = useState('')
  const [publicVisibility, setPublicVisibility] = useState<'nearby' | 'locality' | 'citywide'>('locality')
  const [isExporting, setIsExporting] = useState(false)
  const [preparation, setPreparation] = useState<TicketPreparation | null>(null)
  const [preparationFields, setPreparationFields] = useState<Record<string, string>>({})
  const [isPreparing, setIsPreparing] = useState(false)
  const [run, setRun] = useState<AgentRun | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const drawerRef = useRef<HTMLElement | null>(null)
  const closeRef = useRef<HTMLButtonElement | null>(null)
  const previousFocus = useRef<HTMLElement | null>(null)
  const redactionRisks = detectRedactionRisks(publicTitle, publicBody)
  const authorityId = typeof action?.authority_id === 'string' ? action.authority_id : ticket?.ticket.authority_id
  const authorityLabel = typeof action?.authority_name === 'string' ? action.authority_name : (authorityId ? AUTHORITY_NAMES[authorityId] ?? authorityId : 'Authority to confirm')
  useEffect(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    drawerRef.current?.scrollTo({ top: 0 })
    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key === 'Tab' && drawerRef.current) {
        const focusable = Array.from(drawerRef.current.querySelectorAll<HTMLElement>('button, input, textarea, select, a[href]')).filter((item) => !item.hasAttribute('disabled'))
        if (!focusable.length) return
        const first = focusable[0]; const last = focusable[focusable.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => { window.removeEventListener('keydown', onKeyDown); document.body.style.overflow = previousOverflow; previousFocus.current?.focus() }
  }, [onClose])
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setIsWorking(true); setError(null)
    try {
      const next = await createTicket({ threadId: threadId ?? undefined, title, description, locality, authorityId: typeof action?.authority_id === 'string' ? action.authority_id : null })
      onTicket(next)
      await onThreadsChanged()
    } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not create ticket') } finally { setIsWorking(false) }
  }
  const publish = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!ticket) return
    setIsWorking(true); setError(null)
    try {
      let displayName: string | undefined
      try { const value = readStoredProfilePreferences(ownerId); displayName = value.displayName?.trim() || undefined } catch { /* use account name */ }
      const post = await publishTicket(ticket.ticket.id, { title: publicTitle, body: publicBody, locality, visibility: publicVisibility, displayName })
      onTicket({ ...ticket, ticket: { ...ticket.ticket, public_post_id: post.id, visibility: publicVisibility } }); setShowPublish(false)
    } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not publish post') } finally { setIsWorking(false) }
  }
  const openPreview = () => { if (!ticket) return; setPublicTitle(redactPreview(ticket.ticket.title)); setPublicBody(redactPreview(ticket.ticket.description)); setShowPublish(true) }
  const prepare = async () => {
    if (!ticket || isPreparing) return
    setIsPreparing(true); setError(null)
    try {
      const prepared = await prepareTicket(ticket.ticket.id, preparationFields)
      setPreparation(prepared)
      setPreparationFields((current) => ({ ...prepared.fields, ...current }))
      try { setPreparation(await getTicketPreparation(ticket.ticket.id)) }
      catch { setError('The preparation was saved, but refreshing its status failed. You can continue reviewing this checkpoint.') }
    } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not prepare the authority review') }
    finally { setIsPreparing(false) }
  }
  const approve = async () => {
    if (!ticket || !preparation) return
    setIsPreparing(true); setError(null)
    try {
      const approval = await approveTicketPreparation(ticket.ticket.id, preparation.content_hash)
      setPreparation({ ...preparation, status: 'approved', approved_at: approval.approved_at, approval_expires_at: approval.expires_at, submission_enabled: approval.submission_enabled })
      setError(null)
    } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not save review approval') } finally { setIsPreparing(false) }
  }
  const startSubmission = async () => {
    if (!ticket || !preparation || preparation.status !== 'approved' || isSubmitting) return
    setIsSubmitting(true); setError(null)
    try { setRun(await submitTicket(ticket.ticket.id)) }
    catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not open the government form') }
    finally { setIsSubmitting(false) }
  }
  const continueSubmission = async (submit = false, sendOtp = false) => {
    if (!run || isSubmitting) return
    setIsSubmitting(true); setError(null)
    try { setRun(await resumeAgentRun(run.id, { submit, sendOtp, residentAttestation: submit })) }
    catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not continue the government form') }
    finally { setIsSubmitting(false) }
  }
  const cancelSubmission = async () => {
    if (!run || isSubmitting) return
    setIsSubmitting(true); setError(null)
    try { setRun(await cancelAgentRun(run.id)) }
    catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not cancel the government form') }
    finally { setIsSubmitting(false) }
  }
  const downloadBrief = async () => {
    if (!ticket || isExporting) return
    setIsExporting(true); setError(null)
    try {
      const contents = await exportTicket(ticket.ticket.id)
      const url = URL.createObjectURL(new Blob([contents], { type: 'text/plain;charset=utf-8' }))
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${ticket.ticket.civitas_ticket_id}-evidence-brief.txt`; anchor.click(); URL.revokeObjectURL(url)
    } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not export the evidence brief') } finally { setIsExporting(false) }
  }
  return <div className="drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><aside ref={drawerRef} className="ticket-drawer" role="dialog" aria-modal="true" aria-labelledby="ticket-drawer-title"><div className="drawer-header"><div><p className="eyebrow">{ticket ? 'CivitasX case workspace' : 'Private case draft'}</p><h2 id="ticket-drawer-title">{ticket ? ticket.ticket.civitas_ticket_id : 'New complaint'}</h2></div><button ref={closeRef} className="drawer-close" onClick={onClose} aria-label="Close ticket drawer">×</button></div>{!ticket ? <form className="ticket-form" onSubmit={(event) => void create(event)}><p className="drawer-lede">Save a private CivitasX case first. Nothing is sent to a government authority by creating this case.</p><label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={160} required /></label><label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={7} maxLength={5000} required /></label><label>Locality or landmark<input value={locality} onChange={(event) => setLocality(event.target.value)} maxLength={160} placeholder="e.g. Indiranagar 12th Main" /></label>{error && <div className="inline-error" role="alert">{error}</div>}<div className="route-preview"><span className="eyebrow">Suggested authority · not yet submitted</span><strong>{authorityLabel}</strong>{typeof action?.contact_route === 'string' && <small>{action.contact_route}</small>}</div><button className="button button-dark button-full" disabled={isWorking}>{isWorking ? 'Saving…' : 'Save private Civitas case'} <span aria-hidden="true">↗</span></button></form> : <div className="ticket-created"><div className="ticket-id-block"><span className="eyebrow">CivitasX ticket ID · app tracking only</span><strong>{ticket.ticket.civitas_ticket_id}</strong><span className={`status-chip ${ticket.ticket.status === 'not_solved' ? 'attention' : ticket.ticket.status === 'resolved' ? 'resolved' : 'working'}`}><span className={`dot dot-${ticket.ticket.status === 'not_solved' ? 'attention' : ticket.ticket.status === 'resolved' ? 'resolved' : 'working'}`} /> {statusLabel(ticket.ticket.status)}</span></div><div className="ticket-summary"><span className="eyebrow">What will be tracked</span><h3>{ticket.ticket.title}</h3><p>{ticket.ticket.description}</p><span>{ticket.ticket.locality ?? 'Bengaluru'} · {authorityLabel}</span><small className="trust-note">This CivitasX ID is not a government reference until an official connector confirms submission.</small></div><div className="ticket-history"><span className="eyebrow">Status history</span>{ticket.history.map((event) => <div key={event.id}><span className={`dot dot-${event.status === 'not_solved' ? 'attention' : event.status === 'resolved' ? 'resolved' : 'working'}`} /><span>{statusLabel(event.status)}</span><small>{formatTime(event.created_at)}</small></div>)}</div><div className="preparation-card"><div><span className="eyebrow">Prepare official submission</span><strong>{preparation ? preparation.status.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()) : 'Not checked yet'}</strong></div>{preparation ? <><p>{preparation.missing_fields.length ? `Still needed: ${preparation.missing_fields.join(', ')}.` : 'The exact local payload is ready to review. This hash binds the approval to these fields.'}</p>{preparation.missing_fields.length > 0 && <div className="preparation-fields"><p className="field-help">Confirm the remaining government-form details before the review hash is created.</p>{preparation.missing_fields.map((field) => <label key={field}>{field.replaceAll('_', ' ')}<input value={preparationFields[field] ?? preparation.fields[field] ?? ''} onChange={(event) => setPreparationFields((current) => ({ ...current, [field]: event.target.value }))} type={field === 'mobile' ? 'tel' : 'text'} required /></label>)}<button className="button button-outline button-full" onClick={() => void prepare()} disabled={isPreparing}>{isPreparing ? 'Checking details…' : 'Check these details'}</button></div>}<code>{preparation.content_hash.slice(0, 18)}…</code><button className="button button-dark button-full" onClick={() => void approve()} disabled={isPreparing || preparation.status === 'approved' || preparation.missing_fields.length > 0}>{preparation.status === 'approved' ? 'Review checkpoint saved' : 'Approve this preparation'}</button></> : <><p>Check the verified authority connector for required fields and save a resumable review checkpoint.</p><button className="button button-outline button-full" onClick={() => void prepare()} disabled={isPreparing}>{isPreparing ? 'Checking requirements…' : 'Prepare official submission'}</button></>}</div>{error && <div className="inline-error" role="alert">{error}</div>}{preparation?.status === 'approved' && <div className="inline-success" role="status">{preparation.submission_enabled ? 'Review checkpoint saved. Start the official connector when you are ready to submit.' : 'Review checkpoint saved. This connector is preparation-only in the current environment.'}</div>}{preparation?.status === 'approved' && preparation.submission_enabled && (!run || run.status === 'failed' || run.status === 'cancelled') && <button className="button button-dark button-full" onClick={() => void startSubmission()} disabled={isSubmitting}>{isSubmitting ? 'Starting official submission…' : 'Start official submission'}</button>}{run && <div className="preparation-card run-card"><div><span className="eyebrow">Government portal run</span><strong>{run.status.replaceAll('_', ' ')}</strong></div><p>{run.message}</p>{run.external_reference_id && <code>Government reference · {run.external_reference_id}</code>}{run.status === 'waiting_for_user' || run.status === 'ready_for_review' ? <div className="run-actions"><button className="button button-outline" onClick={() => void continueSubmission()} disabled={isSubmitting}>Refresh portal step</button><button className="button button-outline" onClick={() => void continueSubmission(false, true)} disabled={isSubmitting}>Request OTP</button><button className="button button-dark" onClick={() => void continueSubmission(true)} disabled={isSubmitting}>Confirm final submission</button><button className="button button-outline" onClick={() => void cancelSubmission()} disabled={isSubmitting}>Close portal run</button></div> : null}</div>}<button className="button button-outline button-full" onClick={() => void downloadBrief()} disabled={isExporting}>{isExporting ? 'Preparing brief…' : 'Download evidence brief'} <span aria-hidden="true">↓</span></button><button className="button button-outline button-full" onClick={openPreview}>Share this unsubmitted report</button><button className="button button-dark button-full" onClick={onClose}>Keep this case private</button>{showPublish && <form className="public-preview" onSubmit={(event) => void publish(event)}><div className="preview-label"><span className="eyebrow">What neighbours will see</span><span>Unsubmitted public snapshot</span></div><p>Publishing shares this redacted text to the selected audience. It does not submit anything to a government authority. Private attachments stay out of the post.</p><div className="redaction-checklist"><strong>Review privacy risks before sharing</strong>{redactionRisks.map((risk) => <label key={risk}><input type="checkbox" required /> <span>{risk}</span></label>)}</div><label>Public title<input value={publicTitle} onChange={(event) => setPublicTitle(event.target.value)} maxLength={160} required /></label><label>Public body<textarea value={publicBody} onChange={(event) => setPublicBody(event.target.value)} rows={5} maxLength={5000} required /></label><label>Audience<select value={publicVisibility} onChange={(event) => setPublicVisibility(event.target.value as 'nearby' | 'locality' | 'citywide')}><option value="nearby">Nearby</option><option value="locality">Locality</option><option value="citywide">Citywide</option></select></label><button className="button button-dark button-full" disabled={isWorking}>{isWorking ? 'Sharing…' : 'Approve and share unsubmitted report'} <span aria-hidden="true">↗</span></button></form>}</div>}</aside></div>
}

function PasswordVisibilityIcon({ visible }: { visible: boolean }) {
  return <svg className="password-toggle-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M2.062 12.348a1 1 0 0 1 0-.696C3.5 7.59 7.42 4.5 12 4.5s8.5 3.09 9.938 7.152a1 1 0 0 1 0 .696C20.5 16.41 16.58 19.5 12 19.5s-8.5-3.09-9.938-7.152Z" /><path d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />{!visible && <path d="m3 3 18 18" />}</svg>
}

function BrandMark() { return <img className="brand-mark" src="/civitasx-logo.png" alt="" aria-hidden="true" /> }

function SearchIcon() {
  return <svg className="global-search-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></svg>
}
