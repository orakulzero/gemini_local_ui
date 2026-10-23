import React, { useState, useEffect } from 'react';

export interface FlowSceneItem {
  scene_id: string;
  scene_number: number;
  title: string;
  narration_text: string;
  visual_prompt: string;
  duration_sec: number;
  camera_motion: string;
  on_screen_bullets: string[];
  status: string;
  clip_id?: string | null;
  video_url?: string | null;
}

export interface FlowProjectItem {
  project_id: string;
  topic: string;
  language: string;
  target_audience: string;
  aspect_ratio: string;
  model_key: string;
  scenes: FlowSceneItem[];
  status: string;
  assembled_video_url?: string | null;
}

export interface FlowStudioDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

export const FlowStudioDrawer: React.FC<FlowStudioDrawerProps> = ({
  isOpen,
  onClose,
}) => {
  const [topic, setTopic] = useState('');
  const [sceneCount, setSceneCount] = useState(4);
  const [audience, setAudience] = useState('university');
  const [aspectRatio, setAspectRatio] = useState('16:9');
  const [sourceContext, setSourceContext] = useState('');
  const [project, setProject] = useState<FlowProjectItem | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [flowAuth, setFlowAuth] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    fetch('/api/flow/status')
      .then((r) => r.json())
      .then((d) => {
        if (d && typeof d.authenticated === 'boolean') {
          setFlowAuth(d.authenticated);
        }
      })
      .catch(() => {});
  }, [isOpen]);

  if (!isOpen) return null;

  const handleGenerateStoryboard = async () => {
    if (!topic.trim()) {
      setErrorMsg('Please enter an educational topic.');
      return;
    }
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const resp = await fetch('/api/flow/projects/storyboard', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic: topic.trim(),
          scene_count: sceneCount,
          target_audience: audience,
          aspect_ratio: aspectRatio,
          source_context: sourceContext.trim() || undefined,
        }),
      });
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        throw new Error(data.detail || 'Failed to generate storyboard');
      }
      setProject(data.project);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setIsLoading(false);
    }
  };

  const handleGenerateSceneClip = async (sceneId: string) => {
    if (!project) return;
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const resp = await fetch(
        `/api/flow/projects/${project.project_id}/scenes/${sceneId}/generate`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        }
      );
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        throw new Error(data.detail || 'Failed to generate scene clip');
      }
      const projResp = await fetch(`/api/flow/projects/${project.project_id}`);
      const projData = await projResp.json();
      if (projData.ok) {
        setProject(projData.project);
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setIsLoading(false);
    }
  };

  const handleAssembleLesson = async () => {
    if (!project) return;
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const resp = await fetch(
        `/api/flow/projects/${project.project_id}/assemble`,
        { method: 'POST' }
      );
      const data = await resp.json();
      if (!resp.ok || !data.ok) {
        throw new Error(data.detail || 'Failed to assemble educational video');
      }
      setProject(data.project);
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div
      data-testid="flow-studio-drawer"
      className="fixed inset-y-0 right-0 z-40 w-full max-w-2xl bg-zinc-950 border-l border-zinc-800 shadow-2xl flex flex-col text-zinc-100"
    >
      <div className="px-4 py-3 border-b border-zinc-800 flex items-center justify-between bg-zinc-900/60">
        <div className="flex items-center gap-2">
          <span className="text-lg">🎬</span>
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">
              Google Flow Educational Video Studio (Veo 3.1)
            </h2>
            <p className="text-[11px] text-zinc-400">
              labs.google/fx/tools/flow •{' '}
              {flowAuth ? 'Flow Session Connected' : 'Local Synthesis / Storyboard Mode'}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="px-2 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300"
        >
          Close
        </button>
      </div>

      <div className="p-4 overflow-y-auto flex-1 space-y-4">
        <div className="bg-zinc-900/70 border border-zinc-800 rounded-xl p-3 space-y-3">
          <div>
            <label className="block text-xs text-zinc-400 mb-1">
              Educational Topic
            </label>
            <input
              type="text"
              data-testid="flow-topic-input"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. Transformer Self-Attention Mechanism, Quantum Entanglement..."
              className="w-full px-3 py-2 text-xs rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 focus:outline-none focus:border-blue-500"
            />
          </div>

          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="block text-xs text-zinc-400 mb-1">Scenes</label>
              <input
                type="number"
                min={1}
                max={12}
                value={sceneCount}
                onChange={(e) => setSceneCount(Number(e.target.value))}
                className="w-full px-2 py-1.5 text-xs rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100"
              />
            </div>
            <div>
              <label className="block text-xs text-zinc-400 mb-1">Audience</label>
              <select
                value={audience}
                onChange={(e) => setAudience(e.target.value)}
                className="w-full px-2 py-1.5 text-xs rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100"
              >
                <option value="beginner">Beginner</option>
                <option value="intermediate">Intermediate</option>
                <option value="university">University / Advanced</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-zinc-400 mb-1">
                Aspect Ratio
              </label>
              <select
                value={aspectRatio}
                onChange={(e) => setAspectRatio(e.target.value)}
                className="w-full px-2 py-1.5 text-xs rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100"
              >
                <option value="16:9">16:9 Landscape</option>
                <option value="9:16">9:16 Vertical</option>
                <option value="1:1">1:1 Square</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs text-zinc-400 mb-1">
              Optional Source Context (NotebookLM / Lecture Notes)
            </label>
            <textarea
              rows={2}
              value={sourceContext}
              onChange={(e) => setSourceContext(e.target.value)}
              placeholder="Paste key equations, definitions, or NotebookLM summary..."
              className="w-full px-3 py-1.5 text-xs rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100"
            />
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              data-testid="flow-generate-storyboard-btn"
              disabled={isLoading}
              onClick={handleGenerateStoryboard}
              className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-xs font-medium text-white"
            >
              {isLoading ? 'Working...' : 'Generate Educational Storyboard'}
            </button>

            <button
              type="button"
              data-testid="flow-assemble-btn"
              disabled={!project || isLoading}
              onClick={handleAssembleLesson}
              className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-xs font-medium text-white"
            >
              Assemble Full Video + Subtitles
            </button>
          </div>

          {errorMsg && (
            <div className="text-xs text-red-400 bg-red-950/40 border border-red-800/50 rounded p-2">
              {errorMsg}
            </div>
          )}
        </div>

        {project && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold text-zinc-300">
                Storyboard: {project.topic} ({project.scenes.length} scenes)
              </h3>
              <span className="text-[11px] px-2 py-0.5 rounded bg-zinc-800 text-zinc-300 font-mono">
                {project.status}
              </span>
            </div>

            {project.assembled_video_url && (
              <div className="bg-zinc-900/90 border border-emerald-700/50 rounded-xl p-3 space-y-2">
                <div className="text-xs font-medium text-emerald-300">
                  Assembled Educational Video Lesson
                </div>
                <video
                  controls
                  src={project.assembled_video_url}
                  className="w-full rounded-lg bg-black max-h-56"
                >
                  <track
                    kind="subtitles"
                    src={`/api/flow/projects/${project.project_id}/subtitles.vtt`}
                    srcLang={project.language}
                    label="Educational Subtitles"
                    default
                  />
                </video>
                <div className="flex items-center gap-3 text-xs">
                  <a
                    href={project.assembled_video_url}
                    download={`${project.project_id}_lesson.mp4`}
                    className="text-blue-400 hover:underline"
                  >
                    Download MP4
                  </a>
                  <a
                    href={`/api/flow/projects/${project.project_id}/subtitles.vtt`}
                    download={`${project.project_id}.vtt`}
                    className="text-blue-400 hover:underline"
                  >
                    Download WebVTT (.vtt)
                  </a>
                  <a
                    href={`/api/flow/projects/${project.project_id}/subtitles.srt`}
                    download={`${project.project_id}.srt`}
                    className="text-blue-400 hover:underline"
                  >
                    Download SubRip (.srt)
                  </a>
                </div>
              </div>
            )}

            <div className="space-y-2">
              {project.scenes.map((sc) => (
                <div
                  key={sc.scene_id}
                  className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-3 space-y-1.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-blue-300">
                      Scene {sc.scene_number}: {sc.title}
                    </span>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-zinc-400">
                        {sc.duration_sec}s • {sc.camera_motion}
                      </span>
                      <button
                        type="button"
                        disabled={isLoading}
                        onClick={() => handleGenerateSceneClip(sc.scene_id)}
                        className="px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-[11px] text-zinc-200"
                      >
                        {sc.status === 'completed' ? 'Regenerate Clip' : 'Render Clip'}
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-zinc-300">{sc.narration_text}</p>
                  <p className="text-[11px] text-zinc-500 font-mono">
                    Prompt: {sc.visual_prompt}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
