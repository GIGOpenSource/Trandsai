export interface StatsData {
  companion_count: number;
  total_turns: number;
  avg_affection: number;
  knowledge_stats: {
    total_entries: number;
    categories: string[];
  };
}

export interface CompanionProfile {
  id: string;
  name: string;
  gender: string;
  age: number;
  city: string;
  personality: string;
  background: string;
  speech_style: string;
  hobbies: string;
  values: string;
  fears: string;
  love_view: string;
  daily_routine: string;
  favorite_things: string;
  language: string;  // 与地区(city)信息保持一致的语言
  created_at: string;
}

export interface CompanionState {
  affection: number;
  turns: number;
}

export interface CompanionItem {
  profile: CompanionProfile;
  state: CompanionState;
  avatar?: string;
  avatar_generating?: boolean;
}

export interface MomentItem {
  id: number;
  companion_id: string;
  companion_name: string;
  companion_avatar: string;
  image_url: string | null;
  image_generating?: boolean;
  caption: string;
  likes_count: number;
  comments_count: number;
  created_at: string;
}

export interface UserItem {
  id: number;
  username: string;
  nickname: string | null;
  gender: string | null;
  sexual_orientation: string | null;
  age?: number | null;
  region?: string | null;
  occupation?: string | null;
  created_at: string;
}

export interface UserCompanionStatItem {
  companion_id: string;
  companion_name: string;
  avatar_url: string;
  affection: number;
  turns: number;
  updated_at: string | null;
}

export interface FeedbackItem {
  id: number;
  user_id: number;
  user_name: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  last_message: string;
  last_message_sender: string;
}

export interface FeedbackMessage {
  id: number;
  sender: string;
  content: string;
  created_at: string;
}

export interface FeedbackThreadDetail {
  thread: {
    id: number;
    user_id: number;
    user_name: string | null;
    status: string;
  };
  messages: FeedbackMessage[];
}

export interface KnowledgeEntry {
  id: string;
  title: string;
  category: string;
  language: string;
  content: string;
}

export interface SearchResult extends KnowledgeEntry {
  distance?: number;
}

export interface SystemConfig {
  anthropic_ready: boolean;
  openai_ready: boolean;
  deepseek_ready: boolean;
  model_provider: string;
  admin_password_set: boolean;
}

export interface AgentConfig {
  model_provider?: string;
  temperature?: number;
  max_tokens?: number;
  system_prompt_zh?: string;
  system_prompt_en?: string;
  system_prompt_ja?: string;
  system_prompt_ko?: string;
  system_prompt_pt?: string;
  system_prompt_es?: string;
  system_prompt_id?: string;
}

export interface EmbeddingStatus {
  state: string;
  message: string;
  progress: number;
}

export interface ConfigGroup {
  id: number;
  key: string;
  name: string;
  description: string;
  config_type: 'model_service' | 'agent' | 'image_generation';
  config_json: Record<string, unknown>;
  enabled: boolean;
  sort_order: number;
  created_at?: string;
  updated_at?: string;
}

export interface AnalyticsPageViewItem {
  page_path: string;
  page_name: string;
  language: string;
  pv_count: number;
  uv_count: number;
}

export interface AnalyticsButtonClickItem {
  button_id: string;
  button_name: string;
  page_path: string;
  language: string;
  click_count: number;
  uv_count: number;
}

export interface AnalyticsSummary {
  total_pv: number;
  total_uv: number;
  total_clicks: number;
  total_button_uv: number;
}

export interface DauSeriesItem {
  date: string;
  dau: number;
}

export interface RetentionCohortRow {
  cohort_date: string;
  new_users: number;
  retention_pct: Record<string, number>;
  retention_counts: Record<string, number>;
}

export interface RetentionPayload {
  cohorts: RetentionCohortRow[];
  windows: number[];
  note?: string;
}

export interface SystemNotificationItem {
  id: number;
  title: string;
  content: string;
  language: string;
  created_at: string;
  updated_at?: string;
}

export interface ChatSessionItem {
  user_id: number;
  username: string;
  nickname: string;
  companion_id: string;
  companion_name: string;
  turns: number;
  affection: number;
  session_updated_at: string | null;
  message_count: number;
  last_message_at: string | null;
}

export interface ChatSessionMessage {
  id: number;
  role: string;
  content: string;
  created_at: string | null;
}

export interface ChatSessionMessagesResponse {
  user_id: number;
  username: string;
  nickname: string;
  companion_id: string;
  companion_name: string;
  total: number;
  offset: number;
  messages: ChatSessionMessage[];
}
