-- Run once in a NEW Supabase project's SQL Editor. Never put trades in GitHub.
create table if not exists public.journal_purchases (
 id uuid primary key default gen_random_uuid(), user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
 symbol text not null check(symbol ~ '^[0-9]{6}$'), buy_date date not null,
 average_price numeric(20,4) not null check(average_price>0 and average_price<=1000000000),
 quantity bigint not null check(quantity>0 and quantity<=1000000000),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(), revision bigint not null default 1,
 check(buy_date <= (now() at time zone 'Asia/Seoul')::date)
);
create table if not exists public.journal_audit (
 id bigint generated always as identity primary key,user_id uuid not null references auth.users(id) on delete cascade,
 purchase_id uuid not null,action text not null,old_record jsonb,new_record jsonb,changed_at timestamptz not null default now()
);
create table if not exists public.technical_requests (
 user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,symbol text not null check(symbol ~ '^[0-9]{6}$'),
 requested_at timestamptz not null default now(),primary key(user_id,symbol)
);
create table if not exists public.technical_cache (
 symbol text primary key check(symbol ~ '^[0-9]{6}$'), bundle jsonb,
 updated_at timestamptz,attempted_at timestamptz,last_error text, completed_asof date
);
create table if not exists public.journal_risk_state (
 user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
 symbol text not null check(symbol ~ '^[0-9]{6}$'),trailing_stop numeric not null check(trailing_stop>=0),activated boolean not null default false,
 asof date not null,primary key(user_id,symbol)
);
alter table public.journal_purchases enable row level security;
alter table public.journal_audit enable row level security;
alter table public.technical_requests enable row level security;
alter table public.technical_cache enable row level security;
alter table public.journal_risk_state enable row level security;
revoke all on public.journal_purchases,public.journal_audit,public.technical_requests,public.technical_cache,public.journal_risk_state from anon;
grant select,insert,update,delete on public.journal_purchases to authenticated;
grant select on public.journal_audit,public.technical_cache,public.journal_risk_state to authenticated;
grant select,insert,update on public.technical_requests to authenticated;
create policy purchases_owner on public.journal_purchases for all to authenticated using(user_id=auth.uid()) with check(user_id=auth.uid());
create policy audit_owner on public.journal_audit for select to authenticated using(user_id=auth.uid());
create policy requests_owner on public.technical_requests for all to authenticated using(user_id=auth.uid()) with check(user_id=auth.uid());
create policy risk_owner on public.journal_risk_state for select to authenticated using(user_id=auth.uid());
create policy cache_requested on public.technical_cache for select to authenticated using(
 exists(select 1 from public.technical_requests r where r.user_id=auth.uid() and r.symbol=technical_cache.symbol)
 or exists(select 1 from public.journal_purchases p where p.user_id=auth.uid() and p.symbol=technical_cache.symbol));

create or replace function public.journal_before_change() returns trigger language plpgsql set search_path=public as $$
begin
 if TG_OP='UPDATE' then
  if new.user_id<>old.user_id or new.symbol<>old.symbol then raise exception 'Owner/symbol immutable'; end if;
  new.updated_at=now();new.revision=old.revision+1;
 end if;
 return new;
end $$;
create trigger purchase_revision before update on public.journal_purchases for each row execute function public.journal_before_change();
create or replace function public.journal_audit_change() returns trigger language plpgsql security definer set search_path=public as $$
begin
 insert into journal_audit(user_id,purchase_id,action,old_record,new_record)
 values(coalesce(new.user_id,old.user_id),coalesce(new.id,old.id),TG_OP,to_jsonb(old),to_jsonb(new));
 if TG_OP='DELETE' and not exists(select 1 from journal_purchases where user_id=old.user_id and symbol=old.symbol) then
  delete from journal_risk_state where user_id=old.user_id and symbol=old.symbol;
 end if;
 return coalesce(new,old);
end $$;
create trigger purchase_audit after insert or update or delete on public.journal_purchases for each row execute function public.journal_audit_change();

create or replace function public.request_technical(p_symbol text) returns void language plpgsql security definer set search_path=public as $$
begin
 if auth.uid() is null or p_symbol !~ '^[0-9]{6}$' then raise exception 'Not authorized';end if;
 if (select count(*) from technical_requests where user_id=auth.uid() and requested_at>now()-interval '1 day')>=30
 and not exists(select 1 from technical_requests where user_id=auth.uid() and symbol=p_symbol) then raise exception 'Daily request limit';end if;
 insert into technical_requests(user_id,symbol) values(auth.uid(),p_symbol)
 on conflict(user_id,symbol) do update set requested_at=case when technical_requests.requested_at<now()-interval '1 day' then now() else technical_requests.requested_at end;
end $$;
create or replace function public.save_risk_state(p_symbol text,p_trailing numeric,p_activated boolean,p_asof date) returns void language plpgsql security definer set search_path=public as $$
begin
 if auth.uid() is null or p_trailing<0 or not exists(select 1 from journal_purchases where user_id=auth.uid() and symbol=p_symbol) then raise exception 'Not authorized';end if;
 insert into journal_risk_state(user_id,symbol,trailing_stop,activated,asof) values(auth.uid(),p_symbol,p_trailing,p_activated,p_asof)
 on conflict(user_id,symbol) do update set trailing_stop=greatest(journal_risk_state.trailing_stop,excluded.trailing_stop),activated=journal_risk_state.activated or excluded.activated,asof=greatest(journal_risk_state.asof,excluded.asof);
end $$;
create or replace function public.technical_targets(p_asof date) returns table(symbol text) language sql security definer set search_path=public as $$
 select distinct targets.symbol from (select p.symbol from journal_purchases p union select r.symbol from technical_requests r where requested_at>now()-interval '7 days') targets
 left join technical_cache c on c.symbol=targets.symbol
 where c.completed_asof is null or c.completed_asof<p_asof order by targets.symbol limit 30
$$;
create or replace function public.publish_technical(p_symbol text,p_bundle jsonb,p_error text default null) returns void language plpgsql security definer set search_path=public as $$
begin
 insert into technical_cache(symbol,attempted_at,last_error) values(p_symbol,now(),p_error)
 on conflict(symbol) do update set attempted_at=now(),last_error=excluded.last_error;
 if p_bundle is not null and (p_bundle->>'structure_valid')::boolean and p_bundle->>'symbol'=p_symbol then
  update technical_cache set bundle=p_bundle,updated_at=now(),completed_asof=(p_bundle->>'asof')::date,last_error=null
  where symbol=p_symbol and (bundle is null or (bundle->>'asof')::date<=(p_bundle->>'asof')::date)
  and (bundle is null or bundle->>'finality'<>'VERIFIED' or p_bundle->>'finality'='VERIFIED');
 else
  update technical_cache set last_error=coalesce(p_error,'Data validation failed; prior cache retained') where symbol=p_symbol;
 end if;
end $$;
revoke all on function public.request_technical(text),public.save_risk_state(text,numeric,boolean,date),public.technical_targets(date),public.publish_technical(text,jsonb,text) from public,anon;
grant execute on function public.request_technical(text),public.save_risk_state(text,numeric,boolean,date) to authenticated;
revoke all on function public.technical_targets(date),public.publish_technical(text,jsonb,text) from authenticated;
grant execute on function public.technical_targets(date),public.publish_technical(text,jsonb,text) to service_role;
-- Close direct request writes: the bounded RPC is the only insertion path.
revoke insert,update on public.technical_requests from authenticated;

grant all on public.journal_purchases,public.journal_audit,public.technical_requests,public.technical_cache,public.journal_risk_state to service_role;

-- Private scheduled guidance results; never published to GitHub.
create table if not exists public.journal_signals (
 user_id uuid not null references auth.users(id) on delete cascade,symbol text not null,
 purchase_signature text not null,result jsonb not null,calculated_at timestamptz not null default now(),primary key(user_id,symbol)
);
alter table public.journal_signals enable row level security;
revoke all on public.journal_signals from anon,authenticated;
grant select on public.journal_signals to authenticated;
grant all on public.journal_signals to service_role;
create policy signals_owner on public.journal_signals for select to authenticated using(user_id=auth.uid());
create or replace function public.worker_positions(p_symbol text) returns table(user_id uuid,symbol text,buy_date date,average_price numeric,quantity numeric,purchase_signature text,risk jsonb) language sql security definer set search_path=public as $$
 select p.user_id,p.symbol,min(p.buy_date),sum(p.average_price*p.quantity)/sum(p.quantity),sum(p.quantity),md5(string_agg(p.id::text||':'||p.revision::text,',' order by p.id)),coalesce((select to_jsonb(r) from journal_risk_state r where r.user_id=p.user_id and r.symbol=p.symbol),'{}'::jsonb)
 from journal_purchases p where p.symbol=p_symbol group by p.user_id,p.symbol
$$;
create or replace function public.save_worker_signal(p_user uuid,p_symbol text,p_signature text,p_result jsonb) returns void language plpgsql security definer set search_path=public as $$
declare current_signature text;
begin
 select md5(string_agg(id::text||':'||revision::text,',' order by id)) into current_signature from journal_purchases where user_id=p_user and symbol=p_symbol;
 if current_signature is distinct from p_signature then return;end if;
 insert into journal_signals(user_id,symbol,purchase_signature,result) values(p_user,p_symbol,p_signature,p_result)
 on conflict(user_id,symbol) do update set purchase_signature=excluded.purchase_signature,result=excluded.result,calculated_at=now();
 if p_result ? 'trailing_stop' and p_result->>'asof' is not null then
  insert into journal_risk_state(user_id,symbol,trailing_stop,activated,asof) values(p_user,p_symbol,(p_result->>'trailing_stop')::numeric,(p_result->>'activated')::boolean,(p_result->>'asof')::date)
  on conflict(user_id,symbol) do update set trailing_stop=greatest(journal_risk_state.trailing_stop,excluded.trailing_stop),activated=journal_risk_state.activated or excluded.activated,asof=greatest(journal_risk_state.asof,excluded.asof);
 end if;
end $$;
revoke all on function public.worker_positions(text),public.save_worker_signal(uuid,text,text,jsonb) from public,anon,authenticated;
grant execute on function public.worker_positions(text),public.save_worker_signal(uuid,text,text,jsonb) to service_role;
