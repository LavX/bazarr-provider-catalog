# WhisperAI Provider Notes

- Legacy source: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/whisperai.py`.
- Provider type: generated subtitle provider, not a web subtitle index.
- Catalog behavior: extracts local audio with `ffmpeg`, sends it to a configured Whisper web service, and stores the returned SRT bytes.
- Required settings: `endpoint` and `ffmpeg_path`.
- Search behavior: transcribes when requested subtitle language matches the source audio language. Translates only to English when source audio differs, matching Whisper's translation limitation.
- Detection behavior: when the video has no audio language tags, search extracts audio and calls `/detect-language`.
- Download flow: `POST /asr` with raw PCM audio, `task`, `language`, `output=srt`, and `encode=false`.
