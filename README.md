# Enhancer tool pack - copy the `Effects/` folder over your repo (same paths), commit, push.
Built on top of Effects.zip + overlay.zip.

1. Tools screen: new "Enhancer" button. 6 tools now: 2 columns x 3 rows on phones (3 x 2 on wide screens).
2. Enhancer screen: pick a video or picture, Size 1x / 2x / 4x, Denoise strength slider (blends the two models:
   realesr-general-x4v3 and realesr-general-wdn-x4v3, so they are ONE tool). Live preview of a small area with a
   draggable Before / After line. Photos save as JPEG, videos as MP4 (sound kept), through the same Save/Share bar as the other tools.
3. The old Enhance entry is hidden in the effects library (AiAction.Enhance.offered = false). Engine code is untouched.
4. Both models are now bundled in the app (shared/assets/ai/..., about 4.9 MB). HOME_PAGE is example.invalid, so the
   old download could never work. They are copied to app storage once and checked with the same SHA-256 values the app already had.
5. Photos are capped at 12 megapixels of output; videos use the old limits (4x up to 360p, 2x up to 720p, 1x up to 1080p), max 5 minutes.

NOT compiled here (no Android SDK) - build on GitHub and send me the log if anything fails.
New texts are English only; run python3 tools/i18n_translate.py to translate them.
