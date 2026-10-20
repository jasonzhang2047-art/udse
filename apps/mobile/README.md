# UDSE Futures — native client (Expo)

The React Native + Expo + TypeScript client recommended by spec §13. It talks to the FastAPI backend
in `src/udse/api/app.py` and follows `docs/design/ui-redesign.md`. The core screen is the Futures map.
The mobile-first web client in `src/udse/web/static` was the functional reference.

Expo SDK 58, Expo Router (file routes in `src/app`), `react-native-svg` for the map and charts, and
AsyncStorage for the session and the offline cache. Needs Node 22.13+ or 24.3+ (Expo SDK 58's range).

## Run it

```bash
# terminal 1, from the repo root: API on 127.0.0.1:8765, seeded with three demo decisions
uv sync && uv run udse serve

# terminal 2
cd apps/mobile
npm install
npx expo start --web        # or press i / a for the iOS simulator / Android emulator
npx tsc --noEmit            # type check
```

Pick a fixture participant on the sign-in screen. Tokens come from `fixtures/*/profiles.json` and are
for development only. Founder A owns the fundraising world, Person A the dinner world, and Garden member 1
the garden world. Only owners can rebuild models or explore futures.

### Where the app finds the API

| Target | Default | Notes |
|---|---|---|
| Web (`expo start --web`) | same origin | `metro.config.js` proxies `/v1/*` and `/health` to the backend in development, so plain `udse serve` needs no CORS. Change the target with `UDSE_API_URL=http://host:port npx expo start --web`. |
| iOS simulator | `http://127.0.0.1:8765` | |
| Android emulator | `http://10.0.2.2:8765` | |
| Phone on your network | set it on the sign-in screen | Use your computer's LAN address and start the backend with `uv run udse serve --host 0.0.0.0`. |

`EXPO_PUBLIC_UDSE_API_URL` overrides the default for every platform at build time. A web build served
from a different origin than the API needs the backend's opt-in CORS setting:
`uv run udse serve --cors-origin https://your.web.origin` (or `UDSE_CORS_ORIGINS=a,b`). CORS is off by
default.

## Screens

| Route | What it shows | API |
|---|---|---|
| `/login` | Dev user picker, theme, server address, clear saved results | `GET /v1/dev/users`, `/v1/me` |
| `/` Worlds | Decisions with a futures thumbnail and coverage counts | `GET /v1/decisions`, `/futures?light=true` |
| `/world/[id]` | Header with owner jobs: **Rebuild model** and **Explore futures**. These are async jobs polled through `/v1/jobs/{id}`. | `POST …/models/compile`, `POST …/experiments`, `POST /v1/experiments/{id}/runs` |
| · Futures | Branching futures map, vertical below 640pt. Structure and metric lenses, a time cursor with readout, and nexus, moment and ghost sheets. Also the path list, a thread story (bottom sheet below 1000pt), compare mode, and every run as a trajectory card. | `/futures`, `/runs`, `/v1/runs/{a}/compare/{b}`, `/why/{seq}`, `/promotion-check` |
| · Evidence | Gaps that matter, facts per entity with freshness bars, conflicts, research needs, refused sources | `/evidence`, `/world-model` |
| · World model | Inputs, processes and outcomes, with the dependency path of a tapped outcome. Also per-metric fidelity tiers, unknowns and omissions, the scorecard, downgrades, and reuse/transfer checks. | `/world-model`, `/models`, `/models/{v}` |
| · Possibilities | Coverage frontier by status, linked to paths on the map | `/coverage` |
| · Setup | Options, facts, hard limits, scope | `/v1/decisions/{id}` |
| `/run/[id]` | Timeline explorer: step chart of committed points only, grayed after a run stops early. Also an event scrubber, state after each event, filters, and an event sheet with Why and the promotion check. Plus your reaction and compare-with. | `/timeline`, `/state?seq=`, `/why/{seq}`, `/events/{seq}/promotion-check` |
| `/compare/[a]/[b]` | Divergence: changed on purpose, shared prefix, first difference, end metrics | `/v1/runs/{a}/compare/{b}` |
| `/agent` | Your Agent: suggested updates (approve or reject), preferences, boundaries, history with rollback | `/v1/profiles/{id}`, `…/approvals`, `…/rollback` |
| `/integrity` | Boundary log of refused crossings by zone | `GET /v1/audit?denied_only=true` |
| `/new` | Ask a new question: create, build, explore | `POST /v1/decisions` + the jobs above |

## UX rules kept from the spec

- **Simulated values are labelled as simulated.** Each path, moment, state panel, chart readout and
  comparison carries a `SIMULATED` stamp, or sits under a banner saying so. Agent reasoning is marked as
  the Agent's, not the person's.
- **Tiers describe evidence support, not accuracy.** The four-step meters and their words never show a
  percentage, never average, and every screen that uses them says so.
- **Run counts are never odds.** Counts are labelled as coverage. Nothing ranks paths by frequency.
- **Large text.** All text scales with the OS font size (Dynamic Type, Android font scale, browser zoom).
  Containers grow with their text, and buttons are 34–44pt tall.
- **Reduced motion.** The OS setting (`prefers-reduced-motion` on the web) turns off the map fade-in,
  sheet slides and screen transitions.
- **Accessibility.** The map is an image with a text summary. The path list below it is the full
  accessible equivalent. The scrubbers are `adjustable` and also have step buttons.

## Offline

Every successful GET is saved on the device: the 40 most recent responses, per server and participant.
If the server can't be reached, screens show the saved copy under an "Offline — showing results saved on
this device" banner, and actions are disabled. Job status and promotion checks are never cached. Clear
saved results from the sign-in screen.

## Not done / known limits

- Verified with `expo start --web` against a local backend, at phone (375pt) and desktop widths, in both
  themes. Not yet run on an iOS or Android device or simulator.
- Production sign-in, push notifications and guest links are out of scope, as they are for the backend.
- Expo's web output is a single-page app. Deep links work under `expo start`. A static host needs a
  fallback to `index.html`.
