# Rayban PT MRBD HUD Design System

## 1. Atmosphere & Identity

A quiet clinical control surface that stays legible over the real world. The signature is a single bright state marker inside restrained, faintly luminous dark surfaces: the wearer should understand the current patient, capture state, and next action in one glance without reading a dashboard.

## 2. Color

| Role | Token | Value | Usage |
| --- | --- | --- | --- |
| Transparent canvas | `--bg-primary` | `#000000` | Additive-display page background only |
| Surface | `--bg-surface` | `#0a0d12` | Header, context, status |
| Strong surface | `--bg-surface-strong` | `#111822` | Commands, toast |
| Muted surface | `--bg-muted` | `#1c1e21` | Chips, inactive record surface |
| Primary text | `--text-primary` | `#ffffff` | Titles and actions |
| Secondary text | `--text-secondary` | `#e4e6eb` | Supporting copy |
| Muted text | `--text-muted` | `#b0b3b8` | Metadata |
| Focus/info | `--accent-primary` | `#00d4ff` | Focus and current selection only |
| Success | `--success` | `#20d7a4` | Connected, saved, ready |
| Warning | `--warning` | `#ffb020` | Waiting and processing |
| Error/recording | `--danger` | `#ff3b66` | Disconnected, destructive, recording |
| Border | `--border-default` | `rgba(255, 255, 255, 0.14)` | Visible surface edge |
| Border subtle | `--border-subtle` | `rgba(255, 255, 255, 0.10)` | Internal separation |
| Focus halo | `--focus-glow` | `rgba(0, 212, 255, 0.46)` | Focused control only |

Color never carries state alone. Every state also uses a short Korean label and, where useful, a shape marker.

## 3. Typography

Primary stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`.

| Level | Token | Size | Weight | Line height | Usage |
| --- | --- | --- | --- | --- | --- |
| H1 | `--type-h1` | 24px | 800 | 1.1 | App title |
| H2 | `--type-h2` | 24px | 900 | 1.15 | Current state |
| Body | `--type-body` | 16px | 700 | 1.3 | One-line cue or record |
| Action | `--type-action` | 16px | 900 | Commands |
| Meta | `--type-meta` | 14px | 800 | Connection, count, state chip |

Interactive text is never below 14px. Timers use tabular numerals. Korean copy uses sentence case and avoids English implementation terms.

## 4. Spacing & Layout

The fixed canvas is 600×600 with an 8px safe margin and a 584×584 content area. All spacing uses the 4px base unit.

| Token | Value | Usage |
| --- | --- | --- |
| `--space-1` | 4px | Tight icon/label spacing |
| `--space-2` | 8px | Grid gap and safe margin |
| `--space-3` | 12px | Compact panel padding |
| `--space-4` | 16px | Standard horizontal padding |
| `--space-6` | 24px | Large internal separation |

The screen uses named grid areas: compact header, patient context, one flexible status area, optional cue, and an explicit 96px command row. Hiding the cue must not move the command row. No element may exceed the 584×584 safe content bounds.

## 5. Components

### HUD header
- **Structure**: product label, Korean title, connection indicator.
- **States**: connecting, connected, disconnected.
- **Accessibility**: state is text plus dot color.

### Patient context
- **Structure**: context label, masked alias, readiness, event count.
- **States**: no patient, selected, active, disconnected.
- **Accessibility**: never exposes full identity; stale state is labeled when offline.

### Status card
- **Structure**: state marker, title, one primary message, optional one-record pager, short footer.
- **States**: waiting, ready, recording, uploading, analyzing, success, candidate, error, disconnected.
- **Interaction**: focusable only when a record or candidate can be paged.

### Record pager
- **Structure**: exactly one record line plus `current/total` indicator.
- **States**: empty, active, candidate.
- **Interaction**: left/right changes the record; it never becomes a free-scrolling list.

### Command rail
- **Structure**: one to three 88px buttons in a fixed bottom row.
- **States**: idle, focused, pressed, disabled, busy.
- **Interaction**: left/right wraps; up enters the status card; down returns to the rail; Enter activates.

### Toast
- **Structure**: one short, lens-safe sentence.
- **States**: info, success, error.
- **Content**: never exposes raw exception text, URLs, tokens, PHI, or English transport errors.

## 6. Motion & Interaction

| Type | Duration | Easing | Usage |
| --- | --- | --- | --- |
| Press | 100ms | `cubic-bezier(0.68, 0, 0.29, 1)` | Button press |
| Feedback | 200ms | `ease-out` | Toast and focus opacity |
| Focus color | 300ms | `cubic-bezier(0.4, 0.04, 0.5, 1)` | Focus border/background |

Only opacity and transform animate. There are no idle loops. `prefers-reduced-motion` removes non-essential transitions. Navigation is spatial and shallow: any primary command is reachable within two directional inputs.

## 7. Depth & Surface

Strategy: mixed, constrained by additive optics. Tonal shifts separate static surfaces, a 1px border preserves edges over bright environments, and glow appears only on the focused interactive control. Shadows are not used for decorative elevation.

## 8. Accessibility Constraints & Accepted Debt

- Target WCAG 2.2 AA contrast: 4.5:1 body text, 3:1 large text.
- Every enabled action is keyboard/Neural Band reachable and has a visible focus state.
- Dynamic state changes are announced through the existing live region.
- Text must remain inside the fixed canvas at 200% browser zoom; lens copy remains intentionally short.
- Disconnected transport disables state-changing commands and removes them from focus navigation.

| Item | Location | Why accepted | Owner / Exit |
| --- | --- | --- | --- |
| Physical optical-display validation pending | Whole HUD | Browser emulation cannot reproduce the additive lens and ambient light | Exit only after fresh MRBD on-device capture and Neural Band walkthrough |
