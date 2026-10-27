# Dashcam clips — design

_2026-10-27. Branch `v0.11.2`. Status: draft for builder review._

## Intent

A third export path on a route, beside **HUD Download** and the raw-file
**Download** dialog: plain camera footage for a chosen window, transcoded
on the device's hardware codec into an H.264 MP4 that any phone, chat app
or insurer portal will play, fetched from the phone over the LAN.

**Why.** Evidence after an accident. The user, parked and shaken, opens COD
on their phone, picks the moment, and within minutes has the untouched
footage from the cameras that saw it. The HUD export already covers the
annotated, "what openpilot was doing" version; this one is the dashcam.

This is upstream openpilot v0.11.2's "Generate dashcam clips from comma
connect" feature, rehosted on the local API. The device-side pieces are
reused as-is (`encoderd --clip`); the cloud transport is replaced by COD's
HTTP surface.

## Goals

- **Fast and dependable.** Hardware transcode and a two-minute cap, so a
  clip is ready in well under a minute. Works on the drive that just ended,
  right after the car stops.
- **Untouched footage.** No burned-in overlay or banner. Bitrate defaults
  high. The file is the camera's own frames, re-encoded once.
- **Survives.** Clips are never auto-reclaimed by COD, live outside the
  directories openpilot's deleter and COD's updater touch, and creating a
  clip preserves its source route from COD's cleanup.
- **Carries provenance.** Route, camera, wall-clock start, GPS position and
  openpilot version are written both into the MP4 and into a sidecar, so the
  file explains itself when it leaves the device.
- **Several cameras for one window.** Road and wide by default; driver on
  request.
- **Two ways in.** By hand from the route timeline selection, and from a
  bookmark with a default window around the tap.
- **A clips library.** Named clips that persist across sessions, listed
  newest-first, playable in the browser, downloadable or shareable, and
  deletable.

## Non-goals

- Clips longer than **two minutes** (upstream allows 30 min). A claim needs
  the moment, not the drive; the cap keeps every encode short and every file
  phone-sized. Longer footage is what the raw Download dialog is for.
- Speed-up / time-lapse clips (upstream offers 2x–10x; evidence doesn't want it).
- Any overlay, caption or timestamp burn-in. Use HUD Download for that.
- Interop with comma connect's own clip list (`realdata/clips/` and the
  `ai.comma.clip.settings` tag). COD stores clips elsewhere; the tag is still
  written for self-description but connect won't see COD's clips, nor vice
  versa.
- A software (ffmpeg) encoder on the device. ffmpeg is only for running the
  backend on a laptop against a local data dir.
- Automatic clip creation for every bookmark tap. The raw footage is already
  on disk; clips are made on demand.
- Audio. The cameras record none.
- Anything on the `dev` branch or the 0.11.1 release line.

## Anti-goals (would make this a failure even if it works)

- A clip that is silently missing frames, or spans a gap in the recording,
  without saying so. Better to refuse than to hand over evidence with a hole.
- Any effect on a moving car. Creating, encoding or serving clips must obey
  the onroad guard exactly like HUD renders and screencast.
- Losing a clip to cleanup, an update, or a reboot mid-encode.
- A phone that can't play the result. H.264 baseline/main in a plain MP4
  with `faststart`, nothing exotic.

## Constraints

- **0.11.2 only.** `encoderd --clip` exists only in the openpilot v0.11.2
  tree. The feature ships on the COD `v0.11.2` branch (VERSION `0.11.2`)
  and never on `dev`. COD's channel-aware updater already guarantees a
  0.11.1 device never pulls a 0.11.2 release.
- **Testing the hardware path needs the rebased catpilot on the device**
  (catpilot `rebase/v0.11.2`, Phase 10a–10c of the rebase plan). Until that
  lands, the queue, API and UI are developed and tested on a laptop with the
  ffmpeg path and a fake encoder in pytest.
- **VERSION and releases on the branch.** Bumping `VERSION` to `0.11.2` is
  task 0 of this work; until it is done, `make_release.sh` must not be run
  on the branch (a rolling release built at `0.11.1` would be offered to
  every 0.11.1 device). Device testing uses `deploy_dev.sh` (rsync) only.
  No release is cut from `v0.11.2` until the general 0.11.2 port has
  landed and the on-device checklist below has passed.
- **The encoder is a black box, with sharp edges** (verified against
  `clip_encoder.cc`, `video_writer.cc`, `common/util.h` at the tag):
  `encoderd --clip OUTPUT START DURATION [--bitrate BPS] [--speedup N]
  [--metadata JSON] -- SEG/<camera>.hevc ...` takes consecutive 60 s
  segment files, `START` relative to the first input, exits 0/1, prints
  nothing useful, and writes the MP4 progressively. It runs the Venus
  hardware decoder+encoder.
  - **SIGTERM does not stop it.** openpilot's `ExitHandler` only sets a flag
    the clip path never reads, so a "terminated" encode runs to completion.
    Stopping it means SIGKILL, then removing the temp output and the
    `.<name>.mp4.lock` that `VideoWriter` creates and would only unlink in
    its destructor.
  - **Output size is not progress.** For clips of 2 min or more with two or
    more inputs it encodes two shards in parallel, writes only shard 0 to
    the output while shard 1 spools to an unlinked temp file, appends it at
    the end, then rewrites the file for `faststart`. Size stalls near half,
    then jumps.
  - **A short final segment truncates silently** (exit 0). The window must
    be clamped to the last segment's real frame count, not `(maxqlog+1)×60`.
    A short *intermediate* segment (a gap) is refused with exit 1.
  - **`faststart` is only applied when `--metadata` is passed.** Always
    pass it.
  - **The support probe** must look for the usage line on stderr with exit
    code 2; a non-comma-hardware build logs a line and returns 0 for
    `--clip`.
  - HUD renders decode with ffmpeg on the CPU, so there is no hardware-codec
    conflict with a clip encode, only CPU contention.
- **Offroad only.** Upstream's `createClip` already refuses onroad; COD's
  rule is stricter (never affect a moving car), so the worker must also
  stop an encode in progress when `IsOnroad` flips.
- **Existing patterns to follow.** Background workers are asyncio tasks
  started in `server.py` (`screenshot_worker`, `_cleanup_worker`). Mutating
  endpoints are guarded by the regexes in `handlers/middleware.py`; heavy
  reads onroad are refused the same way. Captures already have a global page
  (`ScreenshotsPage.svelte`) and a list/serve/delete handler trio
  (`handlers/screenshots.py`); clips mirror that shape.
- **Upstream's deleter would eat `realdata/clips/`.** `deleter_step` rmtrees
  the earliest directory under `realdata` once free space drops under 5 GB
  or 10 %, and does not skip non-segment directories. That is why clips do
  not live there.

## Decided by the user

- Keep the upstream pair (`encoderd --clip` + the `VideoClips` queue
  semantics) as a **third export path**, not a replacement for HUD Download
  or raw Download.
- Plain dashcam clip, destined for a phone, for accident claims.
- New COD branch `v0.11.2`; implement clips only there; hardware encoder
  only on the device. `dev` / 0.11.1 untouched.
- Placement: clip creation beside HUD Download on the route page, reusing
  the timeline selection; a "clip this" shortcut on each bookmark; finished
  clips on a page of their own like Screenshots.
- **Maximum clip length: 2 minutes.**

## Design

Everything below is my call unless marked otherwise; the user can veto any
of it.

### Storage

- Clips live in **`/data/media/0/cod_clips/`** (configurable,
  `COD_CLIPS_DIR`). Same filesystem as `realdata` (plenty of space), outside
  `realdata` (upstream deleter), outside `/data/connect-on-device` (COD
  install/update dir and its capped caches).
- One MP4 plus one **sidecar JSON** per clip: `<name>.mp4` and
  `<name>.json`. The sidecar is the source of truth for listing, status and
  provenance; no central index file to corrupt. Listing is a `scandir`.
- An encode in progress writes to `.<name>.mp4` (dot-prefixed, ignored by
  listing) and is renamed into place atomically on success.
- COD's storage management **never reclaims this directory**. The clips
  page shows the total size; the user deletes by hand.
- The `--metadata` JSON passed to the encoder is the same provenance dict,
  so the MP4's `ai.comma.clip.settings` tag describes the file even after it
  leaves the device. (Builder: upstream's `_on_disk` requires a numeric
  `requested_at` in that dict; keep it so a stray connect listing doesn't
  choke.)

### Provenance (sidecar + MP4 tag)

```
filename, route (fullname "dongle/date"), local_id, camera ("road"|"wide"|"driver"),
start_sec, end_sec (route-relative seconds, as selected),
wall_start_epoch (GPS-referenced mono→wall, via log_parser; null if no fix),
gps {lat, lon} at start_sec (null if none), bitrate_mbps,
op_version, git_branch, git_commit, platform (from route metadata),
requested_at (epoch), status ("queued"|"encoding"|"ready"|"error"),
error (string|null), size (bytes, when ready), encode_sec (when ready)
```

### Queue and worker

- A `clip_worker` asyncio task started in `server.py`, same pattern as
  `screenshot_worker`. One encode at a time. The queue **is the set of
  sidecars with status `queued`**, ordered by `requested_at`; a restart
  re-discovers it, and any sidecar left in `encoding` is re-queued (its
  temp file removed). No in-memory-only state that a reboot loses.
- Offroad gate: the worker only starts an encode while offroad, polls
  `IsOnroad` about once a second during an encode, and on onroad SIGKILLs
  the encoder, deletes the temp file and its `.lock`, and sets the clip back
  to `queued`. It resumes when the car is parked again. This mirrors
  `screencast.py`.
- The worker also waits while a user HUD render is running (same rule
  `screenshot_worker` follows), so two CPU-heavy jobs never overlap.
- Free-space admission: **see "Admission rule" below.**
- Inputs: the consecutive `<camera>.hevc` segment files covering
  `[start_sec, end_sec)`, resolved through `route_store` (so a route that
  lives under a non-canonical local directory still works). If any segment
  file in the range is missing, the request is refused up front with a clear
  message (gap in recording / camera not recorded); the encoder's own gap
  refusal is the backstop and surfaces as `error`.
- Encoder selection: on the device, `OPENPILOT_DIR/openpilot/system/loggerd/encoderd`.
  Support is probed once at startup (binary exists and `--clip` with no
  arguments prints the usage line) and exposed as `clips_supported` in the
  dashboard/health payload; the UI hides the feature when false. On a PC
  (no encoderd), the upstream ffmpeg concat/libx264 path is used; it is
  **never** selected on the device.
- Progress: the encoder prints nothing and its output size is misleading
  (see constraints), so progress is **elapsed wall time plus an ETA** from
  an encode-rate constant (seconds of footage per second of wall time). The
  constant ships as a placeholder and is measured on the device during the
  checklist; the UI shows "encoding… 0:42 of ~1:10".
- Cancellation: `DELETE` on a queued clip removes the sidecar; on the
  encoding clip it SIGKILLs the encoder, removes temp, lock and sidecar.
- Always pass `--metadata` (it is what turns on `faststart`).

### Request shape

- Window: `start_sec`/`end_sec` route-relative, from the timeline
  selection (`selectionStart`/`selectionEnd`). Minimum 1 s, **maximum 2
  min** (user's cap; upstream allows 30). A longer selection is not
  silently cut: the dialog says the window is too long and offers to keep
  the first two minutes or the two minutes around the playhead. Clamped to
  the route's recorded length, where the
  last segment's length comes from its real frame count (ffprobe packet
  count or PyAV on `<camera>.hevc`, cached), not from `(maxqlog+1)×60`. If
  the clamp shortens the request, the response says so and the sidecar
  records the clamped `end_sec`.
- Bookmark shortcut: **30 s before and 30 s after** the tap, clamped.
- Cameras: a list; each camera becomes its own clip. Default checked:
  **road + wide**; driver unchecked. Driver is offered only if `dcamera.hevc`
  exists for the range.
- Bitrate: **5 / 8 / 12 Mbps**, default **8**. Estimated size shown before
  creating (`duration × bitrate / 8`): a full two-minute clip is 75 MB at
  5, 120 MB at 8, 180 MB at 12, per camera.
- Name: optional; default
  `<route date>_<camera>_<start>-<end>.mp4`, e.g.
  `2026-10-27--08-14-33_road_02m10s-02m50s.mp4`. Must be a bare filename;
  collisions get a numeric suffix.
- Creating a clip calls `store.preserve_route(local_id)` so COD's cleanup
  leaves the source route alone (the user can un-save it later as usual),
  and also sets the `user.preserve` xattr on the segment directories that
  cover the window, which is the only preservation openpilot's own deleter
  honours (its most-recent-five rule, so not a guarantee, but free).

### Admission rule (free disk space)

My call, made easy by the two-minute cap (worst case ≈ 540 MB for three
cameras at 12 Mbps): a request is **refused when free space minus the
estimate would fall below COD's 10 GB emergency line**, the point where COD
itself starts deleting saved routes and openpilot's deleter (5 GB) is
near. The rule is enforced twice: at `POST` (refuse with a message that
names free space and the estimate) and in the worker before each encode (a
clip that no longer fits goes to `error` with the same message). The
dialog shows free space next to the size estimate.

### API (documented in `API.md`)

```
POST   /v1/route/{routeName}/clips        {start_sec, end_sec, cameras[], bitrate_mbps, name?}
                                          → 202 {clips: [sidecar, ...]}   (one per camera)
GET    /v1/route/{routeName}/clips        → {clips: [...], cameras: {road: [[s,e],...], wide: ..., driver: ...}}
                                            (available ranges per camera, like upstream's getClipState)
GET    /v1/clips                          → {clips: [...newest first], total_bytes, supported}
GET    /v1/clips/{filename}               → the MP4 (FileResponse, Range supported,
                                            Content-Disposition attachment)
DELETE /v1/clips/{filename}               → cancel or delete
```

Onroad guard: `POST`/`DELETE` are mutating and forbidden onroad; `GET
/v1/clips/{filename}` is a heavy read and refused onroad like camera video.
Listing stays allowed.

### Frontend

- **Route page.** A **Clip** button beside HUD Download. It opens a small
  dialog: the selected window (read-only, from the timeline), camera
  checkboxes, quality, estimated size, optional name, Create. After Create,
  a toast links to the Clips page; the dialog closes. If the window is
  longer than 2 min or has no selection, the button explains instead of
  disabling silently.
- **Bookmark rows** (Notes tab) get a clip icon beside the existing
  frame-export icon; it opens the same dialog prefilled ±30 s.
- **Clips page** (`/clips`, nav entry beside Screenshots): newest first,
  each row with name, route link, camera, window, size, status. Queued and
  encoding rows show progress. Ready rows: Play (inline `<video>`),
  Download, Delete with confirm. No Share button: COD is served over plain
  HTTP and the Web Share API only exists in secure contexts, so it could
  never run. Header shows the count and total size, and a notice when
  `clips_supported` is false.
- Dashboard: nothing new beyond the `clips_supported` flag.

### Tests

- pytest: queue discovery and ordering from sidecars; restart re-queues
  `encoding`; onroad flip terminates and re-queues; gap/missing-camera
  refusal; filename collision; provenance fields; delete during encode.
  The encoder is a fake subprocess (a script that sleeps and writes bytes).
- Frontend vitest for the API wrappers, as for screenshots.

## Done criteria

- **Merge to `v0.11.2`:** the pytest and vitest suites above pass on a
  laptop with the fake encoder and the ffmpeg path.
- **First `v0.11.2` release (blocking, needs the rebased catpilot on the
  device):** encode-rate constant measured and committed; SIGKILL leaves no
  temp, lock or stray sidecar; onroad flip mid-encode re-queues and resumes;
  a 1928×1208 12 Mbps clip plays on an iPhone and an Android phone from the
  Clips page; a clip encodes correctly while `IsLiveStreaming` has
  `stream_encoderd` running (Venus shared); a request spanning a short
  final segment is clamped, not truncated.

## Out of scope but prerequisite

- The general 0.11.2 port of COD (notably `render_clip_headless.py`'s
  import of `tools.clip.run`, which moved to `openpilot.tools.clip.run` and
  now feeds `VISION_STREAM_NARROW_ROAD` plus a wide stream) is separate work
  on the same branch and is not part of this design.

## Left to the builder

- Exact module layout (suggested: `clips.py` for store + worker,
  `handlers/clips.py` for endpoints, `ClipsPage.svelte` + `ClipDialog.svelte`).
- How `wall_start_epoch` and the GPS point are looked up (reuse
  `log_parser` frame-time / coords helpers already used by screenshots).
- The poll interval and how the encode-rate constant is measured.
- Whether the Clips page also lists clips grouped per route; a flat
  newest-first list is the minimum.
