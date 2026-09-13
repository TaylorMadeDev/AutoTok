# AutoTok — AI Story Studio

AutoTok turns a Reddit text post (or your own script) into a vertical TikTok,
Reel, or Short with AI dubbing, burned-in captions, a Reddit-style intro card,
and automatically cut background footage.

## AutoTok Studio v3

The creator stays focused on three decisions: choose a story, review the edit,
and create the video. Less-frequent tools live behind the single **Studio**
button, while **Review & Publish** appears only after a successful render.

New production and publishing tools include:

- A media preflight for dimensions, duration, FPS, audio, file size, caption
  safe zones, and SRT readability.
- Ranked hook variants that flow directly into editable posting copy.
- Exact story/video duplicate warnings and persistent approval/publish states.
- Built-in and custom production presets instead of more main-screen switches.
- Story-aware footage categories and faster cuts for more dramatic scripts.
- Narration normalization/fades and a selectable cover frame baked onto exports.
- Series IDs, part numbering, continuation copy, and suggested 24-hour schedules.
- A rights record for the Reddit source, footage, music, and AI narration.
- Private local test packages and a CSV analytics dashboard.
- Quiet Windows notifications for failed renders or uploads.
- An optional isolated integration with
  [TikTokAutoUploader](https://github.com/haziq-exe/TikTokAutoUploader).

### Publishing workflow

1. Render a video and click **Review & Publish**.
2. Play it, choose a hook, edit the description/hashtags, and review preflight.
3. Confirm the source, footage, and audio rights.
4. Keep **Private test** selected for a local approval package, or explicitly
   choose **Public upload** and **Community uploader**.
5. For first-time community publishing, open **Studio → Publishing** and run
   setup. AutoTok creates `.autotok\uploader_env` and installs Chromium there.

The community uploader is experimental browser automation. AutoTok always uses
a visible browser, does not accept or store a TikTok password, does not expose
the package's optional stealth mode, and requires a confirmation for each
upload. Complete login and any account challenge yourself. AutoTok records the
uploader's reported result, but you should confirm the scheduled or public post
inside TikTok.

Analytics import accepts TikTok CSV exports with common fields such as `title`,
`views`, `likes`, `comments`, and `shares`. Column names are normalized on
import, so extra columns are retained for later comparisons.

## Production tools

The production workflow now includes:

- Local Whisper word timestamps with Karaoke, Purple Pop, and Classic captions.
- Automatic 60-second, 90-second, three-minute, or full-story series exports.
- Sentence-aware one-minute Flux requests to prevent HTTP 413 errors.
- Cached narration and alignment, plus fingerprinted resume support.
- A draggable safe-zone preview and adjustable caption height.
- Smart least-used footage selection with category subfolders and cached scene cuts.
- Optional background music with narration-friendly volume mixing.
- Persistent Reddit batch queue and render history.
- Voice previews, speed controls, and a custom pronunciation dictionary.
- Uncensored, softened, and platform-safe cleanup modes.
- `.srt` captions, vertical thumbnails, hooks, descriptions, hashtags, and JSON
  posting packages alongside every MP4.
- OpenRouter secrets stored in Windows Credential Manager.

## What it does

- Pulls safe, non-stickied text stories from a subreddit without Reddit OAuth.
- Filters out posts that are too short/long, removed, deleted, or NSFW.
- Lets you edit the title and full script before spending any TTS credits.
- Uses the same OpenRouter Flux TTS configuration as the sibling `aistory` app.
- Splits long narration into sentence-aware, roughly one-minute Flux requests,
  then joins them seamlessly to avoid HTTP 413 content-size errors.
- Falls back to the local Windows voice if Flux is unavailable.
- Randomly cuts and center-crops any number of videos to a 9:16 canvas.
- Generates large, timed, outlined captions and a polished Reddit intro card.
- Supports a quick 540×960 draft or final 1080×1920 H.264 export.

## Install from a GitHub release

1. Download `AutoTok-Windows-x64-vX.Y.Z.zip` from the project's **Releases**
   page and extract the whole ZIP to a writable folder.
2. Run `AutoTok.exe`. The first-run setup guides you through features, voice
   setup, a live API test, media folders, and optional TikTok publishing.
3. Keep the `_internal` folder beside `AutoTok.exe`; it contains the runtime
   needed by the portable build.

## Run from source

1. Double-click `setup.bat` once. The graphical installer walks through:
   selected features, component installation, Flux or Azure Speech, a required
   live voice test (or **Do it later**), optional visible TikTok sign-in, media
   folders, defaults, and desktop shortcut creation.
2. Double-click `run_autotok.bat` whenever you want to launch the app.

AutoTok has its own `.venv` after setup. If Python is missing and Windows
Package Manager is available, `setup.bat` installs Python 3.11 for the current
user. Node.js LTS is installed only when TikTok auto-upload is selected.

## Build a Windows release

The repository includes a GitHub Actions workflow. Pushing a version tag such
as `v1.0.1` builds the portable Windows app and attaches its ZIP to the GitHub
release. To build locally, install the source requirements plus PyInstaller,
then package `app.py` as a one-directory Windows application. The release ZIP
must keep `AutoTok.exe` and `_internal` together after extraction.

## AI voice: OpenRouter Flux or Azure Speech

On first launch, AutoTok checks:

1. `AutoTok\config.ini`
2. `aistory\config.ini`

This means AutoTok can reuse the existing aistory API key and never prints it
in the UI or logs. Click the **FLUX CONFIGURED** badge to change the key, model, or
voice. A new key can be created at <https://openrouter.ai/settings/keys>.

Azure Speech uses a Speech resource key, its matching region (for example,
`uksouth`), and a neural voice such as `en-GB-SoniaNeural`. The installer sends
a short live synthesis request and plays the returned audio before allowing the
setup to continue. Azure keys are region-scoped, so a key and region from
different resources will fail the test.

If OpenRouter rejects the key or TTS request, AutoTok logs the exact service
error and continues with the Windows voice so a render is not lost.

The expected configuration is:

```ini
[voice]
provider = flux

[openrouter]
api_key = your-key-here
model = deepgram/flux-tts:free
voice = flux-wes-en
http_referer =

[azure_speech]
api_key =
region = uksouth
voice = en-GB-SoniaNeural
```

`config.ini` is intentionally ignored by Git.

When AutoTok first saves or migrates a key, the real secret is placed in
Windows Credential Manager and `config.ini` contains only a marker. Click the
configured voice badge to test, switch, or replace the provider.

## Workflow

1. Enter a subreddit and click **Pull random story**, or paste your own script.
2. Put MP4/MOV/WebM clips in the `videos` folder and leave **Auto-pick** enabled.
   AutoTok randomly chooses one library video for each render. Alternatively,
   turn Auto-pick off and use **Choose files**. With no clips, AutoTok uses a
   clean gradient background so you can still test the pipeline.
3. Edit the story and choose how many words appear in each caption.
4. Open **Production settings** to select part length, caption animation,
   profanity handling, Whisper size, music, and voice speed.
5. Use **Preview** to inspect the safe zone and drag captions vertically.
6. Render a **Draft** first, then switch to **Full HD** for the final export.

Create category folders such as `videos\Minecraft`, `videos\Racing`, and
`videos\Cooking`. The category picker discovers them automatically. Put music
tracks in `music`; AutoTok can randomly select and loop one beneath narration.

The first word-aligned render downloads the selected Whisper model. `tiny.en`
is the default because it is fast and works well on clean generated speech.
Narration and alignment results are cached under `.autotok`.

Use **Queue 5 stories** for batch production. Completed jobs appear in History,
and failed stories remain queued for retry. AutoTok avoids adding the same
Reddit permalink twice.

Reddit's public endpoint can occasionally rate-limit desktop requests. If that
happens, wait briefly and retry; manually pasted stories always remain available.

For narration only, every standalone occurrence of `TIFU` is automatically
spoken as “time I effed up,” including occurrences in the subreddit, title, and
story body. The original spelling remains on screen.
