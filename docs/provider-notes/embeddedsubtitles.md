# Embedded Subtitles Provider Notes

- Legacy source: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/embeddedsubtitles.py`.
- Replacement policy: this catalog entry intentionally keeps provider id `embeddedsubtitles` and marks `builtin_provider_replacement: true`; it depends on the trusted Provider Hub built-in migration allowance, not on a non-shadowing marketplace id.
- Provider type: local media extractor, not a web subtitle source.
- Catalog behavior: inspects local media files with `ffprobe`, exposes allowed embedded text subtitle streams, and extracts the selected stream with `ffmpeg`.
- External requirements: Bazarr+ worker must be able to read the media path and execute configured `ffprobe` and `ffmpeg` binaries.
- Supported codecs: `ass`, `subrip`, `webvtt`, and `mov_text`.
- Download flow: generated subtitle bytes from the local media container.
