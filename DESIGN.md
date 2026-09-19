---
name: CivitasX Bengaluru
description: An ink-and-sand civic record for saving questions and returning to them.
colors:
  ink: "#0c1633"
  paper: "#f3f0e8"
  paper-deep: "#e7e3d9"
  paper-light: "#faf9f5"
  lime: "#c8f45b"
  lime-deep: "#9bbf2d"
  coral: "#ff725a"
  ink-soft: "#4f596d"
  line: "rgba(12, 22, 51, .14)"
typography:
  display:
    fontFamily: "Fraunces, Georgia, serif"
    fontSize: "clamp(46px, 7vw, 100px)"
    fontWeight: 600
    lineHeight: 0.9
    letterSpacing: "-0.075em"
  headline:
    fontFamily: "Fraunces, Georgia, serif"
    fontSize: "clamp(30px, 4vw, 46px)"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.055em"
  body:
    fontFamily: "DM Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: "DM Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "10px"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "0.16em"
rounded:
  none: "0px"
  soft: "10px"
  card: "16px"
  shell: "24px"
spacing:
  sm: "8px"
  md: "16px"
  lg: "32px"
  xl: "64px"
components:
  button-primary:
    backgroundColor: "{colors.lime}"
    textColor: "{colors.ink}"
    rounded: "{rounded.soft}"
    padding: "13px 18px"
  input-field:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.soft}"
    padding: "14px"
  record-card:
    backgroundColor: "{colors.paper-light}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "19px"

# Design System: CivitasX Bengaluru

## Overview

**Creative North Star: "The Civic Score"**

CivitasX treats a resident’s question as a living record and a civic issue as a
shared public signal. The interface borrows the vertical score of notation: a
slim rail marks the Agent or ticket journey, while the main field gives one
clear action room to breathe. The visual language is an ink-and-sand workspace
with small stamped labels, a bright lime state for available action, coral for
the moment that needs attention, and a warm paper texture that makes the civic
workspace feel local and hospitable. The local Feed + Agent slice adds a dense
public issue stream and a calm private conversation without changing that score
grammar. Future surfaces keep two distinct modes:
a dense, scannable Feed for public discovery and a calm, streaming Agent thread
for private work.

The system is intentionally calm around consequential work. It does not
pretend that a saved goal is researched or submitted. Layered panels and
activity marks show what has happened, what is next, and which work remains
future capability.

**Bengaluru Pattern Rule:** Light page canvases may use the public
`/backgrounds/bengaluru-doodle-light.png` illustration as a fixed, repeating
paper texture at low contrast. Reading surfaces, forms, and cards stay on
opaque paper-light; the dark navigation rail remains solid ink. Never place the
pattern behind dense copy without a paper wash, and never use the illustration
as a status or interaction signal.

**Key Characteristics:**

- A vertical score rail makes progress legible without adding a second menu.
- Fraunces display type gives the civic record a human voice; DM Mono labels its provenance and state.
- Lime means available action or completed progress; coral marks emphasis, intervention, or uncertainty.
- Soft, quiet surfaces and a restrained shadow give actions physical weight without making the workspace feel severe.

## Colors

The palette uses a deep navy ink against warm paper, with two small signals that
are functional rather than decorative.

### Primary

- **Field Lime** (#c8f45b): Primary action, completed score markers, and active progress.
- **Record Coral** (#ff725a): Emphasis, attention, identity stamps, and the next meaningful point in the score.

### Neutral

- **Deep Ink** (#0c1633): Main text, navigation rail, high-contrast action surfaces.
- **Quiet Ink** (#4f596d): Supporting text, dates, labels, and non-active states.
- **Warm Paper** (#f3f0e8): The application field and editable text background.
- **Paper Light** (#faf9f5): Cards and forms that need a raised reading surface.
- **Paper Deep** (#e7e3d9): Notices and secondary tonal grouping.
- **Hairline** (rgba(12, 22, 51, .14)): Dividers and inactive borders.

### Named Rules

**The Signal Has a Job Rule.** Lime and coral identify action, attention, or
progress. They do not become a decorative gradient or a collection of random
badges.

## Typography

**Display Font:** Fraunces (with Georgia, serif)

**Body Font:** DM Sans (with a system sans fallback)

**Label/Mono Font:** DM Mono (with a ui-monospace fallback)

**Character:** Fraunces carries the voice of a resident’s question; DM Sans
keeps forms and explanations easy to read; DM Mono makes provenance and state
feel recorded rather than ornamental.

### Hierarchy

- **Display** (600, `clamp(46px, 7vw, 100px)`, 0.9): First-viewport questions and case titles.
- **Headline** (600, `clamp(30px, 4vw, 46px)`, 1): Section heads and compact page statements.
- **Title** (600, 17–19px, 1.2): Saved case names and activity items.
- **Body** (400, 13–15px, 1.5–1.65): Explanations, helper copy, and editable content.
- **Label** (500, 9–11px, 1.3, tracked uppercase): Provenance, status, dates, and navigation context.

### Named Rules

**The Two Voices Rule.** Use Fraunces for the human question or record title;
use DM Mono for state and metadata. Do not put long explanatory paragraphs in
the display face.

## Layout

Desktop uses a 72px dark rail and a flexible main field with a 73px top bar.
Content is constrained to a 1500px reading field with wide side padding. The
signed-in experience opens on Feed; Agent keeps the same rail and top bar while
using a thread sidebar, message stream, attachment composer, and ticket drawer.
The earlier saved-case view remains available to preserve the existing research
record and evidence workflow.

The 680px breakpoint turns the rail into a fixed bottom score, collapses the
account label, stacks starter cards, and removes low-priority table metadata.
The 920px breakpoint stacks case content and sidebar cards. Forms remain
single-column and retain their labels, visible focus, and inline error space.

## Elevation & Depth

Depth is mostly tonal: paper-light cards sit on the warm paper field, and the
dark rail anchors navigation. Soft warm shadows and 10–24px corners make the
workspace feel approachable without turning it into a glass dashboard. Hover
states lift interactive rows by 2–4px or switch them to lime, while reduced
motion removes those transitions.

### Named Rules

**The Soft Welcome Rule.** Use restrained, blurred shadows for surfaces that
invite reading or action; reserve stronger contrast for the dark rail, review
gates, and explicit approval boundaries.

## Shapes

Light reading surfaces use soft 10–24px corners; structural rails and metadata
remain crisp. Pill shapes are reserved for status, selection, and compact
filter controls. Circular geometry belongs to score markers and the account
avatar, where it communicates a point or identity.

## Components

### Buttons

- **Shape:** Soft 10px corners for primary controls; status and filter controls may use pills when selection is the point.
- **Primary:** Field Lime with deep ink text; 13px 18px padding; arrow glyph follows the label.
- **Hover / Focus:** Lime lightens and the button moves up 2px; focus uses a 3px coral outline with a 3px offset.
- **Secondary:** Deep Ink for recovery actions on the full-screen state.

### Cards / Containers

- **Corner Style:** Soft 16–24px corners for welcoming reading surfaces; square edges only where the score grammar needs structure.
- **Background:** Paper Light for editable or readable surfaces; Paper Deep for notices; Deep Ink for usage guardrails.
- **Shadow Strategy:** Soft blurred shadows for elevated cards and drawers; no decorative hard-offset shadow.
- **Border:** Hairline in the field; Deep Ink for the composer.
- **Internal Padding:** 15–20px for compact cards; larger page sections use the 32–64px rhythm.

### Inputs / Fields

- **Style:** Warm Paper background, hairline border, soft corners, and generous vertical padding.
- **Focus:** Deep Ink border, lime offset for text areas, and coral outline for keyboard focus visibility.
- **Error / Disabled:** Coral left rule with a soft coral wash for errors; disabled actions reduce opacity and remain readable.

### Navigation

- **Style:** Dark vertical score rail with Feed and Agent destinations, plus a
  ticket journey with three or more nodes and a single active lime marker.
- **Active:** Lime node and coral case marker; labels use tracked DM Mono.
- **Mobile:** The rail becomes a bottom fixed score with the same nodes and a compact record mark.

The Feed is allowed to feel more like a social stream: stacked posts, visible
vote/comment affordances, locality filters, and status markers. The Agent keeps
the editorial record style: streaming messages, attachment tray, citations,
clarification cards, and approval gates. A public post can open the Agent with
only its approved public context.

### Civic Score

The score rail and case stages represent the current saved state without implying
later capabilities have completed. Stage markers are semantic text plus visual
points, so progress remains available to keyboard and assistive technology users.

## Do's and Don'ts

### Do:

- **Do** let one goal own the opening interaction.
- **Do** keep state labels literal: Saved, Phase 2, Later phase, or a precise error.
- **Do** pair a material action with a recorded event or visible feedback.
- **Do** preserve wide reading margins and allow the display question to wrap naturally.
- **Do** keep lime and coral tied to the signal rule.

### Don't:

- **Don't** present a source-reference snapshot as a complete original archive,
  and do not present later drafting or submission as complete.
- **Don't** turn source or activity metadata into tiny unreadable gray text.
- **Don't** use generic dashboard widgets or rounded card grids that erase the score grammar.
- **Don't** hide errors in toasts when the form can show them beside the action.
