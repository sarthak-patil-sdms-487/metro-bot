// This file defines the TypeScript interfaces that match the backend API schemas.

export interface User {
  id: number;
  whatsapp_number: string;
  name: string | null;
  created_at: string; // ISO 8601 date string
  total_conversations: number;
}

export interface Conversation {
  id: number;
  user_id: number;
  status: string;
  channel: 'chat' | 'call';
  created_at: string;
  updated_at: string;
  user: User;
  is_closed: boolean;
  feedback_rating: number | null;
  feedback_comment: string | null;
  preferred_language: string | null;
  message_count: number;
  categories: string[];
  category_log_ids: number[];
  tracking_ids: string[];
  detected_languages: string[];
}

export interface Message {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface CategoryLog {
  id: number;
  conversation_id: number;
  user: User;
  categories: string[];
  subcategory: string | null;
  message: string;
  status: 'pending' | 'approved' | 'resolved' | 'rejected';
  workflow_status: string;
  created_at: string;
  channel: 'chat' | 'call';
  language: string | null;
  tracking_id: string | null;
}

export interface Ticket extends Omit<CategoryLog, 'message'> {
  tracking_id: string;
  category_log_id: number;
  conversation_id: number;
  language: string | null;
  message: {
    name?: string;
    contact?: string;
    station?: string;
    description?: string;
  };
  channel: 'chat' | 'call';
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface StatsOverview {
  total_users: number;
  total_conversations: number;
  conversations_by_channel: { chat?: number; call?: number };
  messages_today: number;
  messages_this_week: number;
  tickets_by_status: { [key: string]: number };
  categories_by_type: { [key: string]: number };
  messages_per_day: { date: string; count: number }[];
  avg_tickets_resolved_per_day: number;
}

export interface ResponseSourceStats {
  total_cache: number;
  total_llm: number;
  daily_stats: { date: string; cache: number; llm: number }[];
}

export interface CostAuditEvent {
  id: number; operation: 'llm' | 'tts' | 'stt';
  source: 'cache' | 'llm' | 'tts' | 'stt' | 'rules'; question: string | null;
  answer: string | null; provider: string | null; model: string | null;
  input_units: number; output_units: number; actual_cost_inr: number;
  uncached_cost_inr: number; saved_cost_inr: number; created_at: string;
}

export interface CostAuditConversation {
  conversation_id: number; call_session_id: number | null; channel: 'chat' | 'call';
  status: string; user_name: string | null; user_number: string | null;
  created_at: string; ended_at: string | null; actual_cost_inr: number;
  uncached_cost_inr: number; saved_cost_inr: number; cache_hits: number;
  llm_calls: number; fresh_tts: number; cached_tts: number;
  messages: Message[]; events: CostAuditEvent[];
}

export interface CostAuditResponse extends PaginatedResponse<CostAuditConversation> {
  summary: { actual_cost_inr: number; uncached_cost_inr: number; saved_cost_inr: number;
    stt_cost_inr: number; cache_hits: number; llm_calls: number };
  tts_cache: {
    stored_entries: number; total_reuses: number; saved_cost_inr: number;
    most_reused: { text: string; language: string; reuse_count: number } | null;
  };
  pricing: { llm_input_usd_per_million: number; llm_output_usd_per_million: number;
    tts_inr_per_10k_chars: number; stt_inr_per_hour: number };
}

export interface CallSession {
  id: number;
  conversation_id: number;
  user_id: number;
  provider_call_id: string;
  status: 'ringing' | 'connecting' | 'active' | 'completed' | 'failed';
  direction: 'inbound' | 'outbound';
  started_at: string;
  answered_at: string | null;
  ended_at: string | null;
  end_reason: string | null;
  detected_languages: string[];
  user: Pick<User, 'id' | 'name' | 'whatsapp_number'>;
  transcript_count: number;
  duration_seconds: number | null;
  recording_available: boolean;
  recording_url: string | null;
}
