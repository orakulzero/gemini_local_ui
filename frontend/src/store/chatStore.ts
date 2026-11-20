import { create } from 'zustand';
import { createDagSlice, computeActiveBranch } from './slices/dagSlice';
import {
  createStreamSlice,
  mergeAssistantMetadata,
  selectMergedImages,
} from './slices/streamSlice';
import { createHitlSlice } from './slices/hitlSlice';
import { createUiSlice } from './slices/uiSlice';
import type {
  ChatState,
  DagSlice,
  HitlSlice,
  PendingApprovalEntry,
  PendingElicitationEntry,
  StreamSlice,
  UiSlice,
} from './slices/types';

export { computeActiveBranch, mergeAssistantMetadata, selectMergedImages };
export type {
  ChatState,
  DagSlice,
  HitlSlice,
  PendingApprovalEntry,
  PendingElicitationEntry,
  StreamSlice,
  UiSlice,
};

export const useChatStore = create<ChatState>()((...a) => ({
  ...createDagSlice(...a),
  ...createStreamSlice(...a),
  ...createHitlSlice(...a),
  ...createUiSlice(...a),
}));
