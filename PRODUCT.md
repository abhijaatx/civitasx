# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Local-first development uses the React/TypeScript frontend, Python FastAPI,
the MCP SDK, SQLite or optional local Postgres, filesystem or MinIO storage,
local Chromium/Playwright, and Ollama or llama.cpp. Provider interfaces keep a
direct migration path to AWS Amplify, Lambda, Cognito, DynamoDB, S3, Bedrock,
SQS/EventBridge, S3 Vectors, and AgentCore Browser. No AWS service or paid API
is required during development.

## Users

Bengaluru residents who want to see current civic issues, understand government
documents, participate in local discussions, create and track complaints, and
interact with government facilities. This includes people unfamiliar with
municipal departments and difficult government websites.

## Product Purpose

Operate a civic social network with two primary surfaces: a Reddit-like public
feed and a Codex-like private agent. Connect official evidence to
understandable explanations, editable complaint drafts, reviewed government
actions, stable ticket IDs, community discussion, and verified status updates.
Preserve requests so people can continue after interruptions without repeating
confirmed information.

## Operating Context

Citywide Bengaluru topics with city, ward, locality, and nearby discovery.
English interface, answers, and drafts; English and Kannada source material.
Local development first, with AWS introduced only after the local product is
working. One-week hackathon prototype, 2–3 builders with coding agents, and
optional AWS credits reserved for the later migration and controlled validation.

## Capabilities and Constraints

Phase 1 foundation, Phase 2 grounded research, and local vertical slices for
the public Feed, continuous Agent chat, attachments, complaint tickets,
redacted posting, votes, follows, comments, and notifications are implemented.
Reviewed browser actions, moderation, and provider-complete AWS adapters remain
future work. Local adapters remain the default;
AWS adapters are validated separately before migration. The UI must clearly
label prototype sources, unsupported requests, unverified statuses, and later
capabilities.

## Brand Commitments

Working name: CivitasX. Plain, respectful English. The product keeps its
ink-and-sand “Civic Score” visual language while adopting two recognizable
interaction models: a civic feed for discovery and a continuous agent thread
for action. The interface should feel calm and accountable when a ticket,
personal detail, or external submission is involved.

## Evidence on Hand

The repository contains a deterministic Bengaluru reference corpus, local
research implementation, seeded Feed records, and a local ticket/community
adapter. There is no completed government submission or production social
network yet. Sample tickets, statuses, public posts, and authority responses
must be labeled accurately.

## Product Principles

- Ask only for missing information and make reused details editable.
- Show evidence for material claims and be honest about missing information.
- Keep private cases isolated and persist work independently of browser sessions.
- Require review of the actual content and destination before submission.
- Show observed progress and verified outcomes rather than implying completion.
- Keep the private Agent and public Feed distinct while allowing a reviewed
  ticket to connect them.
- Use community voting to improve reach, never as proof of truth or official
  resolution.
- Prefer mature, compatible open-source components and keep product-specific
  ticket, privacy, connector, ranking, and approval logic in CivitasX-owned
  adapters.

## Accessibility & Inclusion

Support desktop and mobile layouts, keyboard navigation, visible focus, clearly
labeled forms, inline editing, accessible comment threads, attachment previews,
screen-reader announcements for streaming agent messages, and recoverable
errors. Location filters must work without precise-location permission. The
interface should minimize the effort required to resume a request or follow a
ticket.
