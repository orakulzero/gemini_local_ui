---
name: google-flow-video
description: Direct execution bridge to Google Flow (labs.google/fx/tools/flow) and Veo 3.1 Educational Video Studio. Generates structured pedagogical storyboards, per-scene visual prompts and voiceover scripts, WebVTT/SRT subtitle tracks, and assembled MP4 educational lessons.
---

# Google Flow Educational Video Studio Skill (`google-flow-video`)

Use this skill when creating educational videos, animated lecture explainers, visual STEM storyboards, or captioned MP4 video lessons via Google Flow (`https://labs.google/fx/tools/flow`).

## Capabilities

1. **Pedagogical Storyboard Generation**:
   - Decomposes any technical or academic topic into 1–12 structured scenes (`Hook & Core Phenomenon`, `Structural Architecture`, `Step-by-Step Dynamics`, `Mathematical Formulation`, `Real-World Application`).
   - Produces Veo 3.1 cinematic visual prompts, camera motion directives (`slow push-in`, `orbital pan`, `macro tracking shot`, `isometric zoom-out`), narration voiceover scripts, and on-screen bullet points.
2. **Scene Clip Rendering & Full Lesson Assembly**:
   - Renders per-scene `.mp4` clips and assembles full multi-scene educational lessons with synchronized WebVTT (`.vtt`) and SubRip (`.srt`) subtitle tracks.

## CLI Usage

```bash
# Dry-run storyboard generation (outputs JSON)
python .agents/skills/google-flow-video/scripts/flow_lesson_cli.py \
  --topic "Fourier Transform" \
  --scenes 3 \
  --audience university \
  --dry-run

# Full educational video assembly with subtitles
python .agents/skills/google-flow-video/scripts/flow_lesson_cli.py \
  --topic "Transformer Self-Attention" \
  --scenes 4 \
  --assemble
```
