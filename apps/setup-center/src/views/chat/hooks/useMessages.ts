import { useReducer, useCallback, useRef, useEffect } from "react";
import type { ChatMessage, ChatConversation, ChainSummaryItem, ChatArtifact } from "../utils/chatTypes";
import {
  loadMessagesFromStorage,
  STORAGE_KEY_ACTIVE,
  STORAGE_KEY_MSGS_PREFIX,
  STORAGE_KEY_CONVS,
} from "../utils/chatHelpers";

// ── Message Actions ──

export type MessageAction =
  | { type: "SET_ALL"; messages: ChatMessage[] }
  | { type: "APPEND"; message: ChatMessage }
  | { type: "APPEND_MANY"; messages: ChatMessage[] }
  | { type: "APPEND_SYSTEM"; content: string; id: string }
  | { type: "UPDATE_BY_ID"; id: string; updater: (msg: ChatMessage) => ChatMessage }
  | { type: "MAP_ALL"; updater: (msg: ChatMessage) => ChatMessage }
  | { type: "SLICE"; endIndex: number }
  | { type: "PATCH_FROM_BACKEND"; patcher: (msgs: ChatMessage[]) => ChatMessage[] }
  | { type: "CLEAR" };

function messageReducer(state: ChatMessage[], action: MessageAction): ChatMessage[] {
  if (__DEV__) {
    console.debug("[msg]", action.type, action.type === "SET_ALL" ? `(${action.messages.length})` : "");
  }
  switch (action.type) {
    case "SET_ALL":
      return action.messages;
    case "APPEND":
      return [...state, action.message];
    case "APPEND_MANY":
      return [...state, ...action.messages];
    case "APPEND_SYSTEM":
      return [...state, { id: action.id, role: "system", content: action.content, timestamp: Date.now() }];
    case "UPDATE_BY_ID":
      return state.map((m) => m.id === action.id ? action.updater(m) : m);
    case "MAP_ALL":
      return state.map(action.updater);
    case "SLICE":
      return state.slice(0, action.endIndex);
    case "PATCH_FROM_BACKEND":
      return action.patcher(state);
    case "CLEAR":
      return [];
    default:
      return state;
  }
}

const __DEV__ = typeof globalThis !== "undefined" && (globalThis as Record<string, unknown>).__DEV__ === true || import.meta.env?.DEV === true;

// ── Conversation Actions ──

export type ConversationAction =
  | { type: "SET_ALL"; conversations: ChatConversation[] }
  | { type: "ADD"; conversation: ChatConversation }
  | { type: "UPDATE"; id: string; updates: Partial<ChatConversation> }
  | { type: "UPDATE_BY_ID"; id: string; updater: (conv: ChatConversation) => ChatConversation }
  | { type: "MAP_ALL"; updater: (conv: ChatConversation) => ChatConversation }
  | { type: "DELETE"; id: string; onDelete?: (conv: ChatConversation) => void }
  | { type: "MERGE_BACKEND"; backendConvs: ChatConversation[]; localOnly?: ChatConversation[] }
  | { type: "FILTER"; predicate: (conv: ChatConversation) => boolean };

function conversationReducer(state: ChatConversation[], action: ConversationAction): ChatConversation[] {
  if (__DEV__) {
    console.debug("[conv]", action.type);
  }
  switch (action.type) {
    case "SET_ALL":
      return action.conversations;
    case "ADD":
      return [action.conversation, ...state];
    case "UPDATE": {
      const found = state.find(c => c.id === action.id);
      if (!found) return state;
      return state.map(c => c.id === action.id ? { ...c, ...action.updates } : c);
    }
    case "UPDATE_BY_ID":
      return state.map(c => c.id === action.id ? action.updater(c) : c);
    case "MAP_ALL":
      return state.map(action.updater);
    case "DELETE": {
      if (action.onDelete) {
        const conv = state.find(c => c.id === action.id);
        if (conv) action.onDelete(conv);
      }
      return state.filter(c => c.id !== action.id);
    }
    case "MERGE_BACKEND": {
      const prevMap = new Map(state.map(c => [c.id, c]));
      const merged = action.backendConvs.map(b => {
        const local = prevMap.get(b.id);
        if (!local) return b;
        return {
          ...local,
          title: local.titleGenerated ? local.title : (b.title || local.title || "对话"),
          lastMessage: b.lastMessage || local.lastMessage,
          timestamp: Math.max(local.timestamp || 0, b.timestamp || 0),
          messageCount: Math.max(local.messageCount || 0, b.messageCount || 0),
          agentProfileId: b.agentProfileId || local.agentProfileId,
        };
      });
      const backendIds = new Set(action.backendConvs.map(c => c.id));
      const localOnly = state.filter(c => !backendIds.has(c.id));
      return [...merged, ...localOnly];
    }
    case "FILTER":
      return state.filter(action.predicate);
    default:
      return state;
  }
}

// ── Hooks ──

export function useMessageReducer() {
  const [messages, dispatch] = useReducer(messageReducer, [], () => {
    try {
      const convId = localStorage.getItem(STORAGE_KEY_ACTIVE);
      if (!convId) return [];
      return loadMessagesFromStorage(STORAGE_KEY_MSGS_PREFIX + convId);
    } catch { return []; }
  });

  const messagesRef = useRef(messages);
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  return { messages, dispatch, messagesRef };
}

export function useConversationReducer() {
  const [conversations, dispatch] = useReducer(conversationReducer, [], () => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY_CONVS);
      return raw ? JSON.parse(raw) : [];
    } catch { return []; }
  });

  const conversationsRef = useRef(conversations);
  useEffect(() => { conversationsRef.current = conversations; }, [conversations]);

  return { conversations, dispatch, conversationsRef };
}
