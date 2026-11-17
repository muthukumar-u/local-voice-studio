# Example inputs

After setup and model preparation, run these commands from the repository root. Generate an eight-second silent input using the bundled macOS ARM64 FFmpeg (use your installed `ffmpeg` on other systems):

```sh
mkdir -p outputs/example-input
.tools/media/node_modules/@ffmpeg-installer/darwin-arm64/ffmpeg \
  -f lavfi -i color=c=navy:s=360x640:r=30:d=8 \
  -an -c:v libx264 -pix_fmt yuv420p outputs/example-input/video.mp4

.venv/bin/python -m local_voice_studio process \
  --video outputs/example-input/video.mp4 --narration examples/narration.txt \
  --config config/explainer.toml --output-dir outputs/example-narration

.venv/bin/python -m local_voice_studio process \
  --video outputs/example-input/video.mp4 --subtitles examples/captions.srt \
  --config config/explainer.toml --output-dir outputs/example-srt

.venv/bin/python -m local_voice_studio process \
  --video outputs/example-input/video.mp4 --subtitles examples/captions.vtt \
  --config config/explainer.toml --output-dir outputs/example-vtt
```

Each successful output directory contains `video.mp4`, `narration.wav`, `receipt.json` and `result.json`. Choose new output directories for subsequent runs. The two subtitle files supply the same words and cue window; their captions are burned into the video. The plain narration run has speech without captions.

To try the upload page, start `.venv/bin/python -m local_voice_studio serve`, open `http://127.0.0.1:8765`, and select the generated input plus narration text or either subtitle file. The Python example in the main README uses the same inputs and production facade.
