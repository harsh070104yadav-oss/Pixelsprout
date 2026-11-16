-- Pixelsprout: anonymous player progress (Sprout XP, quests, weekly league,
-- favourites, recently played).
--
-- Run once in the Supabase SQL editor (same place as the earlier kv_store /
-- game_views setup). Safe to re-run: every statement is idempotent.
--
-- Design notes
-- * A player is identified only by player_key = SHA-256 (hex) of the random
--   token stored in the visitor's HttpOnly "ps_pid" cookie. The raw token is
--   never stored, so a database leak cannot be replayed as a cookie.
-- * No personal data: no email, no IP address, no user agent, no fingerprint.
-- * All tables have RLS enabled with NO policies and are revoked from the
--   anon/authenticated roles, so only the server-side service_role (used by
--   /api/sprout, never sent to the browser) can read or write them.
-- * XP is only ever written by sprout_award(), which is idempotent per
--   (player_key, idem_key) and enforces the daily cap under a row lock.
-- * All day/week boundaries are UTC. Weeks start Monday 00:00 UTC.

-- ---------------------------------------------------------------- tables --

create table if not exists sprout_players (
  player_key   text primary key check (player_key ~ '^[0-9a-f]{64}$'),
  display_name text not null check (char_length(display_name) between 3 and 32),
  lifetime_xp  integer not null default 0 check (lifetime_xp >= 0),
  created_at   timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);

-- Recently opened game pages (latest 20 per player). last_open_at is also the
-- server-side start time used to validate a 60-second qualifying session.
create table if not exists sprout_recent (
  player_key   text not null references sprout_players(player_key) on delete cascade,
  game_slug    text not null check (game_slug ~ '^[a-z0-9-]{1,120}$'),
  last_open_at timestamptz not null default now(),
  primary key (player_key, game_slug)
);
create index if not exists sprout_recent_player_time on sprout_recent (player_key, last_open_at desc);

-- One row per player, per game, per UTC day once a session qualified
-- (>= 60 s of visible, active play). Source of truth for quests/comebacks.
create table if not exists sprout_sessions (
  player_key   text not null references sprout_players(player_key) on delete cascade,
  game_slug    text not null check (game_slug ~ '^[a-z0-9-]{1,120}$'),
  category     text not null check (char_length(category) between 1 and 60),
  day          date not null,
  week         date not null,
  qualified_at timestamptz not null default now(),
  primary key (player_key, game_slug, day)
);
create index if not exists sprout_sessions_player_week on sprout_sessions (player_key, week);
create index if not exists sprout_sessions_player_slug on sprout_sessions (player_key, game_slug, day);

-- Idempotent XP ledger. idem_key examples: first_session, return:2026-11-16,
-- weekly_return:2026-11-16, dq:dq_new_game:2026-11-16, wq:wq_five_games:2026-11-16
create table if not exists sprout_xp_ledger (
  id           bigserial primary key,
  player_key   text not null references sprout_players(player_key) on delete cascade,
  idem_key     text not null check (idem_key ~ '^[a-z0-9_:-]{3,80}$'),
  reason       text not null check (reason ~ '^[a-z0-9_]{2,40}$'),
  requested_xp integer not null check (requested_xp between 0 and 1000),
  xp           integer not null check (xp between 0 and 1000),
  day          date not null,
  week         date not null,
  created_at   timestamptz not null default now(),
  unique (player_key, idem_key)
);
create index if not exists sprout_ledger_player_day on sprout_xp_ledger (player_key, day);

-- Weekly league points (lifetime XP lives on sprout_players and never resets).
create table if not exists sprout_weekly (
  player_key text not null references sprout_players(player_key) on delete cascade,
  week       date not null,
  xp         integer not null default 0 check (xp >= 0),
  updated_at timestamptz not null default now(),
  primary key (player_key, week)
);
create index if not exists sprout_weekly_rank on sprout_weekly (week, xp desc, updated_at asc);

create table if not exists sprout_favorites (
  player_key text not null references sprout_players(player_key) on delete cascade,
  game_slug  text not null check (game_slug ~ '^[a-z0-9-]{1,120}$'),
  created_at timestamptz not null default now(),
  primary key (player_key, game_slug)
);

-- Fixed-window rate-limit counters. bucket values are hashed by the API.
create table if not exists sprout_rate (
  bucket       text not null check (char_length(bucket) <= 80),
  window_start timestamptz not null,
  hits         integer not null default 0,
  primary key (bucket, window_start)
);

-- ------------------------------------------------------ least privilege --

alter table sprout_players   enable row level security;
alter table sprout_recent    enable row level security;
alter table sprout_sessions  enable row level security;
alter table sprout_xp_ledger enable row level security;
alter table sprout_weekly    enable row level security;
alter table sprout_favorites enable row level security;
alter table sprout_rate      enable row level security;

do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on sprout_players, sprout_recent, sprout_sessions, sprout_xp_ledger, sprout_weekly, sprout_favorites, sprout_rate from %I', r);
      execute format('revoke all on sequence sprout_xp_ledger_id_seq from %I', r);
    end if;
  end loop;
end $$;

-- ------------------------------------------------------------- helpers --

create or replace function sprout_today() returns date
language sql stable set search_path = public as $$
  select (now() at time zone 'utc')::date
$$;

create or replace function sprout_week_start(d date) returns date
language sql immutable set search_path = public as $$
  select (d - ((extract(isodow from d)::int - 1)))::date
$$;

create or replace function sprout_new_name() returns text
language plpgsql volatile set search_path = public as $$
declare
  a text[] := array['Sprout','Pixel','Moss','Fern','Clover','Willow','Maple','Cedar','Acorn','Leafy','Bramble','Petal','Sunny','Misty','Pebble','Juniper'];
  b text[] := array['Explorer','Runner','Seeker','Ranger','Hopper','Scout','Player','Voyager','Rover','Dasher','Wanderer','Climber'];
begin
  if random() < 0.35 then
    return a[1 + floor(random() * array_length(a, 1))::int] || (100 + floor(random() * 900))::int;
  end if;
  return a[1 + floor(random() * array_length(a, 1))::int]
      || b[1 + floor(random() * array_length(b, 1))::int]
      || (10 + floor(random() * 990))::int;
end $$;

-- ------------------------------------------------------------------ RPC --

-- Records that a game page was opened. Creates the anonymous player row on
-- first use. Returns {created, display_name}.
create or replace function sprout_open(p_player text, p_slug text)
returns jsonb language plpgsql volatile set search_path = public as $$
declare v_created boolean := false; v_name text;
begin
  insert into sprout_players (player_key, display_name)
  values (p_player, sprout_new_name())
  on conflict (player_key) do nothing;
  v_created := found;
  update sprout_players set last_seen_at = now() where player_key = p_player returning display_name into v_name;

  insert into sprout_recent (player_key, game_slug, last_open_at)
  values (p_player, p_slug, now())
  on conflict (player_key, game_slug) do update set last_open_at = excluded.last_open_at;

  delete from sprout_recent r
  where r.player_key = p_player
    and r.game_slug not in (select game_slug from sprout_recent
                            where player_key = p_player
                            order by last_open_at desc limit 20);

  return jsonb_build_object('created', v_created, 'display_name', v_name);
end $$;

-- Validates and records a qualifying session. The game page must have been
-- opened (sprout_open) at least p_min_secs ago according to the DATABASE
-- clock, so the browser cannot shorten the timer.
-- Returns {ok, reason?, new_session, day, week}.
create or replace function sprout_qualify(p_player text, p_slug text, p_category text, p_min_secs integer)
returns jsonb language plpgsql volatile set search_path = public as $$
declare v_open timestamptz; v_day date := sprout_today(); v_week date; v_new boolean;
begin
  v_week := sprout_week_start(v_day);
  select last_open_at into v_open from sprout_recent where player_key = p_player and game_slug = p_slug;
  if v_open is null then
    return jsonb_build_object('ok', false, 'reason', 'not_opened');
  end if;
  if v_open > now() - make_interval(secs => p_min_secs) then
    return jsonb_build_object('ok', false, 'reason', 'too_early');
  end if;
  insert into sprout_sessions (player_key, game_slug, category, day, week)
  values (p_player, p_slug, p_category, v_day, v_week)
  on conflict (player_key, game_slug, day) do nothing;
  v_new := found;
  update sprout_players set last_seen_at = now() where player_key = p_player;
  return jsonb_build_object('ok', true, 'new_session', v_new, 'day', v_day, 'week', v_week);
end $$;

-- The ONLY way XP is created. Idempotent per idem_key; daily cap enforced
-- while holding the player row lock, so concurrent requests cannot exceed it.
-- Returns {awarded, duplicate, xp, capped}.
create or replace function sprout_award(p_player text, p_idem text, p_reason text, p_xp integer, p_daily_cap integer)
returns jsonb language plpgsql volatile set search_path = public as $$
declare v_day date := sprout_today(); v_week date; v_today integer; v_grant integer;
begin
  v_week := sprout_week_start(v_day);
  if p_xp < 0 or p_xp > 1000 then raise exception 'invalid xp'; end if;
  perform 1 from sprout_players where player_key = p_player for update;
  if not found then return jsonb_build_object('awarded', false, 'reason', 'no_player'); end if;
  if exists (select 1 from sprout_xp_ledger where player_key = p_player and idem_key = p_idem) then
    return jsonb_build_object('awarded', false, 'duplicate', true);
  end if;
  select coalesce(sum(xp), 0) into v_today from sprout_xp_ledger where player_key = p_player and day = v_day;
  v_grant := greatest(0, least(p_xp, p_daily_cap - v_today));
  insert into sprout_xp_ledger (player_key, idem_key, reason, requested_xp, xp, day, week)
  values (p_player, p_idem, p_reason, p_xp, v_grant, v_day, v_week);
  if v_grant > 0 then
    update sprout_players set lifetime_xp = lifetime_xp + v_grant where player_key = p_player;
    insert into sprout_weekly (player_key, week, xp, updated_at) values (p_player, v_week, v_grant, now())
    on conflict (player_key, week) do update set xp = sprout_weekly.xp + excluded.xp, updated_at = now();
  end if;
  return jsonb_build_object('awarded', v_grant > 0, 'duplicate', false, 'xp', v_grant, 'capped', v_grant < p_xp);
end $$;

-- Everything the API needs to compute progress, in one round trip.
create or replace function sprout_state(p_player text)
returns jsonb language plpgsql stable set search_path = public as $$
declare v_day date := sprout_today(); v_week date; p record;
begin
  v_week := sprout_week_start(v_day);
  select display_name, lifetime_xp, created_at into p from sprout_players where player_key = p_player;
  if not found then return jsonb_build_object('exists', false, 'day', v_day, 'week', v_week); end if;
  return jsonb_build_object(
    'exists', true,
    'day', v_day,
    'week', v_week,
    'display_name', p.display_name,
    'lifetime_xp', p.lifetime_xp,
    'weekly_xp', coalesce((select xp from sprout_weekly where player_key = p_player and week = v_week), 0),
    'today_xp', coalesce((select sum(xp) from sprout_xp_ledger where player_key = p_player and day = v_day), 0),
    'has_prior_day', exists (select 1 from sprout_sessions where player_key = p_player and day < v_day),
    'week_sessions', coalesce((
      select jsonb_agg(jsonb_build_object(
               'slug', s.game_slug, 'category', s.category, 'day', s.day,
               'new_game', not exists (select 1 from sprout_sessions o
                                       where o.player_key = p_player and o.game_slug = s.game_slug and o.day < s.day))
             order by s.qualified_at)
      from sprout_sessions s where s.player_key = p_player and s.week = v_week), '[]'::jsonb),
    'keys', coalesce((
      select jsonb_agg(idem_key) from sprout_xp_ledger
      where player_key = p_player and (week = v_week or idem_key = 'first_session')), '[]'::jsonb),
    'recent', coalesce((
      select jsonb_agg(game_slug order by last_open_at desc) from sprout_recent where player_key = p_player), '[]'::jsonb),
    'favorites', coalesce((
      select jsonb_agg(game_slug order by created_at desc) from sprout_favorites where player_key = p_player), '[]'::jsonb)
  );
end $$;

-- Weekly league. Exposes display names, XP and ranks only - never player keys.
create or replace function sprout_leaderboard(p_player text, p_limit integer)
returns jsonb language plpgsql stable set search_path = public as $$
declare v_week date := sprout_week_start(sprout_today()); v_me record; v_rank bigint;
begin
  select xp, updated_at into v_me from sprout_weekly where player_key = p_player and week = v_week and xp > 0;
  if found then
    select 1 + count(*) into v_rank from sprout_weekly w
    where w.week = v_week and w.xp > 0
      and (w.xp > v_me.xp or (w.xp = v_me.xp and w.updated_at < v_me.updated_at));
  end if;
  return jsonb_build_object(
    'week', v_week,
    'players', (select count(*) from sprout_weekly where week = v_week and xp > 0),
    'top', coalesce((
      select jsonb_agg(jsonb_build_object('rank', t.rn, 'name', t.display_name, 'xp', t.xp,
                                          'lifetime_xp', t.lifetime_xp, 'you', t.player_key = p_player) order by t.rn)
      from (select w.player_key, w.xp, pl.display_name, pl.lifetime_xp,
                   row_number() over (order by w.xp desc, w.updated_at asc) as rn
            from sprout_weekly w join sprout_players pl using (player_key)
            where w.week = v_week and w.xp > 0
            order by w.xp desc, w.updated_at asc
            limit greatest(1, least(p_limit, 50))) t), '[]'::jsonb),
    'me', case when v_rank is null then null else jsonb_build_object('rank', v_rank, 'xp', v_me.xp) end
  );
end $$;

create or replace function sprout_set_favorite(p_player text, p_slug text, p_on boolean)
returns jsonb language plpgsql volatile set search_path = public as $$
begin
  if p_on then
    insert into sprout_players (player_key, display_name) values (p_player, sprout_new_name())
    on conflict (player_key) do nothing;
    if (select count(*) from sprout_favorites where player_key = p_player) >= 200 then
      return jsonb_build_object('ok', false, 'reason', 'limit');
    end if;
    insert into sprout_favorites (player_key, game_slug) values (p_player, p_slug)
    on conflict do nothing;
  else
    delete from sprout_favorites where player_key = p_player and game_slug = p_slug;
  end if;
  return jsonb_build_object('ok', true);
end $$;

-- Merges favourites/recent that this browser kept in localStorage before
-- server sync existed (or before its cookie changed). Never removes rows.
create or replace function sprout_import(p_player text, p_favorites text[], p_recent text[])
returns jsonb language plpgsql volatile set search_path = public as $$
declare i integer; n integer;
begin
  insert into sprout_players (player_key, display_name) values (p_player, sprout_new_name())
  on conflict (player_key) do nothing;
  n := least(coalesce(array_length(p_favorites, 1), 0), 200);
  for i in 1..n loop
    exit when (select count(*) from sprout_favorites where player_key = p_player) >= 200;
    if p_favorites[i] ~ '^[a-z0-9-]{1,120}$' then
      insert into sprout_favorites (player_key, game_slug, created_at)
      values (p_player, p_favorites[i], now() - make_interval(secs => 86400 + i))
      on conflict do nothing;
    end if;
  end loop;
  n := least(coalesce(array_length(p_recent, 1), 0), 20);
  -- Imported rows are back-dated by at least a day and never overwrite a
  -- real open, so they can never satisfy (or reset) the qualifying timer.
  for i in 1..n loop
    if p_recent[i] ~ '^[a-z0-9-]{1,120}$' then
      insert into sprout_recent (player_key, game_slug, last_open_at)
      values (p_player, p_recent[i], now() - interval '1 day' - make_interval(secs => i))
      on conflict do nothing;
    end if;
  end loop;
  delete from sprout_recent r
  where r.player_key = p_player
    and r.game_slug not in (select game_slug from sprout_recent
                            where player_key = p_player
                            order by last_open_at desc limit 20);
  return jsonb_build_object('ok', true);
end $$;

-- "Forget me": deletes every row for this player (cascades).
create or replace function sprout_forget(p_player text)
returns jsonb language sql volatile set search_path = public as $$
  with d as (delete from sprout_players where player_key = p_player returning 1)
  select jsonb_build_object('deleted', (select count(*) from d))
$$;

-- Fixed-window counters for several buckets at once; returns hit counts in
-- the same order. Old windows are swept opportunistically.
create or replace function sprout_rate_hit(p_buckets text[], p_window_secs integer)
returns integer[] language plpgsql volatile set search_path = public as $$
declare v_start timestamptz; v_out integer[] := '{}'; v_hits integer; b text;
begin
  v_start := to_timestamp(floor(extract(epoch from now()) / p_window_secs) * p_window_secs);
  foreach b in array p_buckets loop
    insert into sprout_rate (bucket, window_start, hits) values (b, v_start, 1)
    on conflict (bucket, window_start) do update set hits = sprout_rate.hits + 1
    returning hits into v_hits;
    v_out := v_out || v_hits;
  end loop;
  if random() < 0.02 then
    delete from sprout_rate where window_start < now() - interval '2 hours';
  end if;
  return v_out;
end $$;

-- Only the server (service_role) may call these.
do $$
declare f text; r text;
begin
  foreach f in array array[
    'sprout_today()', 'sprout_week_start(date)', 'sprout_new_name()',
    'sprout_open(text,text)', 'sprout_qualify(text,text,text,integer)',
    'sprout_award(text,text,text,integer,integer)', 'sprout_state(text)',
    'sprout_leaderboard(text,integer)', 'sprout_set_favorite(text,text,boolean)',
    'sprout_import(text,text[],text[])', 'sprout_forget(text)', 'sprout_rate_hit(text[],integer)'] loop
    execute format('revoke all on function %s from public', f);
    foreach r in array array['anon', 'authenticated'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('revoke all on function %s from %I', f, r);
      end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
      execute format('grant execute on function %s to service_role', f);
    end if;
  end loop;
end $$;

-- PostgREST caches the schema; ask it to reload so the RPCs are callable now.
notify pgrst, 'reload schema';
