---
name: Cinematic Obsidian
version: alpha
description: Visual identity and design system tokens for Aarkib - dark minimalism, atmospheric glassmorphism, and electric accents for high-fidelity media curation and streaming.
colors:
  surface: '#10131a'
  surface-dim: '#10131a'
  surface-bright: '#363940'
  surface-container-lowest: '#0b0e14'
  surface-container-low: '#191c22'
  surface-container: '#1d2026'
  surface-container-high: '#272a31'
  surface-container-highest: '#32353c'
  on-surface: '#e1e2eb'
  on-surface-variant: '#bbc9cf'
  inverse-surface: '#e1e2eb'
  inverse-on-surface: '#2e3037'
  outline: '#859399'
  outline-variant: '#3c494e'
  surface-tint: '#47d6ff'
  primary: '#a5e7ff'
  on-primary: '#003543'
  primary-container: '#00d2ff'
  on-primary-container: '#00566a'
  inverse-primary: '#00677f'
  secondary: '#c0c1ff'
  on-secondary: '#1000a9'
  secondary-container: '#3131c0'
  on-secondary-container: '#b0b2ff'
  tertiary: '#a3e8ff'
  on-tertiary: '#003642'
  tertiary-container: '#45d1f6'
  on-tertiary-container: '#005769'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#b6ebff'
  primary-fixed-dim: '#47d6ff'
  on-primary-fixed: '#001f28'
  on-primary-fixed-variant: '#004e60'
  secondary-fixed: '#e1e0ff'
  secondary-fixed-dim: '#c0c1ff'
  on-secondary-fixed: '#07006c'
  on-secondary-fixed-variant: '#2f2ebe'
  tertiary-fixed: '#b3ebff'
  tertiary-fixed-dim: '#4cd6fb'
  on-tertiary-fixed: '#001f27'
  on-tertiary-fixed-variant: '#004e5f'
  background: '#10131a'
  on-background: '#e1e2eb'
  surface-variant: '#32353c'
typography:
  display-hero:
    fontFamily: Plus Jakarta Sans
    fontSize: 56px
    fontWeight: '800'
    lineHeight: 64px
    letterSpacing: -0.03em
  display-hero-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 32px
    fontWeight: '800'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-xl:
    fontFamily: Plus Jakarta Sans
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 44px
    letterSpacing: -0.02em
  headline-xl-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 26px
    fontWeight: '700'
    lineHeight: 34px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 28px
    fontWeight: '700'
    lineHeight: 36px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: 0em
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 26px
    letterSpacing: -0.005em
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 22px
    letterSpacing: 0em
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: 0.005em
  label-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: 0.01em
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.02em
  label-badge:
    fontFamily: Inter
    fontSize: 10px
    fontWeight: '700'
    lineHeight: 12px
    letterSpacing: 0.08em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1.5rem
  gutter-mobile: 0.75rem
  margin: 3rem
  margin-mobile: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.5rem
components:
  button-primary:
    backgroundColor: "{colors.primary-container}"
    textColor: "{colors.surface-container-lowest}"
    rounded: "{rounded.full}"
    padding: 0.75rem 1.5rem
  button-secondary:
    backgroundColor: "rgba(255, 255, 255, 0.08)"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.md}"
    padding: 0.5rem 1rem
  button-icon:
    backgroundColor: "rgba(255, 255, 255, 0.05)"
    textColor: "{colors.outline}"
    rounded: "{rounded.full}"
    size: 40px
  card-media:
    backgroundColor: "{colors.surface-container-low}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.xl}"
  badge-technical:
    backgroundColor: "{colors.surface-container-lowest}"
    textColor: "{colors.on-surface-variant}"
    rounded: "{rounded.sm}"
    typography: "{typography.label-badge}"
  badge-status:
    backgroundColor: "{colors.secondary-container}"
    textColor: "{colors.on-secondary-container}"
    rounded: "{rounded.sm}"
    typography: "{typography.label-badge}"
  input-search:
    backgroundColor: "{colors.surface-container}"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.md}"
    padding: 0.75rem 1rem
  checkbox:
    backgroundColor: "{colors.surface-container-low}"
    textColor: "{colors.primary-container}"
    rounded: "{rounded.sm}"
    size: 20px
  scrubber-timeline:
    backgroundColor: "rgba(255, 255, 255, 0.2)"
    textColor: "{colors.primary-container}"
    height: 4px
  stats-overlay:
    backgroundColor: "rgba(11, 14, 20, 0.85)"
    textColor: "{colors.on-surface}"
    rounded: "{rounded.md}"
    padding: 1rem
---

# 🎨 Cinematic Obsidian Design System

## Brand & Style

This design system serves a modern home media server platform engineered for high-fidelity personal streaming, library curation, and home theater control. The core emotional tone is cinematic, immersive, and premium—evoking the sensory presence of a darkened screening room paired with precision engineering.

The visual style blends **Dark Minimalism** with **Atmospheric Glassmorphism**:
- **Immersion First:** The interface recedes into an obsidian background, allowing cover art, video backdrops, and dynamic metadata to lead the experience.
- **Electric Accents:** Luminescent cyan `{colors.primary-container}` and rich indigo `{colors.secondary}` provide instant focal clarity for playback controls, active focus states, and discovery carousels without visual fatigue.
- **Glass & Depth:** Floating translucent bars and ambient radial highlights simulate hardware-level luminescence and tactile elevation.
- **Refined Media Utility:** Metadata badges, stream bitrate monitors, and audio codecs are presented as sharp, high-contrast badges resembling studio equipment indicators.

## Colors

The palette is anchored in an ultra-deep charcoal-obsidian continuum engineered specifically for OLED displays and ambient-lit home theater setups:

- **Canvas & Base (`#0B0E14` / `{colors.surface-container-lowest}`):** Canvas bedrock. Reduces light spill in darkened rooms while keeping true black clipping to a minimum.
- **Elevated Canvas (`#121721` / `{colors.surface-container-low}`):** Structural panels, side navigation rails, and backdrop scrims.
- **Surfaces (`#181F2E`, `#1F293D` / `{colors.surface-container}`, `{colors.surface-container-high}`):** Media cards, overlay sheets, dialog boxes, and drawer panels.
- **Primary Accent (`#00D2FF` / `{colors.primary-container}`):** Electric Cyan, used for playback progress tracks, primary interactive triggers, active tabs, and focus halos.
- **Secondary Accent (`#6366F1` / `{colors.secondary}`):** Vibrant Violet/Indigo, used for secondary action badges, user library profile accents, and multi-user room indicators.
- **Tertiary Accent (`#00B4D8` / `{colors.tertiary}`):** Deep Cerulean, supporting subtle hover fills, scrub bar buffers, and volume slider fills.
- **Typography & Details:** High-contrast crisp white (`#F8FAFC` / `{colors.on-surface}`) for primary titles, cool silver (`#94A3B8` / `{colors.on-surface-variant}`) for secondary cast and technical metadata, and muted slate (`#475569` / `{colors.outline}`) for subtle metadata dividers.
- **Functional Badges:** Crisp solid dark charcoal bases (`#0F172A` / `{colors.surface-container-lowest}`) framed with semi-translucent borders for formats like 4K HDR, Dolby Vision, Dolby Atmos, and HEVC.

## Typography

The typography hierarchy pairs the structural geometry of **Plus Jakarta Sans** with the neutral clarity of **Inter**:

- **Display & Headlines:** Plus Jakarta Sans provides crisp geometric weight for hero movie titles, carousel section titles, and modal headers. Negative tracking tightens large scale headings for a poster-like presentation.
- **Body & Metadata:** Inter guarantees high legibility across variable viewing distances (from desktop monitors to living-room 10-foot television UIs). Synopsis copy uses relaxed line heights (`1.6x`) against the dark background to prevent reading strain.
- **Codec & Spec Badges:** The `label-badge` token employs bold, tracked uppercase typography (`letterSpacing: 0.08em`) to mimic physical AV equipment labels.

## Layout & Spacing

The layout is built on a responsive fluid grid system optimized for browsing density, aspect ratios (2:3 poster art and 16:9 episodic backdrops), and edge-to-edge media viewing:

- **Desktop (12 Columns):** Margins of `3rem` (48px) with `1.5rem` (24px) gutters. Content expands up to a maximum container width of `1920px`, after which outer margins scale fluidly.
- **Tablet (8 Columns):** Margins of `2rem` (32px) with `1rem` (16px) gutters. Horizontal carousels scroll seamlessly off-canvas with preserved left alignment.
- **Mobile (4 Columns):** Margins of `1rem` (16px) with `0.75rem` (12px) gutters. Poster grids collapse to 2 columns, and episodic feeds prioritize full-width 16:9 cards with bottom sheet drawer controls.
- **Rhythm Rules:** Vertical spacing between content carousels strictly uses `space-xl` (`2.5rem`) to maintain separation between distinct streaming hubs, while item padding within cards utilizes `space-sm` and `space-md`.

## Elevation & Depth

Visual depth combines physical surface layers with translucent glassmorphism and radiant colored halos:

1. **Layer 0 (Canvas Base):** Solid `#0B0E14` (`{colors.surface-container-lowest}`). Serves as the backdrop for all full-bleed image scrims and ambient lighting effects.
2. **Layer 1 (Card & Drawer Base):** `#181F2E` with a 1px inner hairline stroke of `rgba(255, 255, 255, 0.06)`. Shadows are soft and tinted: `0 8px 32px -4px rgba(0, 0, 0, 0.6)`.
3. **Layer 2 (Elevated & Active Cards):** `#1F293D` accompanied by an electric cyan glow on hover or TV remote focus: `0 12px 36px -2px rgba(0, 210, 255, 0.15)`.
4. **Glassmorphism (Top Navigation & Player Chrome):** Translucent background `rgba(18, 23, 33, 0.65)` layered with `backdrop-filter: blur(20px) saturate(180%)` and an anchored bottom border of `1px solid rgba(255, 255, 255, 0.08)`.
5. **Backdrop Ambient Scrims:** Full-bleed hero banner images use a dynamic gradient mask transitioning from `transparent 40%` down to `rgba(11, 14, 20, 0.95) 90%` and `#0B0E14 100%`.

## Shapes

The design system employs refined, modern curves with a base rounding of `0.5rem` (8px), scaling upward for media presentation:

- **Buttons, Inputs, & Action Chips:** Built with `0.5rem` (8px / `{rounded.DEFAULT}`) or `0.75rem` (12px / `{rounded.md}`) to match standard tactile surface expectations.
- **Media Poster & Episode Cards:** Apply `rounded-xl` (`1rem` / 16px / `{rounded.lg}`) to maintain a soft, modern screen feel without cropping title art or progress bars.
- **Hero Containers, Floating Modals, & Detail Sheets:** Use `rounded-2xl` (`1.5rem` / 24px / `{rounded.xl}`) for expansive framing.
- **Pill Elements:** Badge pills, playback scrub heads, and streaming quality tags preserve full circular capsules (`{rounded.full}`).

## Components

### Buttons
- **Primary Play Action:** Large pill or rounded-xl button with Electric Cyan background (`#00D2FF` / `{colors.primary-container}`), dark obsidian text (`#0B0E14` / `{colors.surface-container-lowest}`), bold weight (`label-lg`), and an ambient drop shadow (`0 0 24px rgba(0, 210, 255, 0.35)`). Active state applies a scale transform (`scale(0.97)`).
- **Secondary Actions (Queue, Trailer):** Glass-panel button (`rgba(255, 255, 255, 0.08)`) with white text, crisp 1px border (`rgba(255, 255, 255, 0.12)`), and smooth transition to `#1F293D` on hover.
- **Icon Actions (Like, Bookmark, Cast):** Circular buttons (`40px x 40px`) rendered in semi-transparent dark charcoal with silver icons (`#94A3B8`) shifting to cyan on toggle.

### Media Cards (Poster & 16:9 Backdrops)
- Constructed with `rounded-xl` (`{rounded.lg}`) borders and overflow hidden.
- Includes a subtle 1px border (`rgba(255, 255, 255, 0.06)`).
- On hover/focus: Scales up by `1.04`, elevates border to `#00D2FF` (`{colors.primary-container}`), and triggers a bottom overlay showing metadata, audio format icons, and a quick-play button.
- Embedded progress bar anchored at the bottom edge: 3px thick, `#00D2FF` filled track against a `rgba(255, 255, 255, 0.2)` trough.

### Badges & Technical Indicators
- Small, crisp tags displaying format specifications: `4K HDR`, `DOLBY VISION`, `ATMOS`, `HEVC 10-BIT`.
- Rendered using `label-badge` font specs with solid `#0F172A` background, `rgba(255, 255, 255, 0.15)` border, and high-contrast silver/white typography.
- Status badges (e.g., `DIRECT PLAY`, `TRANSCODING`) use secondary violet `#6366F1` (`{colors.secondary}`) or warning amber accents.

### Input Fields & Search Bars
- Glass-morphic inputs with `#181F2E` fills and subtle inner border (`rgba(255, 255, 255, 0.08)`).
- Focus state reveals a luminous cyan outline glow (`0 0 0 2px #00D2FF`).
- Integrated keyboard shortcuts indicator (e.g., `⌘K`) displayed in muted slate pill.

### Lists & Episode Rows
- Alternating subtle hover states (`rgba(255, 255, 255, 0.03)`).
- Left-aligned episode thumbnail (16:9 with duration badge overlay), middle column displaying episode number, title, and overview, and right-aligned action menu (watched status, download, overflow).

### Checkboxes & Radio Buttons
- Obsidian base (`#121721`) with 1px border (`rgba(255, 255, 255, 0.2)`).
- Checked state transitions directly to `#00D2FF` (`{colors.primary-container}`) with sharp white SVG checkmark/dot and electric glow.

### Additional Media-Specific Components
- **Scrubber Timeline:** Custom video scrubber featuring active buffer range (`#00B4D8` / `{colors.tertiary}`), elapsed playback (`#00D2FF` / `{colors.primary-container}`), interactive scrub thumbnail popup, and chapter markers.
- **Transcode / Stream Stats Overlay:** Monospaced and badge-driven HUD displaying frame drop rate, video bitrate (Mbps), audio channels (7.1/5.1), and hardware transcoding engine stats.

## Do's and Don'ts

### Do's
- **DO** preserve dark obsidian immersion: Always maintain the charcoal/obsidian background continuum (`#0B0E14` to `#1D2026`) to protect viewing comfort in low-light home theater environments.
- **DO** use Electric Cyan (`#00D2FF`) exclusively for primary actions, active focus highlights, and playback progress indicators.
- **DO** use tracked uppercase styling (`label-badge`) for technical indicators, audio codecs, and media badges.
- **DO** keep cards softly rounded with `{rounded.lg}` (`1rem`) to create modern, polished media presentation without cutting into cover art.
- **DO** use glassmorphic blur (`backdrop-filter: blur(20px)`) with subtle hairlines for floating bars and modal headers.

### Don'ts
- **DON'T** introduce raw pure white backgrounds (`#FFFFFF`) on UI surfaces or containers.
- **DON'T** use harsh, unrounded corners (`border-radius: 0px`) for media posters or interactive buttons.
- **DON'T** use high-saturation red or yellow for ordinary primary navigation or button triggers.
- **DON'T** clutter poster art with heavy solid banners; prefer bottom scrim overlays and subtle corner badges.
- **DON'T** place high-contrast white text directly on raw image backdrops without a gradient scrim.
