# Guide Dog — Final Plan

## Summary

Four agents, three speeds — this is the pitch in one sentence: **the dog reacts like a
reflex, checks like an instinct, and plans like it's thinking**, and it will refuse an unsafe
instruction the same way a real trained guide dog does. That last part — intelligent
disobedience — is our differentiator; nobody else's nav-robot will have thought to build it.

- **Orchestrator** (Claude Code via MCP) — plans the route, deliberative, slowest.
- **Driver** — NaVILA (vision → 1 of 7 discrete actions) + translates to `Move(vx,vy,vyaw)`.
- **Safety Watchdog** — reactive, ~20Hz, no LLM in the loop, watches (mocked) harness force,
  can emergency-stop directly, bypassing everyone else.
- **Hazard Veto Agent** — tactical, ~1Hz, one Claude vision call per step, can refuse an
  instruction ("red signal," "person crossing") with a stated reason.

Fault injection is our test method: a `ScenarioInjector` for automated testing,
and something visibly real (in-scene object or a physical prop) for the live demo trigger, so
there's never ambiguity about what judges are watching.

Explicitly cut this week: real hardware, `TrajectoryFollow`, multi-axis force sensing, route
memory, TTS. These are roadmap lines in the pitch, not code. Voice input and caregiver alerts
are also not happening this week either — later, not now, not even as a stretch goal.

## Architecture

```mermaid
flowchart TB
    subgraph Human["Human Interface (future)"]
        User["Blind User"]
    end

    subgraph Deliberative["Deliberative — slowest"]
        Orch["Agent 1: Orchestrator<br/>Claude Code via MCP<br/>plans route, issues ONE step,<br/>waits for result"]
    end

    subgraph Tactical["Tactical — ~1Hz — THE DIFFERENTIATOR"]
        Veto["Agent 4: Hazard Veto<br/>Claude vision call<br/>VETO / CLEAR + reason"]
    end

    subgraph DriverLayer["Driver"]
        NaVILA["NaVILA VLM<br/>8 JPEG frames → discrete action"]
        Translate["Action → Move(vx,vy,vyaw)"]
    end

    subgraph Reactive["Reactive — 20Hz — zero LLM calls"]
        Safety["Agent 3: Safety Watchdog<br/>harness force, debounce + threshold only"]
    end

    subgraph Backend["Backend (swappable)"]
        Mock["MockBackend — this week"]
        Real["UnitreeBackend — stretch goal"]
    end

    subgraph Test["Test Harness (disclosed openly)"]
        Inject["ScenarioInjector<br/>fake hazard frames + fake force events"]
    end

    User --> Orch
    Orch -->|one instruction| NaVILA
    NaVILA --> Translate
    NaVILA -->|current frame| Veto
    Inject -.->|injected hazard frame| Veto
    Veto -->|CLEAR| Translate
    Veto -.->|VETO + reason| Orch
    Translate -->|Move| Mock
    Translate -.->|later| Real
    Inject -.->|fake force events| Safety
    Safety ==>|emergency_stop direct, bypasses everyone| Mock
    Safety -->|alert| Orch
```

## Build order (priority-ordered, not strict daily deadlines — school comes first)

| Stage | Goal | Why this order | Status |
|---|---|---|---|
| 1 | Per-step MCP tools + mock backend | Everything else plugs into this seam. | **Done + merged.** (C) |
| 2 | End-to-end loop on mocks + real Safety Watchdog | Proves the pipeline works before the hard part. | **Done + merged.** (A built the watchdog; C wired it into `navila_navigate_step` + `navila_inject_force_drop` / `navila_get_logbook` / `navila_clear_*`; verified headless, 23/23.) |
| C1 | Per-step loop visible in the OrcaLab GUI | Needed to demo anything; also de-risks the `mjlab` backend. | **Done + merged.** (C — `OrcaLabMirrorBackend`, root-pose mirror; verified live: dog glides, freezes on trip, resumes. Legs don't articulate yet.) |
| C2 | Real gait + real ego-camera frames in the per-step loop | Articulated walking for the demo; frames are the Veto Agent's input. | Not started. **Split** — real-gait physics (needs GPU — D has one now) → **D**; real camera-capture-only fallback (no GPU needed) → **C**. See Task delegation. |
| 3 | Hazard Veto Agent gated into the loop | The differentiator. Protect the most time here. | **Done, uncommitted** (**C** — gate wired into `navigate_step` via a stub client on placeholder frames + `WAYPOINT_STOP_OVERRIDE` precedence flag; verified live against the OrcaLab GUI, see `docs/STAGE3_TESTING.md`). Swap to real frames when C2 lands. |
| 4 | Integration + demo rehearsal | No new features — fixing and rehearsing the trigger. | Not started. D's fork is merged (no longer blocking), but the demo is still blocked on C2 (split D + C), the in-scene hazard trigger + scene reset reliability (C), the `NavigationRunner` safety gap (A), and the missing `hackathon_assets.zip` USD payloads (D). |

## Task delegation

Done so far: **A** — all four safety/veto components (`robot_backend/`, `safety_watchdog.py`,
`veto/`, `decision_logbook.py`), 59/59 tests. **C** — Stage 1 (per-step MCP tools), Stage 2
(watchdog + logbook + force injection wired into the loop), C1 (OrcaLab GUI pose mirror), Stage
3 (veto gate + `WAYPOINT_STOP_OVERRIDE` precedence flag — **done but not yet committed**, see
below). **D** — the working fork (traffic-crossing state machine, `--rehearsal` /
`--realtime-visual-sync` / `--traffic-light-crossing` CLI flags, `WAYPOINT_STOP_OVERRIDE`
runner reflex) merged into `main`; the "Does OrcaLab simulate physics under Play" question
answered (no). Stage 1/2/C1/A's components merged to `main`. `CLAUDE.md` stays at repo root
(Claude Code auto-loads it there); other docs live in `docs/`.

**Redelegated this session, twice.** First pass moved everything off D on the assumption her
machine was CPU-only. **Correction: D does have GPU access** (see `CLAUDE.md`'s "Known
technical facts") — the earlier `--device cpu` in her handover command reflected a
constraint at the time, not a permanent limit. Second pass rebalances for as even a 3-way
split as the actual work allows, given each person's real context:
- **C2 split in two**, since it's the single biggest remaining item: the GPU-dependent
  real-gait physics half → **D** (she has the GPU now, and already wrote the `cli.py` render
  assembly this needs to mirror inside `bridge_backends.py` — most-informed owner). The
  GPU-independent real-camera-capture-only fallback half (unblocks real frames for the veto
  gate without needing gait at all) → **C**, who already owns the edit-service connection code
  it plugs into.
- **In-scene hazard trigger + scene reset reliability stay with C** (unchanged from the first
  pass) — same reasoning, plain `EditServiceWrapper` calls into code C already owns.
- **`NavigationRunner` safety-gap / precedence porting → A**, who owns `SafetyWatchdog` and
  `HazardVetoAgent` already — wiring them into a second loop is a natural extension of A's own
  components, not a new subsystem for anyone else to learn.
- Scripted human proxy stays dropped (see "Cut" below D's list) — not reassigned, genuinely not
  needed.
- D also keeps the one thing nobody else can do: exporting the missing asset payloads from her
  own OrcaStudio project.

---

**C — orchestration bridge & final wiring** (has Claude Code; owns `navila_bridge.py` +
`bridge_backends.py`)

1. ~~**Stage 3 — veto gate.**~~ **Done, uncommitted.** `_build_veto_stack` wires
   `HazardVetoAgent` + `ScenarioInjector` into every episode; `navigate_step` gates every
   non-stop decision through `HazardVetoAgent.assess(frame, instruction, action_text)` right
   before the motion chunk, and a VETO ends the step with `termination_reason="veto"` and zero
   physics, recorded via `logbook.record_veto_decision`. Shipped: `NAVILA_BRIDGE_VETO` env
   toggle, `veto`/`veto_client_kind` params on `navila_start_episode`, and
   `navila_inject_hazard`/`navila_clear_hazards` tools mirroring the force-drop ones (hazard
   `at_step` counts *decisions*, not physics ticks). Runs today against placeholder frames +
   a self-contained pixel-detection stub client (`_RedBarStubVetoClient`, no API key needed);
   swapping in `AnthropicVetoVisionClient` is a one-line change to `_make_veto_vision_client`
   once C2 lands. Verified: 7 new tests + full live-GUI pass, see `docs/STAGE3_TESTING.md`.
2. ~~**`WAYPOINT_STOP_OVERRIDE` precedence.**~~ **Done, uncommitted.**
   `session.stop_override_suppressed` — reset `False` every `navigate_step` call, set `True` by
   a watchdog trip or a veto, explicitly *not* set by an ordinary VLM-issued stop. **Scoped as
   inert on purpose**: D's actual override reflex lives in `NaVILA-Orca/src/navila_orca/
   runner.py`'s `NavigationRunner` (the CLI-driven loop, not this per-step bridge) and that
   loop has zero watchdog/veto wiring of its own — this flag is a ready-made suppression check
   for whenever the reflex gets ported into this loop, not a fix to `runner.py`'s live-demo
   safety gap. That gap is still open (see below) and out of scope unless asked. 4 new tests.
3. **C2 — real camera-capture-only fallback (your half of the split, see "Redelegated" above).**
   Add a `bridge_backends` path that wires `OrcaLabRenderBridge.capture` (or
   `EditServiceWrapper.get_camera_png`) into the `orcalab-mock` inner backend's `StepBackend`,
   so `navila_navigate_step` hands the VLM/veto gate a real RGB frame instead of
   `placeholder_frame()`. No GPU/MJLab needed — this is exactly the fallback PLAN.md already
   called out ("unblocks Stage 3 with real frames even before real gait"), now formally your
   half of C2 rather than a fallback for D's half to fall back to.
4. **C2 seam check (D's half).** When D delivers the real-gait `orcalab-render`
   `StepBackend`, confirm `_PerStepSession` / `_build_safety_stack` take it unchanged (same
   `StepBackend` interface — they should) and that the watchdog still attaches. Blocked on D;
   nothing to do yet.
5. **In-scene hazard trigger.** Script spawn/move of a judge-facing
   hazard — traffic-light state, `blue_hatchback_car_1` crossing, a pedestrian stepping out —
   via `EditServiceWrapper` (`add_actor_batch` / `set_actor_transform_batch`, the same wrapper
   `OrcaLabMirrorBackend` already connects to). Expose it as an MCP tool (distinct name from
   `navila_inject_hazard`, which is the `ScenarioInjector` test-harness path, not this one) or
   a standalone script to fire during the demo. This is the "visibly real, not composited"
   trigger.
6. **Scene reset reliability.** Repeatable "reset to authored layout" for
   rehearsal + judges — `EditServiceWrapper.save_state`/`restore_state`, or transform
   write-back to the layout's original pose (the same mechanism already used to fix the
   Go2 pose-reset-between-runs bug). Confirmed genuinely unbuilt — zero
   `save_state`/`restore_state` usage anywhere in the repo yet.

**D — OrcaLab-side code & scene** (has the **Hermes AI** coding assistant; owns everything
inside the sim + the OrcaLab-facing backend code)

1. **`hackathon_assets.zip`'s scene is visually incomplete — needs D's action.** The user
   unzipped it and loaded `street.json` into a live OrcaLab GUI; most actors render as
   missing/placeholder. Root cause: `street.json` is only the scene graph (transforms +
   `asset_path` references into OrcaStudio's managed asset service) — it does not embed the
   actual USD geometry/texture payloads, and the zip only bundles 3 preview `.apng` images
   (explicitly *not* substitutes, per its own `ASSET_MANIFEST.md`) against 20 actual
   `asset_path` references in the scene. The Go2 itself (`unitree_robots/prefabs/go2_usda`) is
   almost certainly fine — that's the prefab pack bundled with every install of this fork — but
   the 2 `simplifynext_hackathon/prefabs/...` assets (asphalt road, traffic light) are private
   to D's own OrcaStudio project and were never exported as portable payloads; ~14 more
   (`default_project/prefabs/a_<hash>_usda`, `remy`, `vln_presentation/...`) are unconfirmed —
   could be shared/stock content or also D-private. **No code fix possible** — D needs to
   export the actual USD payloads or grant asset-project sync access.
2. **C2 — real gait (your half of the split, see "Redelegated" above; top priority).** Add a
   `bridge_backends` kind `orcalab-render`: a `StepBackend` that runs `MjlabGo2Backend` for
   physics and drives `OrcaLabRenderBridge.push_state(state, backend.qpos_batch)` each step for
   articulated pose in the GUI. Model the assembly on `cli.py::_make_renderer` / `_run` — the
   exact pattern already in your own fork's CLI path, so this is largely porting/adapting code
   you've already written into `bridge_backends.py`'s `StepBackend` shape rather than building
   from scratch. Needs `go2_flat.pt` + your GPU. C owns the parallel camera-capture-only half
   (see C's list) and the seam check once this lands.
3. ~~Does OrcaLab simulate its own Go2 physics under "Play"?~~ **Answered: no.** Confirmed by
   the user — no second physics authority fighting C1's transform mirror, no scene change
   needed. See CLAUDE.md's "Known technical facts."
4. ~~**The fork gap.**~~ **Done.** D's actual working fork (branch `daphne-demo-ready`) was
   merged into `main` this session — `--realtime-visual-sync`, `--rehearsal`,
   `--traffic-light-crossing`, `--traffic-*-waypoint`, and the `WAYPOINT_STOP_OVERRIDE` runner
   reflex are all in-repo now (`NaVILA-Orca/src/navila_orca/{cli,runner,traffic_crossing}.py`).
   One thing the merge had to fix by hand: D's branch predated A's Stage-2 safety commits and
   had silently dropped `MjlabGo2Backend.interrupted`/`.emergency_stop()` — restored, since
   `bridge_backends.py`'s C2 seam and `SafetyWatchdog` both depend on it. The reflex still
   doesn't honour C's suppression flag (C's item 2 above) — porting it in is A's item 3 above —
   that wiring is open, not done by the merge.

**Cut, not reassigned:** scripted human proxy (was D Stage 2) — the scene already has static
pedestrians for the veto agent to react to, so this isn't needed for the differentiator to
work. Drop it rather than give it to anyone; revisit only if there's spare time later.

**A — safety/veto components & GPU** (has Claude Code + the GPU box)

1. **`anthropic` dependency.** Add it to `NaVILA-Orca/pyproject.toml`; smoke-test
   `AnthropicVetoVisionClient` on one real saved OrcaLab frame + an API key.
2. **np.ndarray frames.** Make `HazardVetoAgent.assess` / `ScenarioInjector.inject` accept a
   raw `np.ndarray` (the per-step loop's frame type), not just PIL — a small shim in `veto/`,
   removes a conversion burden from C. Not blocking anymore — C's veto gate wiring added its
   own local `_veto_frame()` conversion in `navila_bridge.py` rather than wait on this, but the
   shim is still worth doing for cleanliness/reuse elsewhere.
3. **`NavigationRunner` safety-gap / precedence porting (your share of the split, see
   "Redelegated" above).** D's live demo command runs through `NavigationRunner` (the CLI
   loop, not the per-step MCP bridge), which today has zero `SafetyWatchdog`/`HazardVetoAgent`
   wiring — so the `WAYPOINT_STOP_OVERRIDE` forward-nudge reflex there has nothing stopping it
   from overriding a real safety stop. Port your own components into that loop (mirroring how
   C already wired them into `navila_bridge.py`'s `navigate_step` this session — see
   `docs/STAGE3_TESTING.md` for the pattern) and gate the reflex on a trip/veto having occurred,
   the same way `session.stop_override_suppressed` is meant to. You own `SafetyWatchdog` and
   `HazardVetoAgent` already, so this is closing a gap in your own components' coverage, not
   learning a new subsystem.
4. **Tuning.** Once C2 gives a real loop, set `SafeForceBand` / `debounce_ticks` and the veto
   cadence against measured per-decision NaVILA latency (replace the guessed 120s `vlm_timeout`).

## Stage 4 (everyone)

Integration pass, rehearse the demo trigger until it's reliable on repeat runs, and settle the
pitch narrative — what's built vs. explicitly scoped as roadmap. No new features this stage.
