-- Local PostgreSQL fixture only. Run bootstrap + schema + migration first.
insert into auth.users values ('00000000-0000-0000-0000-000000000001');
insert into journal_purchases(user_id,symbol,buy_date,average_price,quantity)
values ('00000000-0000-0000-0000-000000000001','999999','2020-01-01',100,1);
select publish_technical('999999','{"symbol":"999999","asof":"2020-10-06","structure_valid":true,"finality":"UNVERIFIED","worker_verified":false}'::jsonb);
do $$ begin
 if not exists(select 1 from technical_targets('2020-10-06')) then raise exception 'structural completion closed verification';end if;
 if (select collected_asof from technical_cache where symbol='999999') <> '2020-10-06'::date then raise exception 'collection date missing';end if;
end $$;
select publish_technical('999999','{"symbol":"999999","asof":"2020-10-06","structure_valid":true,"finality":"UNVERIFIED","worker_verified":true}'::jsonb);
select save_worker_signal(user_id,symbol,purchase_signature,'{"asof":"2020-10-06","status":"test"}') from worker_positions('999999');
do $$ begin
 if exists(select 1 from technical_targets('2020-10-06')) then raise exception 'verified unchanged job requeued';end if;
end $$;
create temporary table old_signature as select * from worker_positions('999999');
update journal_purchases set quantity=2 where symbol='999999';
select save_worker_signal(user_id,symbol,purchase_signature,'{"asof":"2020-10-06","status":"stale"}') from old_signature;
do $$ begin
 if exists(select 1 from journal_signals) then raise exception 'stale position result saved';end if;
 if not exists(select 1 from technical_targets('2020-10-06')) then raise exception 'position change not requeued';end if;
end $$;
select publish_technical('999999',null,'test failure');
do $$ begin
 if (select bundle from technical_cache where symbol='999999') is null then raise exception 'failure erased cache';end if;
end $$;
