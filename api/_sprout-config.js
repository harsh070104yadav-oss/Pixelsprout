// Central configuration for Sprout XP, levels, quests and abuse limits.
// Change values here; the API computes everything from this file and the
// browser only ever displays what the API returns.
//
// All day/week boundaries are UTC. Weeks start Monday 00:00 UTC.

export const SPROUT = {
  // Seconds of visible, active play (and server-measured time since the game
  // page was opened) before a session counts as "qualifying".
  qualifyingSeconds: 60,

  // Fixed reward rules. Each is awarded at most once per its idempotency key.
  rewards: {
    first_session: 10,   // first qualifying game session ever (key: first_session)
    return_day: 25,      // qualifying session on a later UTC day (key: return:<day>)
    weekly_return: 75,   // qualifying sessions on 3 distinct days in one week (key: weekly_return:<week>)
  },
  weeklyReturnDays: 3,

  // Hard ceiling on XP a player can earn per UTC day (all sources combined).
  dailyXpCap: 300,

  // Levels by lifetime XP. Lifetime XP never resets.
  levels: [
    { level: 1, name: "Seed", min: 0 },
    { level: 2, name: "Sprout", min: 50 },
    { level: 3, name: "Sapling", min: 150 },
    { level: 4, name: "Bloom", min: 350 },
    { level: 5, name: "Canopy", min: 700 },
    { level: 6, name: "Evergreen", min: 1200 },
  ],

  // Quests are computed only from validated qualifying sessions.
  //   metric "new_games"        - games with no qualifying session before today/this period
  //   metric "categories"       - distinct categories in the period
  //   metric "sessions"         - qualifying sessions (one per game per day)
  //   metric "games"            - distinct games in the period
  //   metric "days"             - distinct active days in the period
  dailyQuests: [
    { id: "dq_new_game", title: "Play a game you have never played before", metric: "new_games", target: 1, xp: 15 },
    { id: "dq_new_category", title: "Explore a different category: play games from 2 categories today", metric: "categories", target: 2, xp: 15 },
    { id: "dq_session", title: "Complete a qualifying game session (60 seconds of play)", metric: "sessions", target: 1, xp: 10 },
  ],
  weeklyQuests: [
    { id: "wq_five_games", title: "Play five different games", metric: "games", target: 5, xp: 60 },
    { id: "wq_three_categories", title: "Explore three different categories", metric: "categories", target: 3, xp: 50 },
    { id: "wq_three_days", title: "Be active on three different days", metric: "days", target: 3, xp: 40 },
  ],

  leaderboardSize: 10,

  // Requests per fixed window. Exceeding any bucket returns 429.
  rateLimits: {
    windowSeconds: 60,
    perPlayer: 40,
    perNetwork: 120,   // IPv4 /24 or IPv6 /48, hashed - never stored raw
    perPlayerWrites: 20,
  },
};

export function levelFor(lifetimeXp) {
  const xp = Math.max(0, Number(lifetimeXp) || 0);
  const levels = SPROUT.levels;
  let i = 0;
  while (i + 1 < levels.length && xp >= levels[i + 1].min) i++;
  const cur = levels[i];
  const next = levels[i + 1] || null;
  return {
    level: cur.level,
    name: cur.name,
    min: cur.min,
    next: next ? { level: next.level, name: next.name, min: next.min } : null,
    into: xp - cur.min,
    span: next ? next.min - cur.min : 0,
    progress: next ? Math.min(1, (xp - cur.min) / (next.min - cur.min)) : 1,
  };
}
