# WhisperAI Provider Notes

- Provenance: independent MIT reimplementation written for the Provider Hub `search`/`download` contract. It does not reuse code from Bazarr's GPLv3 `subliminal_patch` WhisperAI provider. The two share only the Whisper web-service API surface (`/asr`, `/detect-language`, the `task`/`language`/`output`/`encode` parameters) and the public set of languages Whisper supports, neither of which is copyrightable expression. The supported-language table is sourced from the MIT-licensed `whisper.tokenizer.LANGUAGES` mapping in openai/whisper plus ISO 639-3 alpha-3 codes; it is not copied from the GPL provider.
- Provider type: generated subtitle provider, not a web subtitle index.
- Catalog behavior: extracts local audio with `ffmpeg`, sends it to a configured Whisper web service, and stores the returned SRT bytes.
- Required settings: `endpoint` and `ffmpeg_path`.
- Search behavior: transcribes when requested subtitle language matches the source audio language. Translates only to English when source audio differs, matching Whisper's translation limitation.
- Detection behavior: when the video has no audio language tags, search extracts audio and calls `/detect-language`.
- Download flow: `POST /asr` with raw PCM audio, `task`, `language`, `output=srt`, and `encode=false`.
