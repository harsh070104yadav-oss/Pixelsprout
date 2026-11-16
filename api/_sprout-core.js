// Pure reward logic for Sprout XP: no I/O, so it can be unit-tested.
// Input is the object returned by the sprout_state() database function.
import { SPROUT, levelFor } from "./_sprout-config.js";

const DAY_MS = 86400000;

function isoDay(d) { return new Date(d).toISOString().slice(0, 10); }
function addDaysIso(day, n) { return isoDay(Date.parse(day + "T00:00:00Z") + n * DAY_MS); }

export function metricsFor(state) {
  const sessions = Array.isArray(state.week_sessions) ? state.week_sessions : [];
  const today = sessions.filter((s) => s.day === state.day);
  const uniq = (arr) => new Set(arr).size;
  return {
    daily: {
      new_games: uniq(today.filter((s) => s.new_game).map((s) => s.slug)),
      categories: uniq(today.map((s) => s.category)),
      sessions: today.length,
      games: uniq(today.map((s) => s.slug)),
      days: today.length ? 1 : 0,
    },
    weekly: {
      new_games: uniq(sessions.filter((s) => s.new_game).map((s) => s.slug)),
      categories: uniq(sessions.map((s) => s.category)),
      sessions: sessions.length,
      games: uniq(sessions.map((s) => s.slug)),
      days: uniq(sessions.map((s) => s.day)),
    },
    todaySessions: today.length,
    weekDays: [...new Set(sessions.map((s) => s.day))].sort(),
  };
}

function questView(list, values, keys, prefix, period) {
  return list.map((q) => {
    const progress = Math.min(q.target, values[q.metric] || 0);
    const key = `${prefix}:${q.id}:${period}`;
    return { id: q.id, title: q.title, xp: q.xp, target: q.target, progress, done: keys.has(key), ready: progress >= q.target };
  });
}

// Returns the awards that are now due and not yet in the ledger. The
// database still enforces idempotency; this only decides what to attempt.
export function dueAwards(state) {
  if (!state || !state.exists) return [];
  const keys = new Set(state.keys || []);
  const m = metricsFor(state);
  const out = [];
  const add = (idem, reason, xp, label) => { if (!keys.has(idem) && xp > 0) out.push({ idem, reason, xp, label }); };

  if (m.todaySessions > 0 || state.has_prior_day) add("first_session", "first_session", SPROUT.rewards.first_session, "First game session");
  if (m.todaySessions > 0 && state.has_prior_day) add(`return:${state.day}`, "return_day", SPROUT.rewards.return_day, "Welcome back");
  if (m.weekDays.length >= SPROUT.weeklyReturnDays) add(`weekly_return:${state.week}`, "weekly_return", SPROUT.rewards.weekly_return, "Weekly return bonus");
  for (const q of SPROUT.dailyQuests) {
    if ((m.daily[q.metric] || 0) >= q.target) add(`dq:${q.id}:${state.day}`, "daily_quest", q.xp, q.title);
  }
  for (const q of SPROUT.weeklyQuests) {
    if ((m.weekly[q.metric] || 0) >= q.target) add(`wq:${q.id}:${state.week}`, "weekly_quest", q.xp, q.title);
  }
  return out;
}

// The public, display-only view of a player's progress.
export function progressView(state) {
  const weekResets = addDaysIso(state.week, 7) + "T00:00:00Z";
  const dayResets = addDaysIso(state.day, 1) + "T00:00:00Z";
  const base = {
    day: state.day,
    week: state.week,
    daily_resets_at: dayResets,
    weekly_resets_at: weekResets,
    rules: {
      qualifying_seconds: SPROUT.qualifyingSeconds,
      first_session: SPROUT.rewards.first_session,
      return_day: SPROUT.rewards.return_day,
      weekly_return: SPROUT.rewards.weekly_return,
      weekly_return_days: SPROUT.weeklyReturnDays,
      daily_cap: SPROUT.dailyXpCap,
      levels: SPROUT.levels,
    },
  };
  if (!state.exists) {
    return {
      ...base,
      player: null,
      level: levelFor(0),
      lifetime_xp: 0,
      weekly_xp: 0,
      today_xp: 0,
      active: false,
      week_progress: { days: [], count: 0, target: SPROUT.weeklyReturnDays, bonus_xp: SPROUT.rewards.weekly_return, bonus_awarded: false },
      comeback: { xp: SPROUT.rewards.return_day, available: false, claimed_today: false },
      quests: {
        daily: questView(SPROUT.dailyQuests, {}, new Set(), "dq", state.day),
        weekly: questView(SPROUT.weeklyQuests, {}, new Set(), "wq", state.week),
      },
      recent: [],
      favorites: [],
    };
  }
  const keys = new Set(state.keys || []);
  const m = metricsFor(state);
  return {
    ...base,
    player: { name: state.display_name },
    level: levelFor(state.lifetime_xp),
    lifetime_xp: state.lifetime_xp,
    weekly_xp: state.weekly_xp,
    today_xp: state.today_xp,
    active: Boolean(state.has_prior_day || m.weekDays.length || keys.has("first_session")),
    week_progress: {
      days: m.weekDays,
      count: m.weekDays.length,
      target: SPROUT.weeklyReturnDays,
      bonus_xp: SPROUT.rewards.weekly_return,
      bonus_awarded: keys.has(`weekly_return:${state.week}`),
    },
    comeback: {
      xp: SPROUT.rewards.return_day,
      available: Boolean(state.has_prior_day),
      claimed_today: keys.has(`return:${state.day}`),
    },
    quests: {
      daily: questView(SPROUT.dailyQuests, m.daily, keys, "dq", state.day),
      weekly: questView(SPROUT.weeklyQuests, m.weekly, keys, "wq", state.week),
    },
    recent: Array.isArray(state.recent) ? state.recent.slice(0, 20) : [],
    favorites: Array.isArray(state.favorites) ? state.favorites : [],
  };
}

export function leaderboardView(lb) {
  const withLevel = (row) => ({ rank: row.rank, name: row.name, xp: row.xp, level: levelFor(row.lifetime_xp).name, you: Boolean(row.you) });
  return {
    week: lb.week,
    resets_at: addDaysIso(lb.week, 7) + "T00:00:00Z",
    players: Number(lb.players) || 0,
    top: (lb.top || []).map(withLevel),
    me: lb.me ? { rank: Number(lb.me.rank), xp: lb.me.xp } : null,
  };
}
