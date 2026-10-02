-- A third worked example, an organisation that is winding down: Harbour Clinic.
--
-- `health` and `finance` are running organisations. This one exists so closing an
-- organisation can be shown, and tested, without closing either of them. It is
-- small on purpose: three people, one department, no workloads.
--
-- Written idempotently so it can be applied to a running database by hand:
--
--   Get-Content .\infra\postgres\seed-harbour.sql -Raw |
--     docker compose exec -T postgres psql -U munitas -d platform -f -
--
-- Its data is added by scripts/seed/seed-harbour-example.py, which puts real
-- files into its storage so a deletion has something to remove.

insert into tenant (id, isolation_level, key_ref, purpose, note)
values ('harbour', 'shared', 'key/harbour', 'production',
        'A small clinic that is winding down, the worked example for closing an organisation')
on conflict (id) do update set note = excluded.note;

-- Dunmore decides who may read the clinic's records. Adeyemi is the clinic's data
-- protection officer. Quinn is an ordinary member with no decision to make.
insert into directory (id, tenant_id, label, kind, roles) values
  ('cust-dunmore', 'harbour', 'Dunmore', 'human', '{data_custodian}'),
  ('dpo-adeyemi',  'harbour', 'Adeyemi', 'human', '{dpo}'),
  ('ana-quinn',    'harbour', 'Quinn',   'human', '{analyst}')
on conflict (id) do nothing;

insert into department (id, tenant_id, name, custodian) values
  ('d0000000-0000-4000-8000-000000000031', 'harbour', 'Patient Records', 'cust-dunmore')
on conflict (tenant_id, name) do nothing;
