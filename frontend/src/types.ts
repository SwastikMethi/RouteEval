export type Pricing = { source: string; effective_date: string; input_per_million?: number; cached_input_per_million?: number; output_per_million?: number; cache_write_per_million?: number };
export type CostProvenance = 'reported' | 'estimated' | 'mixed' | 'unavailable';
export type CostEstimate = { model: string; pricing: Pricing; calculated_at: string; backfilled?: boolean; buckets: Record<string, { tokens: number; rate_per_million: number; cost_usd: number }> };
export type Profile = {
  id: string; label: string; adapter: string; model: string; enabled: boolean;
  pricing?: Pricing | null;
  preflight?: { status: 'ready' | 'blocked' | 'missing'; version?: string; authentication?: string; model_access?: 'not_checked' | 'verified_in_previous_run' | 'last_execution_failed'; warnings: string[]; capabilities?: { usage: boolean; reported_cost: boolean; route: boolean; tool_events: boolean } };
};
export type Case = { id: string; title: string; category: string; difficulty: string; tags: string[]; prompt: string; timeout_seconds: number };
export type Suite = { id: string; name: string; version: string; hash: string; description: string; cases: Case[] };
export type Grader = { id: string; component: string; weight: number; score: number | null; passed: boolean; mandatory: boolean; summary: string; output: string; duration_ms: number; infrastructure_error: boolean };
export type Route = { turn_index: number; provider: string; model: string; response_model?: string };
export type Attempt = {
  id: string; run_id: string; case_id: string; profile_id: string; attempt_index: number; execution_order: number; status: string;
  score: number | null; full_pass: boolean | null; agent_duration_ms: number | null; preparation_duration_ms: number | null; grading_duration_ms: number | null;
  input_tokens: number | null; output_tokens: number | null; cached_input_tokens: number | null; reasoning_tokens: number | null; cost_usd: number | null;
  cost_provenance: CostProvenance; cost_estimate?: CostEstimate; cache_write_tokens?: number | null; tool_calls: number | null; turns: number | null; retries: number | null; configured_model: string; primary_observed_model: string | null;
  exit_code: number | null; error_code: string | null; error_message: string | null; files_changed: number; lines_added: number; lines_removed: number;
  created_at: string; started_at: string | null; completed_at: string | null; final_response: string; graders: Grader[]; routes: Route[];
  artifacts: { kind: string; id: string; sha256: string; byte_size: number; truncated: boolean }[];
  supersedes_attempt_id?: string;
};
export type AttemptDetail = Attempt & { case: Case; profile: Profile; prompt: string; fixture_hash: string; prompt_hash: string; command_preview: string[] };
export type Leader = {
  profile_id: string; label: string; mean_score: number | null; median_score: number | null; full_passes: number; total_attempts: number; pass_rate: number | null;
  completion_rate: number | null; median_agent_duration_ms: number | null; total_cost_usd: number | null; median_cost_usd: number | null; cost_coverage: number;
  cost_provenance?: CostProvenance; cost_sources?: Pricing[];
  input_tokens: number | null; output_tokens: number | null; tool_calls: number | null; timeout_rate: number; adapter_error_rate: number; flake_rate: number | null;
  score_stddev: number | null; score_min: number | null; score_max: number | null; quality_per_dollar: number | null; quality_per_minute: number | null; valid_attempts: number; failures: Record<string, number>;
};
export type Run = {
  id: string; status: string; suite: Suite; profiles: Profile[]; mode: string; attempts_per_case: number; timeout_seconds: number; random_seed: number;
  created_at: string; started_at: string | null; completed_at: string | null; total_attempts: number; completed_attempts: number; warnings: string[];
  attempts: Attempt[]; leaderboard: Leader[]; winners: Record<string, string | null>;
  categories: { category: string; profile_id: string; score: number | null }[]; pareto: { time: string[]; cost: string[] };
  metric_availability: { cost: number; route: number }; provisional: boolean; security_blocked?: boolean;
};
export type System = { version: string; local_only: boolean; mock_enabled: boolean; defaults: { timeout_seconds: number; attempts_per_case: number; mode: string }; storage: { keep_workspaces: boolean; retention_days: number; [key: string]: unknown }; warnings: string[] };
export type RouterData = { available: boolean; distribution: { model: string; count: number; share: number; mean_score: number | null; pass_rate: number | null; median_agent_duration_ms: number | null; total_cost_usd: number | null }[]; timelines: { attempt_id: string; case_id: string; routes: Route[] }[]; observations: { case_id: string; type: string; message: string }[]; categories: { category: string; model: string; count: number }[] };
