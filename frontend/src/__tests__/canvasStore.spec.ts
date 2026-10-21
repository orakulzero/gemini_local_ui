import { describe, it, expect, beforeEach } from 'vitest';
import { useCanvasStore } from '../store/canvasStore';

describe('useCanvasStore', () => {
  beforeEach(() => {
    useCanvasStore.setState({
      isOpen: false,
      activeArtifact: null,
      activeTab: 'code',
    });
  });

  it('initializes in closed state with null artifact', () => {
    const state = useCanvasStore.getState();
    expect(state.isOpen).toBe(false);
    expect(state.activeArtifact).toBeNull();
    expect(state.activeTab).toBe('code');
  });

  it('openCanvas opens artifact and selects code tab for programming languages', () => {
    useCanvasStore.getState().openCanvas({
      title: 'simulation.py',
      language: 'python',
      content: 'print("hello quantum")',
      originalContent: 'print("hello quantum")',
    });

    const state = useCanvasStore.getState();
    expect(state.isOpen).toBe(true);
    expect(state.activeArtifact?.title).toBe('simulation.py');
    expect(state.activeArtifact?.language).toBe('python');
    expect(state.activeArtifact?.content).toBe('print("hello quantum")');
    expect(state.activeTab).toBe('code');
  });

  it('openCanvas automatically defaults to preview tab for HTML, SVG, and Markdown', () => {
    useCanvasStore.getState().openCanvas({
      title: 'diagram.svg',
      language: 'svg',
      content: '<svg><circle cx="50" cy="50" r="40" /></svg>',
      originalContent: '<svg><circle cx="50" cy="50" r="40" /></svg>',
    });

    expect(useCanvasStore.getState().isOpen).toBe(true);
    expect(useCanvasStore.getState().activeTab).toBe('preview');

    useCanvasStore.getState().openCanvas({
      title: 'notes.md',
      language: 'markdown',
      content: '# Heading\nParagraph',
      originalContent: '# Heading\nParagraph',
    });
    expect(useCanvasStore.getState().activeTab).toBe('preview');
  });

  it('updateContent modifies active content while preserving originalContent', () => {
    useCanvasStore.getState().openCanvas({
      title: 'script.js',
      language: 'javascript',
      content: 'const x = 1;',
      originalContent: 'const x = 1;',
    });

    useCanvasStore.getState().updateContent('const x = 2; const y = 3;');

    const state = useCanvasStore.getState();
    expect(state.activeArtifact?.content).toBe('const x = 2; const y = 3;');
    expect(state.activeArtifact?.originalContent).toBe('const x = 1;');
  });

  it('revertContent restores originalContent', () => {
    useCanvasStore.getState().openCanvas({
      title: 'app.rs',
      language: 'rust',
      content: 'fn main() {}',
      originalContent: 'fn main() {}',
    });

    useCanvasStore.getState().updateContent('fn main() { panic!("broken"); }');
    expect(useCanvasStore.getState().activeArtifact?.content).toContain('broken');

    useCanvasStore.getState().revertContent();
    expect(useCanvasStore.getState().activeArtifact?.content).toBe('fn main() {}');
  });

  it('closeCanvas resets state', () => {
    useCanvasStore.getState().openCanvas({
      title: 'test.html',
      language: 'html',
      content: '<h1>Hi</h1>',
      originalContent: '<h1>Hi</h1>',
    });

    useCanvasStore.getState().closeCanvas();

    const state = useCanvasStore.getState();
    expect(state.isOpen).toBe(false);
    expect(state.activeArtifact).toBeNull();
  });
});
