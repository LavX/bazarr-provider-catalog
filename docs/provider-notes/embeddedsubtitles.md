# Embedded Subtitles Provider Notes

- Legacy source: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/embeddedsubtitles.py`.
- Catalog id: `embeddedsubtitles`, reusing the built-in provider id so a trusted catalog install overwrites the built-in in place (no duplicate provider).
- Provider type: local media extractor, not a web subtitle source.
- Catalog behavior: inspects local media files with `ffprobe`, exposes allowed embedded text subtitle streams, and extracts the selected stream with `ffmpeg`.
- External requirements: Bazarr+ worker must be able to read the media path and execute configured `ffprobe` and `ffmpeg` binaries.
- Supported codecs: `ass`, `subrip`, `webvtt`, and `mov_text`.
- Download flow: generated subtitle bytes from the local media container.
