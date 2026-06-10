const BASE = import.meta.env.BASE_URL || '/app/';

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    credentials: 'include',
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const err = new Error(res.statusText);
    (err as any).status = res.status;
    let detail: string | undefined;
    try {
      const body = await res.json();
      detail = body.detail;
    } catch {}
    if (detail) (err as any).detail = detail;
    throw err;
  }
  return res.json();
}

export interface DashboardData {
  today: string;
  weekday: string;
  deadline_pressure: Record<string, any>;
  workload_density: Record<string, any>;
  active_context: Record<string, any>;
  homework: any[];
  homework_count: number;
  homework_hidden_count?: number;
  today_schedule: any[];
  calendar_events: any[];
  temporal_blocks: any[];
  vocab_progress: Record<string, any>;
  fitness: Record<string, any>;
  finance: Record<string, any>;
  parent_funds?: Record<string, any>;
  partner_debts?: Record<string, any>;
  art: Record<string, any>;
  sync_health: Record<string, any>;
  calendar_consistency?: Record<string, any>;
}

export interface TimelineEvent {
  source: string;
  type: string;
  title: string;
  start?: string;
  end?: string;
  location?: string;
  deadline?: string;
  course?: string;
  event_id?: string;
  calendar_id?: string;
}

export interface TimelineData {
  date: string;
  count: number;
  events: TimelineEvent[];
}

export interface WorkoutSession {
  session: any;
  date: string;
  weekday: string;
  planned_day: string;
  is_training_day: boolean;
  available_days: string[];
  recommended_day: string;
}

export async function checkAuth(): Promise<boolean> {
  try {
    await request<any>('/api/web/auth/check');
    return true;
  } catch {
    return false;
  }
}

export async function login(pin: string): Promise<void> {
  await request('/api/web/auth/login', {
    method: 'POST',
    body: JSON.stringify({ pin }),
  });
}

export async function logout(): Promise<void> {
  await request('/api/web/auth/logout', { method: 'POST' });
}

export async function getDashboard(): Promise<DashboardData> {
  return request<DashboardData>('/api/web/dashboard');
}

export async function getTimeline(dateStr?: string): Promise<TimelineData> {
  const params = dateStr ? `?date_str=${dateStr}` : '';
  return request<TimelineData>(`/api/web/timeline${params}`);
}

export async function getWorkoutSession(dateStr?: string): Promise<WorkoutSession> {
  const params = dateStr ? `?date=${dateStr}` : '';
  return request<WorkoutSession>(`/api/workout/session${params}`);
}

export interface ActionResponse {
  ok: boolean;
  message: string;
  command_type: string;
  events: number;
  needs_followup: boolean;
  action_id?: string;
  action_type?: string;
  proposal?: any;
  dashboard?: DashboardData;
}

export interface CalendarProposalRequest {
  action: 'create' | 'update' | 'delete';
  title?: string;
  date?: string;
  start_time?: string;
  end_time?: string;
  location?: string;
  note?: string;
  event_id?: string;
  calendar_id?: string;
}

export interface CalendarConflict {
  source: string;
  type: string;
  title: string;
  start: string;
  end: string;
  location?: string;
  event_id?: string;
}

export interface CalendarProposalResponse {
  ok: boolean;
  message: string;
  proposal?: any;
  dashboard?: DashboardData;
  needs_followup?: boolean;
  conflicts?: CalendarConflict[];
}

export async function postCalendarProposal(
  req: CalendarProposalRequest,
): Promise<CalendarProposalResponse> {
  return request<CalendarProposalResponse>('/api/web/calendar/proposal', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export interface RecentAction {
  action_id: string;
  action_type: string;
  label: string;
  timestamp: string;
  reverted: boolean;
  can_undo: boolean;
  params?: Record<string, any>;
}

export async function getRecentActions(limit = 20): Promise<{ actions: RecentAction[] }> {
  return request<{ actions: RecentAction[] }>(`/api/web/actions/recent?limit=${limit}`);
}

export async function postUndo(actionId: string): Promise<{ ok: boolean; message: string }> {
  return request('/api/web/actions/undo', {
    method: 'POST',
    body: JSON.stringify({ action_id: actionId }),
  });
}

export async function postProposalDecision(
  proposal: any,
  decision: 'accept' | 'reject',
): Promise<{ ok: boolean; message: string; needs_followup: boolean; event?: any; dashboard?: DashboardData }> {
  return request('/api/web/proposals/decision', {
    method: 'POST',
    body: JSON.stringify({ proposal, decision }),
  });
}

export async function postAction(text: string, action?: string, payload?: Record<string, any>): Promise<ActionResponse> {
  return request<ActionResponse>('/api/web/actions', {
    method: 'POST',
    body: JSON.stringify({ text, action, payload }),
  });
}

export interface FinanceActionRequest {
  action: 'expense' | 'income' | 'parent_received' | 'parent_plan' | 'partner_debt';
  amount: number;
  category?: string;
  description?: string;
  source?: string;
  person?: string;
  item_id?: string;
  requested_date?: string;
  date?: string;
  counterparty?: string;
}

export interface FinanceActionResponse {
  ok: boolean;
  message: string;
  action: string;
  events: number;
  needs_followup: boolean;
  dashboard?: DashboardData;
  action_id?: string | null;
  can_undo?: boolean;
}

export interface FinanceRevertRequest {
  action_type: 'finance_transaction' | 'finance_income';
  action_id: string;
  amount: number;
  category?: string;
}

export interface FinanceRevertResponse {
  ok: boolean;
  message: string;
  events?: number;
  needs_followup?: boolean;
  dashboard?: DashboardData;
}

export interface TasksActionRequest {
  action: 'complete' | 'skip' | 'delay_30' | 'calendar_proposal';
  items: Array<{ id: string; title: string; course?: string; deadline?: string }>;
  date?: string;
  start_time?: string;
  end_time?: string;
  location?: string;
  note?: string;
}

export interface TasksActionResponse {
  ok: boolean;
  message: string;
  action: string;
  events: number;
  item_count?: number;
  action_id?: string;
  proposal?: any;
  dashboard?: DashboardData;
}

export async function postTasksAction(req: TasksActionRequest): Promise<TasksActionResponse> {
  return request<TasksActionResponse>('/api/web/tasks/action', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function postFinanceAction(req: FinanceActionRequest): Promise<FinanceActionResponse> {
  return request<FinanceActionResponse>('/api/web/finance/action', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function postFinanceRevert(req: FinanceRevertRequest): Promise<FinanceRevertResponse> {
  return request<FinanceRevertResponse>('/api/web/finance/revert', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export interface TodayActionRequest {
  action: 'art_progress' | 'hydration' | 'completion' | 'context' | 'school_leave_today' | 'sync_refresh';
  minutes?: number;
  amount_ml?: number;
  text?: string;
  type?: string;
  note?: string;
  sessions?: number;
  kind?: string;
  date?: string;
}

export interface TodayActionResponse {
  ok: boolean;
  message: string;
  action: string;
  events: number;
  dashboard?: DashboardData;
  action_id?: string;
}

export async function postTodayAction(req: TodayActionRequest): Promise<TodayActionResponse> {
  return request<TodayActionResponse>('/api/web/today/action', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export interface ReviewActionRequest {
  mood_score?: number;
  energy_score?: number;
  pressure_score?: number;
  body_state?: string;
  completed?: string;
  deviation?: string;
  tomorrow?: string;
  note?: string;
}

export interface ReviewActionResponse {
  ok: boolean;
  message: string;
  events: number;
  dashboard?: DashboardData;
}

export async function postReviewAction(req: ReviewActionRequest): Promise<ReviewActionResponse> {
  return request<ReviewActionResponse>('/api/web/review/action', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export type SystemAction =
  | 'sync_all'
  | 'sync_homework'
  | 'sync_schedule'
  | 'sync_calendar'
  | 'sync_vocab'
  | 'calendar_review'
  | 'calendar_repair';

export interface SystemActionResponse {
  ok: boolean;
  message: string;
  action: SystemAction;
  events: number;
  dashboard?: DashboardData;
}

export async function postSystemAction(action: SystemAction): Promise<SystemActionResponse> {
  return request<SystemActionResponse>('/api/web/system/action', {
    method: 'POST',
    body: JSON.stringify({ action }),
  });
}

export async function selectWorkoutDay(dateStr: string, dayName: string, force = false): Promise<WorkoutSession> {
  return request<WorkoutSession>('/api/workout/session/select', {
    method: 'POST',
    body: JSON.stringify({ date: dateStr, day_name: dayName, force }),
  });
}

export async function updateSet(
  dateStr: string, exerciseIndex: number, setNumber: number,
  field: string, value: any
): Promise<WorkoutSession> {
  const body: any = { date: dateStr, exercise_index: exerciseIndex, set_number: setNumber };
  body[field] = value;
  return request<WorkoutSession>('/api/workout/set/update', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function addSet(dateStr: string, exerciseIndex: number): Promise<WorkoutSession> {
  return request<WorkoutSession>('/api/workout/set/add', {
    method: 'POST',
    body: JSON.stringify({ date: dateStr, exercise_index: exerciseIndex }),
  });
}

export async function deleteSet(dateStr: string, exerciseIndex: number, setNumber: number): Promise<WorkoutSession> {
  return request<WorkoutSession>('/api/workout/set/delete', {
    method: 'POST',
    body: JSON.stringify({ date: dateStr, exercise_index: exerciseIndex, set_number: setNumber }),
  });
}

export async function duplicateSet(dateStr: string, exerciseIndex: number): Promise<WorkoutSession> {
  return request<WorkoutSession>('/api/workout/set/duplicate', {
    method: 'POST',
    body: JSON.stringify({ date: dateStr, exercise_index: exerciseIndex }),
  });
}

export async function moveExercise(dateStr: string, exerciseIndex: number, direction: 'up' | 'down'): Promise<WorkoutSession> {
  return request<WorkoutSession>('/api/workout/exercise/move', {
    method: 'POST',
    body: JSON.stringify({ date: dateStr, exercise_index: exerciseIndex, direction }),
  });
}

export async function updateExercise(
  dateStr: string, exerciseIndex: number,
  name?: string, notes?: string,
): Promise<WorkoutSession> {
  const body: Record<string, any> = { date: dateStr, exercise_index: exerciseIndex };
  if (name !== undefined) body.name = name;
  if (notes !== undefined) body.notes = notes;
  return request<WorkoutSession>('/api/workout/exercise/update', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function addExercise(
  dateStr: string, name: string,
  targetReps?: string, notes?: string, setsCount?: number,
): Promise<WorkoutSession> {
  const body: Record<string, any> = { date: dateStr, name };
  if (targetReps !== undefined) body.target_reps = targetReps;
  if (notes !== undefined) body.notes = notes;
  if (setsCount !== undefined) body.sets_count = setsCount;
  return request<WorkoutSession>('/api/workout/exercise/add', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function deleteExercise(dateStr: string, exerciseIndex: number): Promise<WorkoutSession> {
  return request<WorkoutSession>('/api/workout/exercise/delete', {
    method: 'POST',
    body: JSON.stringify({ date: dateStr, exercise_index: exerciseIndex }),
  });
}
