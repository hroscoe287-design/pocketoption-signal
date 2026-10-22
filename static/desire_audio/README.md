# DESIRE voice clips — GODMODE V4 / Signal Lab

The dashboard code now supports the 13-clip local voice bundle and does not require an ElevenLabs API key to play these saved recordings.

## Install the bundled recordings

Upload the single file `desire_13_voice_clips_repo_upload.zip` into this folder (`static/desire_audio/`). On the first audio request, the server extracts only MP3 files from the ZIP into this directory.

Bundle includes activation, superior signal, strong/perfect setup, five-second countdown, twenty-second entry window, below-threshold, cancelled signal, CALL, PUT, scanning, fakeout warning, signal locked, and standby clips.

## Playback notes

- Use the dashboard's **ENABLE SOUND** button first; mobile browsers require a user gesture before audio playback.
- Local clips are served at `/audio/desire/{clip_name}`.
- If a clip is missing, the browser's built-in speech voice remains the fallback.
- Trading stays signal-only; no orders are placed automatically.
