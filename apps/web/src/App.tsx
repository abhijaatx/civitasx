import { FormEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
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
  getFeedComments,
  getFeedPostEvidence,
  getAuthorities,
  getSubjectFollows,
  getMe,
  getNotifications,
  getLiveConnectors,
  getThread,
  getThreadAttachments,
  getTicket,
  getThreads,
  getToken,
  login,
  exchangeCognitoCode,
  logout,
  publishTicket,
  reportFeedComment,
  reportFeedPost,
  followFeedPost,
  register,
  recoverPassword,
  sendAgentMessageStream,
  applyWorkspaceChange,
  markAllNotificationsRead,
  markNotification,
  updateThread,
  shareFeedPost,
  saveFeedPost,
  muteFeedPost,
  prepareTicket,
  approveTicketPreparation,
  getTicketPreparation,
  updateTicketStatus,
  uploadAgentAttachment,
  voteFeedPost,
} from './api'
import type {
  AgentMessage,
  AgentThread,
  AgentThreadDetail,
  AppConfig,
  Attachment,
  CivicComment,
  CivicPost,
  CivicNotification,
  MessagePart,
  SourceEvidence,
  TicketDetail,
  TicketPreparation,
  TicketStatus,
  User,
  AuthorityRecord,
  LiveEndpointProfile,
} from './types'

type Surface = 'feed' | 'agent'

function surfaceFromLocation(): Surface {
  return window.location.pathname.startsWith('/agent') ? 'agent' : 'feed'
}

function threadFromLocation(): string | null {
  return new URLSearchParams(window.location.hash.replace(/^#/, '')).get('thread')
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

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { hour: 'numeric', minute: '2-digit' }).format(new Date(value))
}

function initials(name: string): string {
  return name.split(/\s+/).map((part) => part[0]).join('').slice(0, 2).toUpperCase()
}

function statusLabel(status: TicketStatus): string {
  return status.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
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
    window.history.replaceState({}, '', '/')
    setAuthNotice(signOutWarning ? `${signOutWarning} You are signed out on this device.` : null)
  }

  const navigateSurface = (nextSurface: Surface) => {
    setSurface(nextSurface)
    const nextPath = nextSurface === 'agent' ? '/agent' : '/feed'
    const currentHash = window.location.hash
    const nextHash = nextSurface === 'agent'
      ? (currentHash.startsWith('#thread=') ? currentHash : '')
      : (currentHash.startsWith('#post=') ? currentHash : '')
    if (window.location.pathname !== nextPath || currentHash !== nextHash) window.history.pushState({}, '', `${nextPath}${window.location.search}${nextHash}`)
  }

  useEffect(() => {
    const handleNavigation = () => {
      setSurface(surfaceFromLocation())
      setActiveThreadId(threadFromLocation())
    }
    window.addEventListener('popstate', handleNavigation)
    return () => window.removeEventListener('popstate', handleNavigation)
  }, [])

  useEffect(() => {
    if (user && window.location.pathname === '/' && !window.location.hash) {
      window.history.replaceState({}, '', `/feed${window.location.search}`)
    }
  }, [user])

  useEffect(() => {
    if (!user) {
      document.title = window.location.pathname === '/explore' ? 'CivitasX — Explore a sample issue' : 'CivitasX — Your civic record'
      return
    }
    document.title = surface === 'agent' ? 'CivitasX — Private Agent' : 'CivitasX — Public civic feed'
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
    <div className="app-shell">
      <ScoreRail surface={surface} onSurface={navigateSurface} capabilities={config.capabilities} />
      <main className="app-main">
        <TopBar user={user} surface={surface} onSurface={navigateSurface} onSignOut={() => void handleSignOut()} />
        {surface === 'feed' ? capabilities.feed ? (
          <FeedView user={user} capabilities={capabilities} onOpenAgent={(prompt) => { navigateSurface('agent'); setAgentPrompt(prompt ?? null); setActiveThreadId(null) }} />
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
  const [isWorking, setIsWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const cognitoReady = config?.auth.mode === 'cognito' && Boolean(config.auth.cognito_domain && config.auth.client_id)
  const cognitoRedirectUri = `${window.location.origin}${window.location.pathname}`
  const cognitoAuthorizeUrl = cognitoReady
    ? `${config.auth.cognito_domain!.replace(/\/$/, '')}/oauth2/authorize?response_type=code&client_id=${encodeURIComponent(config.auth.client_id!)}&redirect_uri=${encodeURIComponent(cognitoRedirectUri)}&scope=openid+email+profile`
    : null
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

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setIsWorking(true)
    setError(null)
    try {
      const nextUser = mode === 'login' ? await login(email, password) : await register(name, email, password)
      await onAuthenticated(nextUser)
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
          {config?.auth.mode === 'cognito' ? (
            <div className="auth-form">
              {error && <div className="form-error" role="alert">{error}</div>}
              {cognitoAuthorizeUrl ? <a className="button button-lime button-full" href={cognitoAuthorizeUrl}>{isWorking ? 'Opening…' : 'Continue with secure sign-in'}</a> : <div className="form-error" role="alert">Cognito is selected, but the identity provider is not configured yet. Ask an administrator to set the Cognito domain and client ID.</div>}
            </div>
          ) : (
            <>
              <div className="auth-tabs" role="group" aria-label="Account access"><button type="button" className={mode === 'login' ? 'active' : ''} aria-pressed={mode === 'login'} onClick={() => { setMode('login'); setError(null); setRecoveryOpen(false); setRecoveryNotice(null) }}>Sign in</button><button type="button" className={mode === 'register' ? 'active' : ''} aria-pressed={mode === 'register'} onClick={() => { setMode('register'); setError(null); setRecoveryOpen(false); setRecoveryNotice(null) }}>Create account</button></div>
              <div className={`auth-panel auth-panel-${recoveryOpen ? 'recovery' : mode}`} aria-label={recoveryOpen ? 'Password recovery form' : mode === 'login' ? 'Sign in form' : 'Create account form'}>
                <div key={`${mode}-${recoveryOpen ? 'recovery' : 'form'}`} className="auth-panel-content">
                  {recoveryOpen && mode === 'login' && recoveryEnabled ? <LocalRecoveryForm initialEmail={email} onCancel={() => setRecoveryOpen(false)} onAuthenticated={onAuthenticated} /> : <>
                  <form onSubmit={(event) => void submit(event)} className="auth-form">
                    {mode === 'register' && <label htmlFor="auth-name">Name<input id="auth-name" name="name" value={name} onChange={(event) => setName(event.target.value)} autoComplete="name" required maxLength={120} placeholder="Your name" /></label>}
                    <label htmlFor="auth-email">Email<input id="auth-email" name="email" value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" required maxLength={254} placeholder="you@example.com" /></label>
                    <label htmlFor="auth-password">Password<div className="password-field"><input id="auth-password" name="password" value={password} onChange={(event) => setPassword(event.target.value)} type={showPassword ? 'text' : 'password'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required minLength={mode === 'login' ? 1 : 12} maxLength={256} aria-describedby={mode === 'register' ? 'password-help' : undefined} placeholder={mode === 'login' ? 'Your password' : '12 characters or more'} /><button type="button" className="password-toggle" aria-label={showPassword ? 'Hide password' : 'Show password'} title={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword} onClick={() => setShowPassword((current) => !current)}><PasswordVisibilityIcon visible={showPassword} /></button></div>{mode === 'register' && <small id="password-help">Use at least 12 characters. Your password stays with this local pilot account.</small>}</label>
                    {error && <div className="form-error" role="alert">{error}</div>}
                      <button className="button button-lime button-full auth-submit" disabled={isWorking}>{isWorking ? 'Opening…' : mode === 'login' ? 'Sign in' : 'Create account'}</button>
                  </form>
                  {recoveryNotice && <div className="auth-help" role="status">{recoveryNotice}</div>}
                  </>}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </main>
  )
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
  return <aside className="score-rail" aria-label="CivitasX navigation"><button className="rail-brand" onClick={() => capabilities.feed && onSurface('feed')} aria-label="Open Civic Feed" disabled={!capabilities.feed}><BrandMark /></button><div className="rail-spine" aria-hidden="true"><span className={`rail-node ${surface === 'feed' ? 'active' : ''}`} /><span className="rail-line" /><span className={`rail-node ${surface === 'agent' ? 'active' : ''}`} /></div><nav className="rail-nav" aria-label="Primary">{capabilities.feed && <button aria-current={surface === 'feed' ? 'page' : undefined} className={surface === 'feed' ? 'active' : ''} onClick={() => onSurface('feed')}>FEED</button>}{capabilities.agent && <button aria-current={surface === 'agent' ? 'page' : undefined} className={surface === 'agent' ? 'active' : ''} onClick={() => onSurface('agent')}>AGENT</button>}</nav><div className="rail-bottom"><span className="rail-caption">BENGALURU<br />CIVIC NETWORK</span><span className="rail-version">V0.3</span></div></aside>
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
      if (item.post_id) { onSurface('feed'); window.history.pushState({}, '', `#post=${encodeURIComponent(item.post_id)}`); window.dispatchEvent(new PopStateEvent('popstate')); setShowInbox(false) }
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
  return <header ref={topbarRef} className="topbar"><div className="topbar-context"><span className="context-kicker">CIVITASX</span><span className="context-slash">/</span><span>{surface === 'feed' ? 'Public civic feed' : 'Private agent workspace'}</span></div><div className="topbar-actions"><div className={`connection-state ${online ? 'online' : 'offline'}`} aria-live="polite"><span className="dot" />{online ? 'On this device' : 'Offline · drafts stay safe'}</div><div className="inbox-menu"><button ref={inboxButtonRef} className="inbox-button" onClick={() => void openInbox()} aria-expanded={showInbox} aria-haspopup="dialog" aria-controls="activity-popover" aria-label={`Activity${unreadCount ? `, ${unreadCount} unread` : ''}`}>◎{unreadCount > 0 && <span className="inbox-count">{unreadCount}</span>}</button>{showInbox && <div id="activity-popover" ref={inboxDialogRef} className="notification-popover" role="dialog" aria-modal="true" aria-label="Activity"><div className="notification-heading"><span className="eyebrow">Activity</span><span>{notifications.length ? `${notifications.length} updates` : 'Quiet for now'}</span></div>{notifications.length > 0 && <button className="mark-read-button" onClick={() => void markAllRead()}>Mark all read</button>}{inboxError ? <p className="muted" role="alert">{inboxError}</p> : notifications.length === 0 ? <p className="muted">Follow a civic post to receive useful updates here.</p> : <div className="notification-list">{notifications.slice(0, 8).map((item) => <button className={`notification-item ${item.read ? '' : 'unread'}`} key={item.id} onClick={() => void markRead(item)}><span className="dot dot-lime" /><span><span className="notification-message">{item.message}</span><small>{formatTime(item.created_at)}{item.post_id ? ' · Open post' : ''}</small></span></button>)}</div>}</div>}</div><div className="account-menu"><button ref={accountButtonRef} className="account-button" onClick={() => setShowMenu((current) => !current)} aria-expanded={showMenu} aria-haspopup="dialog" aria-controls="account-popover"><span className="avatar">{initials(user.name)}</span><span className="account-name">{user.name}</span></button>{showMenu && <div id="account-popover" ref={accountDialogRef} className="account-popover" role="dialog" aria-modal="true" aria-label="Account settings"><div className="popover-email">{user.email}</div><PrivacyPreferences userId={user.id} onActivityNotificationsChange={setActivityNotifications} /><button onClick={onSignOut}>Sign out</button></div>}</div></div></header>
}

function PrivacyPreferences({ userId, onActivityNotificationsChange }: { userId: string; onActivityNotificationsChange?: (enabled: boolean) => void }) {
  const key = `civitas.profile.preferences.${userId}`
  const [open, setOpen] = useState(false)
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
      setOpen(false)
    } catch { setStorageError('These choices could not be saved in this browser. They will remain active for this session.') }
  }
  const clear = () => {
    try { window.localStorage.removeItem(key); window.sessionStorage.removeItem(key) } catch { /* best effort */ }
    setLocality(''); setDisplayName(''); setRemember(true); setActivityNotifications(true)
    onActivityNotificationsChange?.(true)
  }
  if (!open) return <button onClick={() => setOpen(true)}>Privacy & memory</button>
  return <div className="privacy-preferences" role="group" aria-label="Privacy and memory preferences"><p className="eyebrow">Your local memory</p><p className="muted">Saved here on this device. Turn off remembering to keep these choices only for this session.</p>{storageError && <p className="inline-error" role="alert">{storageError}</p>}<label>Default locality<select value={locality} onChange={(event) => setLocality(event.target.value)}><option value="">Ask each time</option>{LOCALITIES.filter((item) => item !== 'All Bengaluru').map((item) => <option key={item}>{item}</option>)}</select></label><label>Public display name<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Use a pseudonym" /></label><label className="checkbox-label"><input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} /> Remember these choices</label><label className="checkbox-label"><input type="checkbox" checked={activityNotifications} onChange={(event) => setActivityNotifications(event.target.checked)} /> Activity notifications</label><div className="privacy-actions"><button onClick={clear}>Clear saved choices</button><button onClick={() => setOpen(false)}>Cancel</button><button className="button-dark" onClick={save}>Save</button></div></div>
}

function FeedView({ user, capabilities, onOpenAgent }: { user: User; capabilities: AppConfig['capabilities']; onOpenAgent: (prompt?: string) => void }) {
  const [sort, setSort] = useState<'recent' | 'popular' | 'nearby' | 'following' | 'recommended'>(() => readFeedPreferences(user.id).sort ?? 'recommended')
  const [locality, setLocality] = useState(() => readFeedPreferences(user.id).locality || readProfileLocality(user.id) || '')
  const [statusFilter, setStatusFilter] = useState<TicketStatus | ''>('')
  const [authorityFilter, setAuthorityFilter] = useState('')
  const [topic, setTopic] = useState('')
  const [topicInput, setTopicInput] = useState('')
  const [localityFollowing, setLocalityFollowing] = useState(false)
  const [subjectFollowing, setSubjectFollowing] = useState(false)
  const [posts, setPosts] = useState<CivicPost[]>([])
  const [authorities, setAuthorities] = useState<AuthorityRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedPost, setSelectedPost] = useState<CivicPost | null>(null)
  const [showPrivacyHint, setShowPrivacyHint] = useState(() => {
    return !Boolean(readStoredProfilePreferences(user.id).onboardingSeen)
  })
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
    const openFromHash = () => {
      const id = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('post')
      setSelectedPost(id ? posts.find((post) => post.id === id) ?? null : null)
    }
    openFromHash()
    window.addEventListener('popstate', openFromHash)
    return () => window.removeEventListener('popstate', openFromHash)
  }, [posts])
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
  const updatePost = (postId: string, patch: Partial<CivicPost>) => setPosts((current) => current.map((post) => post.id === postId ? { ...post, ...patch } : post))
  const openPost = (post: CivicPost) => { setSelectedPost(post); window.history.pushState({}, '', `#post=${encodeURIComponent(post.id)}`) }
  const closePost = () => { setSelectedPost(null); window.history.replaceState({}, '', window.location.pathname + window.location.search) }
  const nearbyExplanation = locality ? `Nearby uses your selected locality (${locality}) and citywide posts.` : 'Nearby uses the locality you select; no precise location is collected.'
  return <div className="feed-view"><div className="demo-banner" role="note"><strong>Sample civic records</strong><span>Seeded for this pilot · not government records.</span></div>{showPrivacyHint && <div className="privacy-onboarding" role="status"><span><strong>Make this workspace yours.</strong> Set your locality, display name, and activity preferences in Account.</span><button onClick={() => { setShowPrivacyHint(false); const current = readStoredProfilePreferences(user.id); try { if (current.remember === false) window.sessionStorage.setItem(`civitas.profile.preferences.${user.id}`, JSON.stringify({ ...current, onboardingSeen: true })); else window.localStorage.setItem(`civitas.profile.preferences.${user.id}`, JSON.stringify({ ...current, onboardingSeen: true })) } catch { /* best effort */ } }}>Got it</button></div>}<section className="feed-hero"><div><p className="eyebrow">Bengaluru / public civic feed</p><h1>What needs<br /><em>attention?</em></h1><p className="feed-lede">Public issues, evidence, and community context. Official submission is always reviewed separately.</p></div><div className="feed-index"><span>OPEN SIGNALS</span><strong>{String(posts.length).padStart(2, '0')}</strong><span>IN THIS VIEW</span></div></section><div className="feed-toolbar"><div className="feed-tabs" aria-label="Feed sort"><button type="button" aria-pressed={sort === 'recommended'} className={sort === 'recommended' ? 'active' : ''} onClick={() => setSort('recommended')}>For you</button><button type="button" aria-pressed={sort === 'recent'} className={sort === 'recent' ? 'active' : ''} onClick={() => setSort('recent')}>Recent</button><button type="button" aria-pressed={sort === 'nearby'} className={sort === 'nearby' ? 'active' : ''} disabled={!locality} onClick={() => setSort('nearby')} aria-label={locality ? 'Nearby' : 'Nearby, select a locality first'}>Nearby</button><button type="button" aria-pressed={sort === 'following'} className={sort === 'following' ? 'active' : ''} onClick={() => setSort('following')}>Following</button><button type="button" aria-pressed={sort === 'popular'} className={sort === 'popular' ? 'active' : ''} onClick={() => setSort('popular')}>Popular</button></div><div className="feed-filters"><label className="sr-only" htmlFor="locality-filter">Locality</label><select id="locality-filter" value={locality || 'All Bengaluru'} onChange={(event) => { setLocality(event.target.value === 'All Bengaluru' ? '' : event.target.value); setLocalityFollowing(false) }}>{LOCALITIES.map((item) => <option key={item}>{item}</option>)}</select><button className="filter-follow" disabled={!locality} onClick={() => void toggleLocalityFollow()}>{localityFollowing ? 'Following locality' : 'Follow locality'}</button><label className="sr-only" htmlFor="status-filter">Status</label><select id="status-filter" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as TicketStatus | '')}><option value="">All statuses</option>{TICKET_STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select><label className="sr-only" htmlFor="authority-filter">Authority</label><select id="authority-filter" value={authorityFilter} onChange={(event) => { setAuthorityFilter(event.target.value); setSubjectFollowing(false) }}><option value="">All authorities</option>{authorities.map((authority) => <option key={authority.authority_id} value={authority.authority_id}>{authority.short_name || authority.name}</option>)}</select><label className="sr-only" htmlFor="topic-filter">Search Feed</label><input id="topic-filter" value={topicInput} onChange={(event) => { setTopicInput(event.target.value); setSubjectFollowing(false) }} placeholder="Search issues" />{subjectLabel && <button className="filter-follow" onClick={() => void followSubjectFilter()}>{subjectFollowing ? `Following ${subjectLabel}` : `Follow ${subjectLabel}`}</button>}</div></div><p className="feed-sort-help">{sort === 'recommended' ? 'For you: locality, follows, fresh discussion, evidence, and unresolved issues.' : sort === 'nearby' ? nearbyExplanation : sort === 'popular' ? 'Popular: votes and discussion, with official status preserved.' : sort === 'following' ? 'Following: people, authorities, and subjects you follow.' : 'Recent: chronological.'}</p><div className="feed-create"><div><span className="feed-create-mark" aria-hidden="true">+</span><strong>Have an issue to report?</strong><span>Describe what happened. Attach photos or documents.</span></div><button className="button button-dark" disabled={!capabilities.agent} onClick={() => onOpenAgent()}>{capabilities.agent ? 'Open private Agent' : 'Agent unavailable'} <span aria-hidden="true">↗</span></button></div>{error && <div className="inline-error" role="alert">{error}</div>}{loading ? <div className="feed-loading"><div className="loading-mark small" aria-hidden="true"><span /><span /><span /></div><span>Loading civic signals…</span></div> : posts.length === 0 ? <div className="empty-cases"><div className="empty-score"><span /><span /><span /></div><div><h3>No public signals match this view.</h3><p>Open Agent to create the first reviewed ticket.</p></div></div> : <><div className="feed-list">{posts.map((post) => <FeedPost key={post.id} post={post} agentEnabled={capabilities.agent} onUpdate={(patch) => updatePost(post.id, patch)} onOpenDetail={() => openPost(post)} onOpenAgent={() => onOpenAgent(`Ticket ${post.civitas_ticket_id}: ${post.title}\n\n${post.body}`)} />)}</div>{nextCursor && <button className="button button-outline feed-load-more" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? 'Loading more…' : 'Load more civic signals'}</button>}</>}<p className="feed-footnote">Ticket IDs track CivitasX cases; they become government references only after connector-confirmed submission.</p>{selectedPost && <PostDetailDialog post={selectedPost} locality={locality || undefined} researchEnabled={capabilities.research} onClose={closePost} onOpenAgent={onOpenAgent} />}</div>
}

function PostDetailDialog({ post, locality, researchEnabled, onClose, onOpenAgent }: { post: CivicPost; locality?: string; researchEnabled: boolean; onClose: () => void; onOpenAgent: (prompt?: string) => void }) {
  const [evidence, setEvidence] = useState<SourceEvidence[]>([])
  const [loading, setLoading] = useState(true)
  const [evidenceError, setEvidenceError] = useState<string | null>(null)
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'unavailable'>('idle')
  const dialogRef = useRef<HTMLElement | null>(null)
  const closeRef = useRef<HTMLButtonElement | null>(null)
  const previousFocus = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (!researchEnabled) { setEvidence([]); setEvidenceError(null); setLoading(false); return }
    let alive = true
    setEvidenceError(null)
    setLoading(true)
    void getFeedPostEvidence(post.id, locality || post.locality || undefined).then((items) => { if (alive) setEvidence(items) }).catch((error) => { if (alive) { setEvidence([]); setEvidenceError(error instanceof Error ? error.message : 'Evidence is temporarily unavailable') } }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [locality, post.id, post.locality, researchEnabled])
  useLayoutEffect(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>('button, input, textarea, select, a[href], summary')).filter((item) => !item.hasAttribute('disabled'))
      if (!focusable.length) return
      const first = focusable[0]; const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', close)
    return () => { window.removeEventListener('keydown', close); document.body.style.overflow = previousOverflow; previousFocus.current?.focus() }
  }, [onClose])
  const copyLink = async () => {
    try { await copyText(window.location.href); setCopyState('copied') }
    catch { setCopyState('unavailable') }
  }
  return <div className="detail-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><article ref={dialogRef} className="post-detail" role="dialog" aria-modal="true" aria-labelledby={`post-detail-${post.id}`} tabIndex={-1}><button ref={closeRef} className="drawer-close" onClick={onClose} aria-label="Close post detail">×</button><div className="post-detail-top"><span className="eyebrow">{post.is_demo ? 'Sample civic signal' : 'Public civic signal'}</span><span className="post-ticket">{post.civitas_ticket_id}</span></div><h2 id={`post-detail-${post.id}`}>{post.title}</h2><p>{post.body}</p><div className="post-meta"><span>{post.author_name}</span><span>{post.locality ?? 'Bengaluru'}</span>{post.authority_name && <span>{post.authority_name}</span>}<span>{statusLabel(post.status)}</span></div><section className="detail-evidence"><div className="sources-heading"><span className="eyebrow">Evidence</span><span>{!researchEnabled ? 'Unavailable' : loading ? 'Checking…' : `${evidence.length} ${evidence.length === 1 ? 'source' : 'sources'}`}</span></div>{!researchEnabled ? <p className="muted">Evidence lookup is not enabled in this deployment.</p> : loading ? <p className="muted">Loading source metadata…</p> : evidenceError ? <p className="inline-error" role="alert">{evidenceError}. Try again later; no evidence claim was made.</p> : evidence.length === 0 ? <p className="muted">No source passages are attached to this public snapshot yet.</p> : evidence.map((source) => <details key={source.source_id}><summary><strong>{source.title}</strong><small>{source.authority} · page {source.page ?? '—'}</small></summary><p>{source.passage || 'No extractable passage was available.'}</p><div className="source-provenance"><span>Status: {source.status}</span><span>Retrieved: {source.retrieved_at ? formatDate(source.retrieved_at) : 'unknown'}</span><span>Language: {source.translation_language ?? 'English'}</span>{source.url && <a href={source.url} target="_blank" rel="noreferrer">Open official source ↗</a>}</div></details>)}</section><div className="detail-actions"><button className="button button-dark" onClick={() => onOpenAgent(`Ticket ${post.civitas_ticket_id}: ${post.title}\n\n${post.body}`)}>Ask Agent about this</button><button className="button button-outline" onClick={() => void copyLink()}>Copy link</button>{copyState !== 'idle' && <span className={copyState === 'copied' ? 'copy-feedback success' : 'copy-feedback error'} role={copyState === 'copied' ? 'status' : 'alert'}>{copyState === 'copied' ? 'Link copied.' : 'Copy is unavailable here.'}</span>}</div></article></div>
}

function FeedPost({ post, onUpdate, onOpenDetail, onOpenAgent, agentEnabled = true }: { post: CivicPost; onUpdate: (patch: Partial<CivicPost>) => void; onOpenDetail: () => void; onOpenAgent: () => void; agentEnabled?: boolean }) {
  const [commentsOpen, setCommentsOpen] = useState(false)
  const [comments, setComments] = useState<CivicComment[]>([])
  const [comment, setComment] = useState('')
  const [replyTo, setReplyTo] = useState<CivicComment | null>(null)
  const [busy, setBusy] = useState(false)
  const [followBusy, setFollowBusy] = useState(false)
  const [saved, setSaved] = useState(post.is_saved)
  const [muted, setMuted] = useState(post.is_muted)
  const [showReason, setShowReason] = useState(false)
  const [showMore, setShowMore] = useState(false)
  const [feedback, setFeedback] = useState<{ kind: 'success' | 'error'; message: string } | null>(null)
  const moreRef = useRef<HTMLSpanElement | null>(null)
  const statusTone = post.status === 'resolved' ? 'resolved' : post.status === 'not_solved' ? 'attention' : post.status === 'in_progress' ? 'working' : 'neutral'
  const rootComments = comments.filter((item) => !item.parent_id || !comments.some((parent) => parent.id === item.parent_id))
  useEffect(() => {
    if (!showMore) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setShowMore(false) }
    const outside = (event: PointerEvent) => { if (moreRef.current && !moreRef.current.contains(event.target as Node)) setShowMore(false) }
    window.addEventListener('keydown', close); document.addEventListener('pointerdown', outside)
    return () => { window.removeEventListener('keydown', close); document.removeEventListener('pointerdown', outside) }
  }, [showMore])
  const vote = async (value: -1 | 0 | 1) => {
    setBusy(true)
    const previous = { user_vote: post.user_vote, vote_score: post.vote_score, upvotes: post.upvotes, downvotes: post.downvotes }
    onUpdate({ user_vote: value, vote_score: previous.vote_score - previous.user_vote + value, upvotes: post.upvotes + (value === 1 ? 1 : 0) - (previous.user_vote === 1 ? 1 : 0), downvotes: post.downvotes + (value === -1 ? 1 : 0) - (previous.user_vote === -1 ? 1 : 0) })
    try { const result = await voteFeedPost(post.id, value); onUpdate({ user_vote: result.value, vote_score: result.vote_score, upvotes: result.upvotes, downvotes: result.downvotes }) } catch (error) { onUpdate(previous); setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not record vote' }) } finally { setBusy(false) }
  }
  const toggleComments = async () => {
    setCommentsOpen((current) => !current)
    if (!commentsOpen) {
      try { setComments(await getFeedComments(post.id)) } catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not load comments' }) }
    }
  }
  const submitComment = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!comment.trim() || post.is_locked) return
    try { const next = await addFeedComment(post.id, comment, replyTo?.id); setComments((current) => [...current, next]); setComment(''); setReplyTo(null); onUpdate({ comment_count: post.comment_count + 1 }) } catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not add comment' }) }
  }
  const toggleFollow = async () => {
    setFollowBusy(true)
    try { const result = await followFeedPost(post.id, !post.is_following); onUpdate({ is_following: result.following }); setFeedback({ kind: 'success', message: result.following ? 'Following this civic signal.' : 'Unfollowed.' }) } catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not update follow' }) } finally { setFollowBusy(false) }
  }
  const toggleSave = async () => {
    try { const result = await saveFeedPost(post.id, !saved); setSaved(result.saved); onUpdate({ is_saved: result.saved }); setFeedback({ kind: 'success', message: result.saved ? 'Saved to your local list.' : 'Removed from saved.' }) } catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not save post' }) }
  }
  const toggleMute = async () => {
    try { const result = await muteFeedPost(post.id, !muted); setMuted(result.muted); onUpdate({ is_muted: result.muted }); setFeedback({ kind: 'success', message: result.muted ? 'Muted from this feed.' : 'Unmuted.' }) } catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not update mute' }) }
  }
  const share = async () => {
    try { const result = await shareFeedPost(post.id); await copyText(`${window.location.origin}${result.url}`); setFeedback({ kind: 'success', message: 'Share link copied. It expires in 72 hours and contains only this redacted snapshot.' }) } catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not create or copy share link' }) }
  }
  const report = async () => {
    try { await reportFeedPost(post.id, 'other'); setFeedback({ kind: 'success', message: 'Report received. A moderator can review it from the local queue.' }) } catch (error) { setFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Could not report post' }) }
  }
  const askAgent = () => { if (agentEnabled) onOpenAgent(); else setFeedback({ kind: 'error', message: 'The private Agent is not enabled in this phase.' }) }
  return <article className={`feed-post ${post.is_demo ? 'demo-post' : ''}`}><div className="post-votes"><button aria-label={`Upvote ${post.title}`} className={post.user_vote === 1 ? 'selected' : ''} disabled={busy} onClick={() => void vote(post.user_vote === 1 ? 0 : 1)}>▲</button><strong>{post.vote_score}</strong><button aria-label={`Downvote ${post.title}`} className={post.user_vote === -1 ? 'selected down' : ''} disabled={busy} onClick={() => void vote(post.user_vote === -1 ? 0 : -1)}>▼</button></div><div className="post-body"><div className="post-topline"><span className={`status-chip ${statusTone}`}><span className={`dot dot-${statusTone}`} /> {statusLabel(post.status)}</span>{post.is_demo && <span className="sample-chip">SAMPLE · NOT OFFICIAL</span>}<button className="post-ticket post-ticket-link" onClick={onOpenDetail} aria-label={`Open ticket ${post.civitas_ticket_id}`}>{post.civitas_ticket_id}</button><span className="post-time">{formatDate(post.created_at)}</span></div><h2><button className="post-title-link" onClick={onOpenDetail} aria-label={post.title}>{post.title}</button></h2><p>{post.body}</p><div className="post-meta"><span>{post.author_name}</span><span>{post.locality ?? 'Bengaluru'}</span>{post.authority_name && <span>{post.authority_name}</span>}{post.evidence_count > 0 && <button className="evidence-link" onClick={onOpenDetail}>{post.evidence_count} evidence</button>}</div><div className="post-actions"><button onClick={toggleComments}>{post.comment_count} {post.comment_count === 1 ? 'Comment' : 'Comments'}</button><button onClick={askAgent} disabled={!agentEnabled}>Ask Agent</button><button onClick={toggleFollow} disabled={followBusy}>{post.is_following ? 'Following' : 'Follow'}</button><button className="more-toggle" onClick={() => setShowMore((current) => !current)} aria-expanded={showMore}>More {showMore ? '⌃' : '⌄'}</button>{showMore && <span ref={moreRef} className="more-actions"><button onClick={toggleSave}>{saved ? 'Saved' : 'Save'}</button><button onClick={toggleMute}>{muted ? 'Unmute' : 'Mute'}</button><button onClick={share}>Share</button><button onClick={report}>Report</button></span>}{post.is_owner && <TicketStatusControl post={post} onUpdate={onUpdate} onFeedback={(message, kind) => setFeedback({ message, kind })} />}</div>{post.ranking_reasons.length > 0 && <div className="ranking-explain"><button onClick={() => setShowReason((current) => !current)}>{showReason ? 'Hide why this is here' : 'Why this is here'}</button>{showReason && <span>{post.ranking_reasons.join(' · ')}</span>}</div>}{feedback && <div className={feedback.kind === 'success' ? 'inline-success' : 'inline-error'} role={feedback.kind === 'error' ? 'alert' : 'status'}>{feedback.message}</div>}{commentsOpen && <div className="comments"><div className="comment-list">{comments.length === 0 ? <p className="muted">No comments yet. Add useful local context.</p> : rootComments.map((item) => <CommentThread key={item.id} item={item} comments={comments} onReply={setReplyTo} onRefresh={() => { void getFeedComments(post.id).then(setComments) }} />)}</div>{replyTo && <div className="reply-context">Replying to {replyTo.author_name}<button onClick={() => setReplyTo(null)} aria-label="Cancel reply">×</button></div>}<form className="comment-form" onSubmit={(event) => void submitComment(event)}><label className="sr-only" htmlFor={`comment-${post.id}`}>Add a comment</label><input id={`comment-${post.id}`} value={comment} onChange={(event) => setComment(event.target.value)} placeholder={post.is_locked ? 'This discussion is locked' : replyTo ? 'Reply with useful context…' : 'Add useful context…'} disabled={post.is_locked} /><button className="button button-dark" disabled={!comment.trim() || post.is_locked}>{replyTo ? 'Reply' : 'Comment'}</button></form></div>}</div></article>
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
  return <div className="comment"><div className="comment-author">{item.author_name} <span>{formatTime(item.created_at)}</span></div>{editing ? <div className="comment-edit"><input value={body} onChange={(event) => setBody(event.target.value)} /><button onClick={() => void save()}>Save</button><button onClick={() => setEditing(false)}>Cancel</button></div> : <p>{item.body}</p>}<div className="comment-tools"><button className="comment-reply" onClick={() => onReply(item)}>Reply</button>{item.is_owner && <><button className="comment-reply" onClick={() => setEditing(true)}>Edit</button>{confirmDelete ? <><button className="comment-reply confirm-delete" onClick={() => void remove()}>Confirm delete</button><button className="comment-reply" onClick={() => setConfirmDelete(false)}>Cancel</button></> : <button className="comment-reply" onClick={() => setConfirmDelete(true)}>Delete</button>}</>}<button className="comment-reply" onClick={() => void report()}>Report</button></div>{error && <small className="muted" role="alert">{error}</small>}{children.map((child) => <CommentThread key={child.id} item={child} comments={comments} onReply={onReply} onRefresh={onRefresh} />)}</div>
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
  const [liveSources, setLiveSources] = useState<LiveEndpointProfile[]>([])
  const [threadQuery, setThreadQuery] = useState('')
  const [agentError, setAgentError] = useState<string | null>(null)
  const [creatingThread, setCreatingThread] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const pendingPromptRef = useRef<string | null>(null)
  const openThread = useCallback(async (threadId: string) => {
    setLoading(true)
    setAgentError(null)
    setDetail(null)
    try {
      setDetail(await getThread(threadId))
      onSelectThread(threadId)
      window.history.replaceState({}, '', `${window.location.pathname}${window.location.search}#thread=${encodeURIComponent(threadId)}`)
    } catch (nextError) { onSelectThread(null); setAgentError(nextError instanceof Error ? nextError.message : 'Could not open this conversation') } finally { setLoading(false) }
  }, [onSelectThread])
  useEffect(() => {
    if (initialPrompt || pendingPromptRef.current || loading || creatingThread) return
    if (activeThreadId) {
      if (detail?.thread.id !== activeThreadId) void openThread(activeThreadId)
      return
    }
    if (threads.length > 0) { void openThread(threads[0].id) }
  }, [activeThreadId, creatingThread, detail?.thread.id, initialPrompt, loading, openThread, threads])
  useEffect(() => {
    if (!capabilities.live_sources) { setLiveSources([]); return }
    void getLiveConnectors().then(setLiveSources).catch(() => setLiveSources([]))
  }, [capabilities.live_sources])
  const newThread = async (goal = 'Explore a Bengaluru civic issue'): Promise<boolean> => {
    if (creatingThread) return false
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
  const openTicket = async (ticketId: string) => {
    try { setTicket(await getTicket(ticketId)) } catch (nextError) { setAgentError(nextError instanceof Error ? nextError.message : 'Could not open this ticket') }
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
        <div className="thread-sidebar-note"><span className="dot dot-lime" /> Local evidence workspace.{capabilities.live_sources && <div className="live-source-mini"><span className="eyebrow">Government connectors</span><strong>{liveSources.filter((source) => source.status === 'ready').length} live · {liveSources.length} registered</strong><small>Public sources refresh read-only; consent and approval-gated services wait for official credentials.</small><details><summary>View catalog</summary><div className="connector-mini-list">{liveSources.map((source) => <div key={source.endpoint_id}><span>{source.priority}</span><strong>{source.title}</strong><small>{source.status.replaceAll('_', ' ')}</small></div>)}</div></details></div>}</div>
      </aside>
      <section className="chat-panel">
        <button type="button" className="sidebar-toggle-panel" aria-controls="conversation-list" aria-expanded={!sidebarCollapsed} aria-label={sidebarCollapsed ? 'Show older conversations' : 'Hide older conversations'} title={sidebarCollapsed ? 'Show older conversations' : 'Hide older conversations'} onClick={toggleSidebar}><span className="sidebar-toggle-icon" aria-hidden="true" /><span className="sidebar-toggle-label">{sidebarCollapsed ? 'Show chats' : 'Hide chats'}</span></button>
        <div className="mobile-thread-tools"><label className="sr-only" htmlFor="mobile-thread-select">Conversation</label><select id="mobile-thread-select" value={detail?.thread.id ?? ''} onChange={(event) => { if (event.target.value) void openThread(event.target.value) }}><option value="">Conversations</option>{threads.map((thread) => <option key={thread.id} value={thread.id}>{thread.title}</option>)}</select><button className="new-thread" aria-label="New conversation" disabled={creatingThread} onClick={() => void newThread()}>+</button></div>
        {loading ? <div className="chat-empty"><div className="loading-mark small" aria-hidden="true"><span /><span /><span /></div><p>Opening your conversation…</p></div> : detail ? <ChatThread capabilities={capabilities} ownerInitials={initials(ownerName)} detail={detail} ticketId={ticket?.ticket.civitas_ticket_id ?? detail.thread.ticket_id} prefill={prefill} onPrefillConsumed={() => setPrefill('')} onDetail={setDetail} onThreadsChanged={onThreadsChanged} onTicketAction={handleAgentAction} onOpenTicket={openTicket} onNewThread={() => void newThread()} /> : <ChatEmpty onStarter={chooseStarter} />}
      </section>
      {capabilities.tickets && (ticket || ticketAction) && <TicketDrawer ownerId={ownerId} action={ticketAction} ticket={ticket} threadId={detail?.thread.id ?? null} onClose={() => { setTicket(null); setTicketAction(null) }} onTicket={setTicket} onThreadsChanged={onThreadsChanged} />}
    </div>
  )
}

function ThreadRow({ thread, active, onOpen, onRefresh, onRenamed, onArchived }: { thread: AgentThread; active: boolean; onOpen: () => void; onRefresh: () => Promise<AgentThread[]>; onRenamed: (detail: AgentThreadDetail) => void; onArchived: () => void }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(thread.title)
  const [confirmArchive, setConfirmArchive] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
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
  return <div className={`thread-row ${active ? 'active' : ''}`}>{editing ? <form className="thread-rename-form" onSubmit={(event) => void saveRename(event)}><label className="sr-only" htmlFor={`rename-${thread.id}`}>Conversation title</label><input id={`rename-${thread.id}`} value={title} onChange={(event) => setTitle(event.target.value)} autoFocus /><button type="submit" disabled={working || !title.trim()}>Save</button><button type="button" onClick={() => { setTitle(thread.title); setEditing(false) }}>Cancel</button></form> : <button className="thread-select" onClick={onOpen}><span className="thread-dot" /><span><strong>{thread.title}</strong><small>{thread.message_count} messages {thread.ticket_id ? `· ${thread.ticket_id}` : ''}</small></span></button>}<span className="thread-row-actions">{!editing && <button aria-label={`Rename ${thread.title}`} onClick={() => { setTitle(thread.title); setEditing(true); setConfirmArchive(false) }}>Rename</button>}{confirmArchive ? <><button className="confirm-archive" disabled={working} onClick={() => void archive()}>Confirm archive</button><button onClick={() => setConfirmArchive(false)}>Cancel</button></> : <button aria-label={`Archive ${thread.title}`} onClick={() => setConfirmArchive(true)}>Archive</button>}</span>{error && <span className="thread-row-error" role="alert">{error}</span>}</div>
}

function ChatEmpty({ onStarter }: { onStarter: (prompt: string) => void }) {
  return <div className="chat-empty"><div className="chat-empty-mark"><BrandMark /></div><p className="eyebrow">Your private agent</p><h1>What are we<br /><em>working on?</em></h1><p>Ask about a civic record, describe an issue, or prepare a complaint. You approve anything public or external.</p><div className="starter-prompts">{starterPrompts.map((prompt) => <button key={prompt} onClick={() => onStarter(prompt)}>{prompt}<span aria-hidden="true">↗</span></button>)}</div></div>
}

type StagedAttachment = { id: string; file: File; previewUrl: string | null; uploadedId?: string }
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

function ChatThread({ capabilities, ownerInitials, detail, ticketId, prefill, onPrefillConsumed, onDetail, onThreadsChanged, onTicketAction, onOpenTicket, onNewThread }: { capabilities: AppConfig['capabilities']; ownerInitials: string; detail: AgentThreadDetail; ticketId: string | null; prefill: string; onPrefillConsumed: () => void; onDetail: (detail: AgentThreadDetail) => void; onThreadsChanged: () => Promise<AgentThread[]>; onTicketAction: (action: Record<string, unknown>) => void; onOpenTicket: (ticketId: string) => void; onNewThread: () => void }) {
  const [message, setMessage] = useState(prefill)
  const [attachments, setAttachments] = useState<StagedAttachment[]>([])
  const [savedAttachments, setSavedAttachments] = useState<Attachment[]>([])
  const [uploadedAttachments, setUploadedAttachments] = useState<Record<string, Attachment>>({})
  const [isSending, setIsSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [phase, setPhase] = useState<string | null>(null)
  const [liveTurn, setLiveTurn] = useState<LiveTurnItem[]>([])
  const [turnState, setTurnState] = useState<TurnState>('idle')
  const [lastFailedPrompt, setLastFailedPrompt] = useState<string | null>(null)
  const [draftRestored, setDraftRestored] = useState(false)
  const [attachmentsRestored, setAttachmentsRestored] = useState(false)
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
  const draftKey = `civitas.agent.draft.${detail.thread.id}`
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
    setLastFailedPrompt(null)
  }, [detail.thread.id])
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
  }, [detail.messages.length, liveTurn.length, phase, turnState])
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
    const clientMessageId = clientMessageIdRef.current ?? (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`)
    clientMessageIdRef.current = clientMessageId
    submittedContentRef.current = submittedContent
    const controller = new AbortController()
    requestRef.current = controller
    const uploadedThisAttempt: Attachment[] = []
    const stagedAttachments = [...attachments]
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
      )
      onDetail(next)
      if (hasProviderFailure(next)) {
        throw new Error('The configured providers did not complete this turn. Your request is still in the composer; retry when a provider is reachable.')
      }
      setMessage('')
      clientMessageIdRef.current = null
      submittedContentRef.current = null
      try { window.localStorage.removeItem(draftKey) } catch { /* ignore */ }
      setAttachments([])
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
      setPhase(stopped ? 'Response stopped · your draft is preserved' : 'Turn failed · retry when ready')
      setError(stopped ? 'Response stopped. Your draft is still here; edit it or retry this turn.' : nextError instanceof Error ? nextError.message : 'Could not send the message')
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
  return (
    <div className="chat-thread">
      <header className="chat-header">
      <div className="chat-header-copy"><p className="eyebrow">Private conversation</p><h1>{detail.thread.title}</h1><p className="chat-subtitle">{detail.messages.length} messages · Private conversation</p></div>
        <span className="chat-state"><span className="dot dot-lime" /> {linkedTicketId ? <button className="chat-ticket-link" onClick={() => onOpenTicket(linkedTicketId)}>Civitas case {linkedTicketId}</button> : 'Hermes-style local agent'}</span>
      </header>
      <div ref={messageListRef} className="message-list" onScroll={handleMessageScroll}>
        {detail.messages.length === 0 && <div className="assistant-message welcome"><span className="message-avatar">CX</span><div><strong>I’m ready when you are.</strong><p>Research a record, explain an official document, or prepare a complaint. You approve anything public or external.</p><div className="welcome-hints"><button onClick={() => setMessage('/research safer walking routes near my locality')}>Research a record</button><button onClick={() => setMessage('/complaint broken streetlight near Indiranagar')}>Prepare a complaint</button></div></div></div>}
        {detail.messages.map((item) => <MessageBubble key={item.id} message={item} userInitials={ownerInitials} ticketId={linkedTicketId} onReuse={setMessage} onTicketAction={onTicketAction} />)}
        {turnState !== 'idle' && <HermesLiveTurn state={turnState} phase={phase} items={liveTurn} onRetry={lastFailedPrompt ? retryLastTurn : undefined} />}
      </div>
      <div ref={composerRef} className="chat-composer-wrap">
        <div className="composer-privacy"><span className="dot dot-lime" /> Private thread · nothing is submitted or posted without your approval {phase && <strong className="agent-phase">{phase}</strong>}</div>
        {commandPaletteOpen && <div className="hermes-command-menu" role="group" aria-label="Agent commands">{commandOptions.map(([command, description]) => <button key={command} type="button" onClick={() => setMessage(`${command} `)}><code>{command}</code><span>{description}</span></button>)}</div>}
        {draftRestored && <div className="draft-restored" role="status">Continue your saved request · attachments are stored privately until you send.</div>}
        {savedAttachments.length > 0 && <div className="attachment-history"><span className="eyebrow">Sent thread evidence</span>{savedAttachments.map((attachment) => <span className="attachment-chip saved" key={attachment.id}><span aria-hidden="true">⊙</span>{attachment.filename}</span>)}</div>}
        {attachments.length > 0 && <div className="attachment-tray"><span className="eyebrow">Staged locally · private until send</span>{attachments.map((attachment) => <span className="attachment-chip" key={attachment.id}>{attachment.previewUrl && <img className="attachment-thumb" src={attachment.previewUrl} alt={`Preview of ${attachment.file.name}`} />}{attachment.file.name}<button type="button" aria-label={`Remove ${attachment.file.name}`} onClick={() => void removeStagedAttachment(attachment)}>×</button></span>)}</div>}
        {error && <div className="inline-error chat-inline-error" role="alert"><span>{error}</span>{lastFailedPrompt && !isSending && <button type="button" className="inline-error-action" onClick={retryLastTurn}>Retry this turn</button>}</div>}
        <form ref={formRef} className="chat-composer" onSubmit={(event) => void send(event)}>
          {capabilities.attachments ? <><button type="button" className="attach-button" onClick={() => fileRef.current?.click()} disabled={isSending} aria-label="Attach a photo or file">＋</button><input ref={fileRef} type="file" multiple accept="image/*,audio/*,.pdf,.txt,.csv,.doc,.docx" onChange={(event) => void addFiles(event.target.files)} className="sr-only" /></> : <span className="composer-unavailable">Attachments are not enabled in this phase.</span>}
          <textarea value={message} onChange={(event) => { const nextValue = event.target.value; setMessage(nextValue); setDraftRestored(false); if (submittedContentRef.current && nextValue.trim() !== submittedContentRef.current) { clientMessageIdRef.current = null; submittedContentRef.current = null }; if (lastFailedPrompt && nextValue.trim() !== lastFailedPrompt) setLastFailedPrompt(null) }} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); formRef.current?.requestSubmit() } }} placeholder="Tell the Agent what you want to understand or get done… Type / for commands." rows={3} />
          <button type={isSending ? 'button' : 'submit'} className={`send-button ${isSending ? 'stop-button' : ''}`} onClick={isSending ? () => requestRef.current?.abort() : undefined} disabled={!isSending && !message.trim()} aria-label={isSending ? 'Stop response' : 'Send message'} title={isSending ? 'Stop response' : 'Send message'}>{isSending ? <><span aria-hidden="true">■</span><span className="sr-only">Stop response</span></> : <><span aria-hidden="true">↑</span><span className="sr-only">Send message</span></>}</button>
        </form>
        <div className="composer-hint-row"><span>Attachments stay private until you send.</span><span>{isSending ? 'Stop anytime · edit and send again' : 'Enter sends · Shift + Enter for a new line'}</span></div>
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

function AssistantMarkdown({ content }: { content: string }) {
  return <div className="assistant-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ table: ({ children }) => <div className="markdown-table-wrap"><table>{children}</table></div> }}>{content}</ReactMarkdown></div>
}

function MessageBubble({ message, userInitials, ticketId, onReuse, onTicketAction }: { message: AgentMessage; userInitials: string; ticketId: string | null; onReuse: (content: string) => void; onTicketAction: (action: Record<string, unknown>) => void }) {
  if (message.role === 'user') {
    const attachments = message.parts.filter((part) => part.type === 'attachment')
    return <div className="user-message"><div className="message-content"><p>{message.content}</p></div><span className="message-avatar message-avatar-user" aria-hidden="true">{userInitials}</span>{attachments.length > 0 && <div className="message-supporting user-message-attachments">{attachments.map((part) => <span className="message-attachment" key={part.attachment_id}>{part.text ?? 'Attached evidence'}</span>)}<button className="message-reuse" onClick={() => onReuse(message.content)}>Edit / use again</button></div>}</div>
  }
  const citations = message.parts.filter((part) => part.type === 'citation')
  const actions = message.parts.filter((part) => part.type === 'action')
  const toolParts = message.parts.filter((part) => part.type === 'tool' || part.type === 'status' || part.type === 'command')
  return <div className="assistant-message"><span className="message-avatar">CX</span><div className="assistant-message-stack"><div className="message-content"><AssistantMarkdown content={message.content} /></div>{toolParts.length > 0 && <details className="hermes-trace" open={false}><summary><span className="eyebrow">Hermes turn trace</span><span>{toolParts.length} events</span></summary><div>{toolParts.map((part, index) => { const toolName = typeof part.data?.tool_name === 'string' ? part.data.tool_name : part.type; return <div className="hermes-trace-row" key={`${part.type}-${index}`}><span className="dot dot-lime" /><code>{part.text ?? toolName}</code><span>{part.type === 'command' ? 'command' : part.data?.status === 'planned' ? 'planned' : 'completed'}</span></div> })}</div></details>}{citations.length > 0 && <div className="message-supporting message-citations"><p className="eyebrow">Source passages · verified record</p>{citations.map((part, index) => { const passage = typeof part.data?.passage === 'string' ? part.data.passage : ''; const original = typeof part.data?.original_passage === 'string' ? part.data.original_passage : ''; const title = typeof part.data?.title === 'string' ? part.data.title : part.text ?? 'Source passage'; const authority = typeof part.data?.authority === 'string' ? part.data.authority : 'Official authority'; const url = typeof part.data?.url === 'string' ? part.data.url : ''; return <details key={`${part.attachment_id}-${index}`}><summary>{title}</summary><div className="citation-meta"><span>{authority}</span><span>Page {typeof part.data?.page === 'number' ? part.data.page : '—'}</span><span>Retrieved {typeof part.data?.retrieved_at === 'string' ? formatDate(part.data.retrieved_at) : 'unknown'}</span>{url && <a href={url} target="_blank" rel="noreferrer">Official page ↗</a>}</div><p>{passage || 'No extractable passage was available for this source.'}</p>{original && <small>Original text: {original}</small>}</details> })}</div>}{actions.map((part, index) => <AgentAction key={`${part.type}-${index}`} part={part} ticketId={ticketId} onTicketAction={onTicketAction} />)}</div></div>
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
  const [isPreparing, setIsPreparing] = useState(false)
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
      const prepared = await prepareTicket(ticket.ticket.id)
      setPreparation(prepared)
      try { setPreparation(await getTicketPreparation(ticket.ticket.id)) }
      catch { setError('The preparation was saved, but refreshing its status failed. You can continue reviewing this checkpoint.') }
    } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not prepare the authority review') }
    finally { setIsPreparing(false) }
  }
  const approve = async () => {
    if (!ticket || !preparation) return
    setIsPreparing(true); setError(null)
    try { await approveTicketPreparation(ticket.ticket.id, preparation.content_hash); setPreparation({ ...preparation, status: 'approved', approved_at: new Date().toISOString() }); setError(null) } catch (nextError) { setError(nextError instanceof Error ? nextError.message : 'Could not save review approval') } finally { setIsPreparing(false) }
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
  return <div className="drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><aside ref={drawerRef} className="ticket-drawer" role="dialog" aria-modal="true" aria-labelledby="ticket-drawer-title"><div className="drawer-header"><div><p className="eyebrow">{ticket ? 'CivitasX case workspace' : 'Private case draft'}</p><h2 id="ticket-drawer-title">{ticket ? ticket.ticket.civitas_ticket_id : 'New complaint'}</h2></div><button ref={closeRef} className="drawer-close" onClick={onClose} aria-label="Close ticket drawer">×</button></div>{!ticket ? <form className="ticket-form" onSubmit={(event) => void create(event)}><p className="drawer-lede">Save a private CivitasX case first. Nothing is sent to a government authority by creating this case.</p><label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={160} required /></label><label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={7} maxLength={5000} required /></label><label>Locality or landmark<input value={locality} onChange={(event) => setLocality(event.target.value)} maxLength={160} placeholder="e.g. Indiranagar 12th Main" /></label>{error && <div className="inline-error" role="alert">{error}</div>}<div className="route-preview"><span className="eyebrow">Suggested authority · not yet submitted</span><strong>{authorityLabel}</strong>{typeof action?.contact_route === 'string' && <small>{action.contact_route}</small>}</div><button className="button button-dark button-full" disabled={isWorking}>{isWorking ? 'Saving…' : 'Save private Civitas case'} <span aria-hidden="true">↗</span></button></form> : <div className="ticket-created"><div className="ticket-id-block"><span className="eyebrow">CivitasX ticket ID · app tracking only</span><strong>{ticket.ticket.civitas_ticket_id}</strong><span className={`status-chip ${ticket.ticket.status === 'not_solved' ? 'attention' : ticket.ticket.status === 'resolved' ? 'resolved' : 'working'}`}><span className={`dot dot-${ticket.ticket.status === 'not_solved' ? 'attention' : ticket.ticket.status === 'resolved' ? 'resolved' : 'working'}`} /> {statusLabel(ticket.ticket.status)}</span></div><div className="ticket-summary"><span className="eyebrow">What will be tracked</span><h3>{ticket.ticket.title}</h3><p>{ticket.ticket.description}</p><span>{ticket.ticket.locality ?? 'Bengaluru'} · {authorityLabel}</span><small className="trust-note">This CivitasX ID is not a government reference until an official connector confirms submission.</small></div><div className="ticket-history"><span className="eyebrow">Status history</span>{ticket.history.map((event) => <div key={event.id}><span className={`dot dot-${event.status === 'not_solved' ? 'attention' : event.status === 'resolved' ? 'resolved' : 'working'}`} /><span>{statusLabel(event.status)}</span><small>{formatTime(event.created_at)}</small></div>)}</div><div className="preparation-card"><div><span className="eyebrow">Prepare official submission</span><strong>{preparation ? preparation.status.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()) : 'Not checked yet'}</strong></div>{preparation ? <><p>{preparation.missing_fields.length ? `Still needed: ${preparation.missing_fields.join(', ')}.` : 'The exact local payload is ready to review. This hash binds the approval to these fields.'}</p><code>{preparation.content_hash.slice(0, 18)}…</code><button className="button button-dark button-full" onClick={() => void approve()} disabled={isPreparing || preparation.status === 'approved' || preparation.missing_fields.length > 0}>{preparation.status === 'approved' ? 'Review checkpoint saved' : 'Approve this preparation'}</button></> : <><p>Check the verified authority connector for required fields and save a resumable review checkpoint.</p><button className="button button-outline button-full" onClick={() => void prepare()} disabled={isPreparing}>{isPreparing ? 'Checking requirements…' : 'Prepare official submission'}</button></>}</div>{error && <div className="inline-error" role="alert">{error}</div>}{preparation?.status === 'approved' && <div className="inline-success" role="status">Review checkpoint saved. Submission remains disabled until a verified connector and your final approval are available.</div>}<button className="button button-outline button-full" onClick={() => void downloadBrief()} disabled={isExporting}>{isExporting ? 'Preparing brief…' : 'Download evidence brief'} <span aria-hidden="true">↓</span></button><button className="button button-outline button-full" onClick={openPreview}>Share this unsubmitted report</button><button className="button button-dark button-full" onClick={onClose}>Keep this case private</button>{showPublish && <form className="public-preview" onSubmit={(event) => void publish(event)}><div className="preview-label"><span className="eyebrow">What neighbours will see</span><span>Unsubmitted public snapshot</span></div><p>Publishing shares this redacted text to the selected audience. It does not submit anything to a government authority. Private attachments stay out of the post.</p><div className="redaction-checklist"><strong>Review privacy risks before sharing</strong>{redactionRisks.map((risk) => <label key={risk}><input type="checkbox" required /> <span>{risk}</span></label>)}</div><label>Public title<input value={publicTitle} onChange={(event) => setPublicTitle(event.target.value)} maxLength={160} required /></label><label>Public body<textarea value={publicBody} onChange={(event) => setPublicBody(event.target.value)} rows={5} maxLength={5000} required /></label><label>Audience<select value={publicVisibility} onChange={(event) => setPublicVisibility(event.target.value as 'nearby' | 'locality' | 'citywide')}><option value="nearby">Nearby</option><option value="locality">Locality</option><option value="citywide">Citywide</option></select></label><button className="button button-dark button-full" disabled={isWorking}>{isWorking ? 'Sharing…' : 'Approve and share unsubmitted report'} <span aria-hidden="true">↗</span></button></form>}</div>}</aside></div>
}

function PasswordVisibilityIcon({ visible }: { visible: boolean }) {
  return <svg className="password-toggle-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M2.062 12.348a1 1 0 0 1 0-.696C3.5 7.59 7.42 4.5 12 4.5s8.5 3.09 9.938 7.152a1 1 0 0 1 0 .696C20.5 16.41 16.58 19.5 12 19.5s-8.5-3.09-9.938-7.152Z" /><path d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />{!visible && <path d="m3 3 18 18" />}</svg>
}

function BrandMark() { return <span className="brand-mark" aria-hidden="true"><span /><span /><span /></span> }
