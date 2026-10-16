-- Existing installations: apply AFTER private_journal.sql, as project owner.
-- No new roles, policies, or credentials. Atomic schema/function upgrade.
BEGIN;
alter table public.technical_cache add column if not exists collected_asof date;
update public.technical_cache set collected_asof=(bundle->>'asof')::date,
 completed_asof=null where bundle is not null;
create or replace function public.technical_targets(p_asof date) returns table(symbol text) language sql security definer set search_path=public as $$
 select targets.symbol from (select p.symbol from journal_purchases p union select r.symbol from technical_requests r where requested_at>now()-interval '7 days') targets
 left join technical_cache c on c.symbol=targets.symbol
 where c.completed_asof is null or c.completed_asof<p_asof
 or exists(select 1 from journal_purchases p where p.symbol=targets.symbol
           and not exists(select 1 from journal_signals s where s.user_id=p.user_id and s.symbol=p.symbol))
 order by c.attempted_at nulls first,targets.symbol limit 30
$$;

create or replace function public.publish_technical(p_symbol text,p_bundle jsonb,p_error text default null) returns void language plpgsql security definer set search_path=public as $$
begin
 insert into technical_cache(symbol,attempted_at,last_error) values(p_symbol,now(),p_error)
 on conflict(symbol) do update set attempted_at=now(),last_error=excluded.last_error;
 if p_bundle is not null and (p_bundle->>'structure_valid')::boolean and p_bundle->>'symbol'=p_symbol then
  update technical_cache set bundle=p_bundle,updated_at=now(),collected_asof=(p_bundle->>'asof')::date,
   completed_asof=case when coalesce((p_bundle->>'worker_verified')::boolean,false) then (p_bundle->>'asof')::date else null end,last_error=null
  where symbol=p_symbol and (bundle is null or (bundle->>'asof')::date<=(p_bundle->>'asof')::date)
  and (bundle is null or (bundle->>'asof')::date<(p_bundle->>'asof')::date or bundle->>'finality'<>'VERIFIED' or p_bundle->>'finality'='VERIFIED');
 else
  update technical_cache set last_error=coalesce(p_error,'Data validation failed; prior cache retained') where symbol=p_symbol;
 end if;
end $$;
COMMIT;
