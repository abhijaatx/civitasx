# CivitasX Frontend Audit Report

> **Historical baseline:** this report captures an earlier implementation state. The current frontend build passes, and several findings below have since been addressed (capability gating, Cognito branching, feed debounce, vote rollback, modal focus, attachment persistence, and Agent error states). Use the latest critique snapshot under `.impeccable/critique/` plus a fresh browser audit as the source of truth.

Date: 2026-09-18  
Scope: `apps/web` React/Vite frontend, including Feed, Agent, account preferences, post details, comments, ticket drawer, authentication surface, and responsive behavior.

## Method and confidence

- Earlier baseline TypeScript/Vite build: passed. Current `npm run build`: fails with TS6133 errors for an unused `useLayoutEffect` import and unused `copyText` helper in `apps/web/src/App.tsx`.
- Impeccable detector: 6 findings, all side-tab accent patterns in `styles.css`.
- Live browser verification: desktop and 390×844 mobile viewport.
- Live flows checked: Feed load, Feed filters, Feed → Agent, new Agent conversation, post-detail dialog, mobile Agent composer, DOM accessibility names, hit-target dimensions, scroll width, and browser console warnings/errors.
- Browser console: no warnings/errors observed during the checked flows.
- Findings below are implementation-backed. Visual/design observations are labelled separately from deterministic code findings.

## Audit health score

| Dimension | Score | Summary |
|---|---:|---|
| Accessibility | 1/4 | Major mobile target-size failures and incomplete modal/menu semantics. |
| Performance | 2/4 | Small bundle, but filter keystrokes trigger requests and attachments are duplicated into data URLs/localStorage. |
| Theming | 2/4 | Core tokens exist, but status/error colors and opacity values bypass the token system. |
| Responsive design | 1/4 | Mobile feed controls become unreadable and many interactive targets are far below 44px. |
| Implementation integrity | 1/4 | Current source does not compile; repeated side-tab accents and incomplete interaction fixes also remain. |
| **Total** | **7/20** | **Poor — the current worktree has a release-blocking build failure.** |

## Executive summary

Found 36 actionable issues: 1 P0, 10 P1, 20 P2, and 5 P3. The prior running build was reachable, but the current source state has a P0 compile failure.

The highest-risk issues are:

1. The current frontend build is blocked by unused `useLayoutEffect` and `copyText` symbols.
2. Mobile Feed actions and controls are too small for reliable touch use.
3. The mobile filter row compresses five controls into roughly 54px-wide fields, visibly truncating labels and values.
4. The post-detail dialog does not move focus into the dialog or trap focus; keyboard users remain focused on the obscured page behind it.
5. Agent navigation can race and lose the prompt that the user selected from Feed.

Some first-pass findings were partially addressed in the current source (for example, notification error handling and remember/session storage). Those areas were re-audited below; only the remaining defects are counted as active.

### P0 — Blocking

#### [P0] Current TypeScript build is broken

- Location: `apps/web/src/App.tsx:1` and `apps/web/src/App.tsx:108-123`.
- Evidence: `npm run build` currently exits non-zero with `TS6133: 'useLayoutEffect' is declared but its value is never read` and `TS6133: 'copyText' is declared but its value is never read`.
- Impact: CI/production builds cannot complete, so the current frontend cannot be shipped or reliably previewed.
- Recommendation: Either wire both symbols into the intended focus/clipboard behavior or remove them, then run the full build again. Do not leave partial fixes as dead code.
- Suggested command: `/impeccable harden`.

## Detailed findings

### P1 — Fix before release

#### [P1] Configured Cognito mode is ignored by the authentication UI

- Location: `apps/web/src/App.tsx:225-245` and `apps/web/src/api.ts:131-147`.
- Evidence: `AppConfig.auth.mode` and Cognito metadata are loaded, but `AuthScreen` always calls the local `login()`/`register()` API. The config only changes the explanatory note from “local pilot” to “Secure account mode.”
- Impact: A deployment configured for Cognito presents a credential form that does not execute the configured authentication flow. Users cannot sign in/register through the intended provider, and the UI falsely implies the mode is active.
- Recommendation: Branch the auth surface on `config.auth.mode`, implement the Cognito redirect/token exchange, and make unsupported auth modes explicit rather than silently using local endpoints.
- Suggested command: `/impeccable harden`.

#### [P1] Capability flags do not gate the rendered product

- Location: `apps/web/src/App.tsx:149-211`, plus `AppConfig.capabilities` in `apps/web/src/types.ts:44-68`.
- Evidence: The app loads `capabilities.feed`, `agent`, `tickets`, `attachments`, `research`, `submission`, and `live_sources`, but only uses `config.auth.mode` in the rendered UI. Feed and Agent are always mounted, and their actions always call the corresponding APIs.
- Impact: Phase/capability configuration cannot prevent unavailable or future functionality from being shown. Users can reach controls that are unsupported in the current deployment and receive avoidable API failures, contradicting the product requirement to label unsupported requests and later capabilities.
- Recommendation: Gate surfaces/actions from the capability object, show explicit “not available in this phase” states, and disable/hide attachment, ticket, research, submission, and connector actions when unavailable.
- Suggested command: `/impeccable harden`.

#### [P1] Mobile interactive controls fail touch-target guidance

- Location: `apps/web/src/styles.css:560`, `apps/web/src/styles.css:660`, `apps/web/src/styles.css:620-623`; also the top-bar controls around `apps/web/src/styles.css`’s `.inbox-button`/`.account-button` rules.
- Category: Accessibility / Responsive.
- Evidence: At 390px viewport, live measurement found Feed post action buttons at 36–60px wide and only 12px high; vote buttons were about 18×23px; Activity was about 22×30px; the account button was 29×29px. Several ticket/action buttons were about 10–12px high.
- Impact: Users with motor impairments and mobile users can miss controls or activate the wrong adjacent action. This is especially risky for vote, follow, report, archive, and close actions.
- Standard: WCAG 2.2 SC 2.5.8 Target Size (Minimum); use 44×44px as the practical target across the interface.
- Recommendation: Give every interactive control a minimum 44px hit area, using padding or a pseudo-element without changing the visual typography. Keep icon-only controls labelled.
- Suggested command: `/impeccable adapt` then `/impeccable audit`.

#### [P1] Mobile Feed filters collapse into unreadable controls

- Location: `apps/web/src/styles.css:631-633`.
- Evidence: At 390px viewport, live DOM measurements found the locality, status, authority, and search controls each compressed to approximately 53.9px wide. The screenshot visibly showed clipped select/search content.
- Impact: Users cannot reliably discover or select locality, status, authority, or search values; the primary public-feed discovery workflow becomes guesswork on mobile.
- Recommendation: Stack filters, use a two-column layout with explicit minimum widths, or provide a dedicated filter drawer. Never allow the fields to flex below a readable minimum.
- Suggested command: `/impeccable adapt`.

#### [P1] Post-detail modal does not manage focus

- Location: `apps/web/src/App.tsx:437-442`.
- Evidence: The dialog has `role="dialog"` and `aria-modal="true"`, but opening it left focus on the background post-title button. There is Escape handling but no initial focus, focus trap, or focus restoration.
- Impact: Keyboard and screen-reader users can continue tabbing through obscured Feed content, lose their place, and interact with background controls while a modal is open.
- Standard: WCAG 2.1 SC 2.4.3 Focus Order, SC 2.4.7/2.4.11 Focus Visible; WAI-ARIA modal dialog pattern.
- Recommendation: Store the trigger element, focus the close button or dialog heading on open, trap Tab/Shift+Tab inside the dialog, restore focus on close, and prevent background scrolling.
- Suggested command: `/impeccable harden`.

#### [P1] “Got it” can recreate durable profile storage after session-only memory is selected

- Location: `apps/web/src/App.tsx:346-358` and the Feed onboarding action around line 388.
- Evidence: Privacy settings now use sessionStorage when `remember` is false, but the onboarding “Got it” handler still reads localStorage directly and writes `onboardingSeen` back to localStorage. Dismissing the hint can therefore recreate a persistent profile key after the user selected session-only memory.
- Impact: The UI promises session-only memory, but a normal first-visit action can persist profile/onboarding data beyond the session.
- Recommendation: Route onboarding writes through the same storage policy as profile preferences and test the disable-remember → dismiss hint → reload flow.
- Suggested command: `/impeccable harden`.

#### [P1] Failed optimistic votes leave incorrect counts

- Location: `apps/web/src/App.tsx:459-465`.
- Evidence: The optimistic update changes `upvotes` and `downvotes`, but the error path restores only `user_vote` and `vote_score`.
- Impact: A failed vote can leave the visible upvote/downvote totals wrong until a full reload, undermining trust in civic signal counts.
- Recommendation: Snapshot and restore all affected fields (`user_vote`, `vote_score`, `upvotes`, `downvotes`) on failure, or refetch the post after an error. Disable repeat submission while pending.
- Suggested command: `/impeccable harden`.

### P2 — Fix in the next quality pass

#### [P2] Feed status and authority filters are hard-coded and incomplete

- Location: `apps/web/src/App.tsx:63-70` and the filter markup around line 388; backend status enum: `services/api/src/civitas_api/models.py:537-550`.
- Evidence: The UI offers only four ticket statuses (`in_progress`, `acknowledged`, `not_solved`, `resolved`) while the backend supports draft, needs information, ready for review, submitted, awaiting confirmation, resolved pending confirmation, reopened, and outcome unknown. Authority options are also fixed to only GBA/BDA/BMRCL despite the backend exposing authority records.
- Impact: Users cannot discover or filter many legitimate civic states or authorities, particularly the review/submission states central to this product.
- Recommendation: Load filter options from the API or derive them from the shared contract, include all user-relevant statuses, and label unavailable/legacy statuses intentionally.
- Suggested command: `/impeccable harden`.

#### [P2] Service/bootstrap failures can fall through to a misleading sign-in screen

- Location: `apps/web/src/App.tsx:149-192`.
- Evidence: If `getConfig()` succeeds but `getMe()` or the initial `refreshThreads()` fails for a non-401 reason, `bootError` is set while `config` remains non-null. The render condition then skips `StartupError` and returns `AuthScreen` because `user` is null.
- Impact: A backend outage or timeout can be presented as “please sign in,” causing users to re-enter credentials or misdiagnose an infrastructure failure.
- Recommendation: Distinguish unauthenticated 401 from bootstrap/network errors and show a retryable service state whenever the authenticated session check cannot be completed.
- Suggested command: `/impeccable harden`.

#### [P2] Feed search debounce state is dead and requests still fire on every keystroke

- Location: `apps/web/src/App.tsx:374`, `apps/web/src/App.tsx:391-403`, and the search input around line 448.
- Evidence: `topicInput` has a 300ms debounce effect, but the input is bound to `topic` and its `onChange` calls `setTopic(...)` directly. `topicInput` is never updated, so the debounce never controls the API query.
- Category: Performance.
- Impact: Typing a multi-character search creates one API request per character, increasing latency, server load, and race/error noise on slower connections.
- Recommendation: Debounce the query by roughly 250–400ms, or submit on Enter/apply. Keep sequence cancellation as a second safety net.
- Suggested command: `/impeccable optimize`.

#### [P2] Attachment drafts duplicate entire files into memory and localStorage

- Location: `apps/web/src/App.tsx:614-621`, `apps/web/src/App.tsx:540-555`.
- Impact: Every staged file is read as a data URL and serialized into localStorage. Large or multiple files can exceed quota, cause expensive main-thread work, and silently fail persistence while the UI still says attachments are staged for recovery.
- Recommendation: Enforce client-side size/count limits, persist only metadata or use IndexedDB, show a persistence failure, and revoke restored object URLs on removal/unmount.
- Suggested command: `/impeccable optimize`.

#### [P2] Account dialog focuses in but does not trap or restore focus

- Location: `apps/web/src/App.tsx:324-329`.
- Category: Accessibility.
- Evidence: The current source changed the container to `role="dialog"` and focuses its first control on open, but Escape/outside-click closes it without restoring focus to `accountButtonRef`, and there is no Tab trap. Focus can escape into the page behind the dialog.
- Impact: Keyboard users can lose their place and interact with background controls while account settings are open.
- Recommendation: Store the trigger, trap focus while the dialog is open, and restore focus on every close path.
- Suggested command: `/impeccable harden`.

#### [P2] Feed sort tabs are visually tab-like but incomplete as a tab pattern

- Location: `apps/web/src/App.tsx:362`.
- Impact: The tabs have `role="tab"` and `aria-selected`, but no `aria-controls`, tabpanel, roving tabindex, or arrow-key behavior. Keyboard users receive a partial/ambiguous tab interaction model.
- Recommendation: Either implement the full tab pattern or use ordinary buttons with `aria-pressed` for sort selection.
- Suggested command: `/impeccable harden`.

#### [P2] Clipboard actions claim success without verifying clipboard availability

- Location: `apps/web/src/App.tsx:370` and `apps/web/src/App.tsx:415-417`.
- Impact: `navigator.clipboard?.writeText(...)` is optional-chained, but the share flow still says “Share link copied” when clipboard access is unavailable or returns no promise. Copy-link has no user feedback at all.
- Recommendation: Check for clipboard support and await/catch it explicitly; provide a fallback selection/copy path and distinct “link created” vs “copied” status.
- Suggested command: `/impeccable harden`.

#### [P2] Archive is a destructive, one-click action with no confirmation or undo

- Location: `apps/web/src/App.tsx:483-484` and the archive button in the thread list around line 500.
- Impact: The small × button immediately archives a conversation and removes it from the active list. Accidental loss is likely on mobile, and recovery is not exposed.
- Recommendation: Add a confirm step or undo snackbar, label the action “Archive” rather than ×, and expose an archived view/recovery path.
- Suggested command: `/impeccable harden`.

#### [P2] Rename/archive rely on browser `prompt()`/`alert()` dialogs

- Location: `apps/web/src/App.tsx:478-484`.
- Impact: Native dialogs interrupt the task, are inconsistent with the product’s UI, are difficult to style, and provide poor accessible context. Error recovery is detached from the relevant row.
- Recommendation: Use an inline rename field and an in-app confirmation/error state.
- Suggested command: `/impeccable clarify`.

#### [P2] Post-detail dialog and menu overlays do not lock background scrolling or consistently dismiss on outside interaction

- Location: `apps/web/src/App.tsx:365-370`, `apps/web/src/App.tsx:386`, and the `.status-menu`/`.more-actions` styles around `styles.css:558-560` and `676-679`.
- Impact: Users can scroll or leave contextual menus open while interacting with unrelated content; overlay state becomes ambiguous on long Feed pages.
- Recommendation: Centralize overlay behavior with outside-click dismissal, body scroll locking for dialogs, and explicit ownership of open state.
- Suggested command: `/impeccable harden`.

#### [P2] Hard-coded status/error colors bypass the token system

- Location: `apps/web/src/styles.css:76`, `apps/web/src/styles.css:538-543`, plus repeated rgba literals throughout the file.
- Category: Theming / Implementation integrity.
- Impact: Status colors cannot be adjusted consistently for contrast, future themes, or a dark mode. The same semantic state is encoded with scattered raw values.
- Recommendation: Add semantic tokens such as `--success`, `--success-strong`, `--warning`, `--error-surface`, and `--focus-ring`; replace raw values and document contrast requirements.
- Suggested command: `/impeccable colorize`.

### P3 — Polish

#### [P3] Repeated side-tab accent borders trigger the implementation-integrity detector

- Location: `apps/web/src/styles.css:76`, `190`, `246`, `375`, `542-543`.
- Evidence: The bundled detector reported six “Side-tab accent border” findings.
- Impact: Repetition makes unrelated notices, cards, and success states look mechanically generated and weakens the stated “signal has a job” rule.
- Recommendation: Keep the accent for one clearly semantic component and use subtler borders, background, or icon treatment elsewhere.
- Suggested command: `/impeccable polish`.

#### [P3] Ticket/detail close controls are visually below the product’s own target-size intent

- Location: `apps/web/src/styles.css:501`, `.drawer-close`/`.post-detail > .drawer-close`.
- Impact: The × glyph has a small visual and hit area, particularly problematic on mobile drawers.
- Recommendation: Use a 44px square hit area while keeping the glyph visually restrained.
- Suggested command: `/impeccable adapt`.

#### [P3] Attachment preview images use empty alt text without an adjacent accessible filename relationship

- Location: `apps/web/src/App.tsx:633`.
- Impact: The image is decorative in isolation, but screen-reader users may not get a clear association between the preview and the filename when attachment chips are announced.
- Recommendation: Use `alt="Preview of <filename>"` when the image conveys file identity, or mark it decorative and ensure the filename is the accessible name of the chip.
- Suggested command: `/impeccable harden`.

## Deep-dive findings from the second pass

### P1 — Fix before release

#### [P1] Feed-to-Agent navigation has an async race that can lose the requested prompt

- Location: `apps/web/src/App.tsx:458-498`, especially the `AgentView` effect at lines 462-468 and the `initialPrompt` effect at lines 493-498.
- Evidence: When Feed opens Agent with a prompt and existing threads are present, one effect calls `openThread(threads[0].id)` while another calls `newThread(initialPrompt)`. Both update `detail`; whichever request resolves last wins.
- Impact: The user can land in an unrelated existing conversation instead of the conversation created for the issue they just selected. The prompt can appear to vanish, which is especially damaging for a civic complaint workflow.
- Recommendation: Gate initial-prompt creation before auto-opening an existing thread, or use a request-generation token and make the initial prompt the authoritative navigation intent.
- Suggested command: `/impeccable harden`.

#### [P1] Logout failure leaves a cleared-token user inside the authenticated UI

- Location: `apps/web/src/api.ts:149-155` and `apps/web/src/App.tsx:183-188`.
- Evidence: `logout()` clears the token in `finally` but rethrows a network/API error. `handleSignOut()` only clears React auth state after `await logout()` succeeds and has no catch.
- Impact: If logout’s request fails, the token is removed but the Feed/Agent remains visible with an invalid session. Subsequent requests fail, and the user has no reliable sign-in recovery without a reload.
- Recommendation: Treat local sign-out as successful after clearing the token, show a non-blocking server-sync warning if needed, and always clear React auth state in `handleSignOut()`.
- Suggested command: `/impeccable harden`.

#### [P1] Core Agent creation actions fail silently

- Location: `apps/web/src/App.tsx:472-477`, `489-492`, `493-498`, and the `New conversation`/starter button handlers around lines 501 and 505.
- Evidence: `newThread()` has no try/catch or local error state, and callers intentionally discard the promise with `void newThread()`.
- Impact: A failed create request leaves the user on the empty Agent view with no explanation, despite clicking a primary action. The same applies to starter prompts and Feed-to-Agent entry.
- Recommendation: Add an Agent-level error boundary/status region, disable creation controls while pending, preserve the requested prompt, and expose retry.
- Suggested command: `/impeccable harden`.

### P2 — Fix in the next quality pass

#### [P2] Failed thread loads leave stale conversation content visible

- Location: `apps/web/src/App.tsx:458-461`.
- Evidence: `openThread()` catches errors by calling `onSelectThread(null)` but does not clear `detail` or set an error. After `loading` ends, the previous detail can remain rendered.
- Impact: Users may read or edit the wrong conversation while believing the newly selected thread opened successfully.
- Recommendation: Clear or quarantine stale detail while loading, show a labelled error state, and provide retry/return-to-list actions.
- Suggested command: `/impeccable harden`.

#### [P2] Stop-response does not cancel attachment uploads and can create orphaned/duplicate attachments

- Location: `apps/web/src/App.tsx:565-601` and `apps/web/src/api.ts:433-452`.
- Evidence: The AbortController is passed only to `sendAgentMessageStream`; `uploadAgentAttachment()` has no signal. Uploads happen before the stream, and a stopped/failed stream leaves already-uploaded attachment records on the server. Retrying uploads the same staged files again.
- Impact: Stopping a request does not actually stop all work, may waste bandwidth/storage, and can create duplicate evidence records.
- Recommendation: Pass the abort signal into uploads, track uploaded IDs, delete or reuse staged uploads after a cancelled send, and distinguish “uploaded” from “attached to a message.”
- Suggested command: `/impeccable optimize`.

#### [P2] SSE parser drops a final event without a blank-line terminator

- Location: `apps/web/src/api.ts:405-430`.
- Evidence: The parser processes only events produced by `buffer.split('\n\n')`; any event remaining in `buffer` when `reader.read()` returns `done` is never parsed.
- Impact: A terminal `done`, status, or final message event can be silently lost depending on server framing. The UI may miss final progress state even though the subsequent thread fetch eventually recovers some data.
- Recommendation: Flush and parse the remaining buffer after the read loop, normalize CRLF, and add an SSE framing test for a stream ending without an extra blank line.
- Suggested command: `/impeccable harden`.

#### [P2] Agent live region encompasses the entire conversation history

- Location: `apps/web/src/App.tsx:633` (`.message-list[aria-live="polite"]`).
- Impact: Streaming changes occur inside a live region containing every prior message, citations, traces, and actions. Screen readers may repeatedly re-announce large portions of the conversation, making long Agent sessions exhausting and obscuring the newest response.
- Recommendation: Remove live semantics from the history container and give only the streaming/status node a concise `role="status"` or `aria-live` region. Announce completion separately.
- Suggested command: `/impeccable harden`.

#### [P2] Command palette uses listbox role without option semantics or keyboard model

- Location: `apps/web/src/App.tsx:633` and the command menu CSS around `styles.css:464-469`.
- Evidence: The container is `role="listbox"`, but children are plain buttons with no `role="option"`, `aria-selected`, active descendant, or arrow-key handling.
- Impact: Assistive technology receives an incomplete widget model, and keyboard users cannot navigate the command suggestions predictably.
- Recommendation: Use a real combobox/listbox implementation or use a labelled disclosure list of ordinary buttons without `listbox` semantics.
- Suggested command: `/impeccable harden`.

#### [P2] Heading is nested inside a button in every Feed post

- Location: `apps/web/src/App.tsx:421` (`button.post-title-link` containing `<h2>`).
- Impact: A button’s content model is phrasing content; placing a section heading inside it produces invalid/fragile semantics. Screen readers can expose the title as a button rather than a navigable heading, weakening document structure and post discovery.
- Recommendation: Keep the `<h2>` as the heading and place a separate labelled button/link around the title action, or use a heading with an adjacent action control.
- Suggested command: `/impeccable harden`.

#### [P2] Closing a post dialog adds a history entry instead of returning cleanly

- Location: `apps/web/src/App.tsx:431-432`.
- Evidence: `openPost()` uses `pushState()` to add `#post=...`; `closePost()` also uses `pushState()` to remove the hash. Browser Back after closing can therefore revisit the just-closed dialog.
- Impact: Back-button behavior feels broken and users can reopen a modal unexpectedly while navigating the Feed.
- Recommendation: Use `replaceState()` when closing, or use `history.back()` when the current history entry was created by the dialog.
- Suggested command: `/impeccable harden`.

#### [P2] Ticket preparation makes two dependent requests and masks partial success

- Location: `apps/web/src/App.tsx:782-785`.
- Evidence: `prepare()` awaits `prepareTicket()` and immediately awaits `getTicketPreparation()`. If the POST succeeds but the GET fails, the UI only shows an error and discards the successful preparation state.
- Impact: Users may repeat preparation, create duplicate server work, or assume the authority review was not prepared when it was.
- Recommendation: Render the POST response immediately, refresh opportunistically, and distinguish “prepared locally” from “refresh failed.”
- Suggested command: `/impeccable harden`.

#### [P2] Preference save can throw without an inline error

- Location: `apps/web/src/App.tsx:301`.
- Evidence: `window.localStorage.setItem()` is not wrapped in the same defensive try/catch used by the readers.
- Impact: Storage-disabled/private browsing/quota conditions cause the Save click to throw with no user-facing explanation, leaving the menu in an ambiguous state.
- Recommendation: Catch storage errors, keep the UI state explicit, and explain that preferences could not be saved locally.
- Suggested command: `/impeccable harden`.

### P3 — Polish

#### [P3] Active navigation does not expose current location semantics

- Location: `apps/web/src/App.tsx:249`.
- Impact: The Feed/Agent buttons visually show active state only through CSS. Assistive technology users do not receive `aria-current` or selected navigation state.
- Recommendation: Add `aria-current="page"` to the active destination, or use a tab/switch pattern consistently.
- Suggested command: `/impeccable harden`.

#### [P3] User comment deletion has no confirmation or undo

- Location: `apps/web/src/App.tsx:432-438`.
- Impact: A small text action can permanently remove a user comment with no recovery or accidental-click protection.
- Recommendation: Add an inline confirmation/undo pattern and keep the affected comment context visible.
- Suggested command: `/impeccable harden`.

## Systemic patterns

- Touch targets are consistently treated as visual text rather than interaction surfaces.
- Overlay primitives are inconsistent: the ticket drawer has focus trapping, while the post dialog and account popover do not.
- Semantic state colors are partly tokenized and partly hard-coded.
- Local persistence is treated as “best effort” even when the UI makes a durable privacy/recovery promise.
- Feed state is highly interactive but has limited pending/error rollback discipline.
- Agent navigation and streaming have multiple async boundaries without a single request ownership/cancellation model.
- Destructive actions (archive, delete comment, mute/report) lack a consistent confirmation/undo policy.

## Positive findings

- The earlier baseline build passed and the checked browser flows produced no console errors; the current worktree must first resolve the new TypeScript compile failure before it can be shipped.
- Core form labels are present for auth, Feed filters, comments, Agent search, and ticket fields; live DOM checks found no unnamed buttons or unlabeled text inputs in the tested states.
- The ticket drawer demonstrates a stronger modal pattern: it focuses the close control, handles Escape, and traps Tab focus (`apps/web/src/App.tsx:800-814`).
- The product clearly labels seeded civic records and distinguishes app ticket IDs from official government references.
- The Agent composer preserves drafts and gives explicit privacy/review language before upload or external action.
- The layout adapts to a bottom navigation rail and a mobile conversation selector rather than simply shrinking the desktop sidebar.

## Recommended action order

1. `/impeccable harden`: restore a passing TypeScript build by removing or wiring the dead `useLayoutEffect`/`copyText` code.
2. `/impeccable adapt`: fix mobile filter layout and all interactive hit areas.
3. `/impeccable harden`: fix Agent races/failures, logout recovery, modal semantics, privacy persistence, vote rollback, archive/delete recovery, and streaming announcements.
4. `/impeccable optimize`: wire the Feed debounce correctly, redesign attachment draft persistence, and cancel/reconcile uploads.
5. `/impeccable colorize`: consolidate semantic status/error/focus tokens.
6. `/impeccable polish`: reduce repeated side-tab accents and finish the interaction consistency pass.

Re-run `/impeccable audit` after fixes to verify the score improves.
