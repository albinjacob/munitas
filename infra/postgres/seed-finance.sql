-- A second worked example, in a different industry, in tenant `finance`.
--
-- `health` (seed-organisation.sql) is a hospital: Cardiology, Radiology,
-- consultation audio. Every screenshot, every walkthrough, and every
-- example anyone reading this repository sees is healthcare, which reads
-- as "a healthcare tool" rather than "a governance platform that happens
-- to have a healthcare example." This file exists to make that untrue: a
-- card-payments company, a fraud operations team, transaction records
-- instead of consultation audio, and the exact same mechanism underneath
-- with nothing platform-specific changed for it.
--
-- Same shape as seed-organisation.sql on purpose: a tenant row, a handful
-- of people with roles from platform/policy/access.rego's role_floor, two
-- departments each with their own custodian, a project, and the workloads
-- that will act inside them. Read seed-organisation.sql's own header first
-- if this file's choices don't make sense on their own; the reasoning
-- there (why departments exist, why directory.id is global, why workloads
-- have no login) applies here unchanged.
--
-- Written idempotently so it can be applied to a running database by hand:
--
--   Get-Content .\infra\postgres\seed-finance.sql -Raw |
--     docker compose exec -T postgres psql -U munitas -d platform -f -

insert into tenant (id, isolation_level, key_ref, purpose, note)
values ('finance', 'shared', 'key/finance', 'production', 'A card-payments company, the second worked example, in a different industry')
on conflict (id) do update set note = excluded.note;

-- The people. 'analyst' gets used here for the first time in either seed
-- file -- health's closest equivalent, Sam, holds 'notebook_explore'
-- instead, so this also exercises a role the healthcare example never
-- does.
insert into directory (id, tenant_id, label, kind, roles) values
  ('cust-marcus', 'finance', 'Marcus', 'human', '{data_custodian}'),
  ('cust-naomi',  'finance', 'Naomi',  'human', '{data_custodian}'),
  ('eng-lena',    'finance', 'Lena',   'human', '{pipeline_operator}'),
  ('ana-omar',    'finance', 'Omar',   'human', '{analyst}'),
  -- Seeded for the same reason health and canary each seed one: the role
  -- exists platform-wide, not per-industry, so finance holds it too even
  -- though it has no agents yet -- the day it registers one, somebody
  -- already holds the role that can approve its requested hosts.
  ('arch-sofia',  'finance', 'Sofia',  'human', '{network_architect}')
on conflict (id) do nothing;

-- Two departments, the same reason health has two: Marcus cannot approve
-- access to Risk & Compliance's data and Naomi cannot approve access to
-- Fraud Operations's, and that is something to watch happen, not just
-- read about.
insert into department (id, tenant_id, name, custodian) values
  ('d0000000-0000-4000-8000-000000000021', 'finance', 'Fraud Operations',    'cust-marcus'),
  ('d0000000-0000-4000-8000-000000000022', 'finance', 'Risk & Compliance',   'cust-naomi')
on conflict (tenant_id, name) do nothing;

insert into project (id, tenant_id, name, purpose, lead, starts_at, ends_at) values
  ('c0000000-0000-4000-8000-000000000021', 'finance', 'Card fraud detection model',
   'training a model to flag anomalous card transactions',
   'ana-omar', now(), now() + interval '180 days')
on conflict (tenant_id, name) do nothing;

-- The workloads. Same reasoning as seed-organisation.sql's own: no
-- agent_runtime identity here, because registering an agent creates its
-- own and a pre-seeded one would sit unclaimed.
insert into directory (id, tenant_id, label, kind, roles) values
  ('finance-pipeline',   'finance', 'Fraud triage pipeline', 'workload', '{pipeline_action}'),
  ('svc-fraud-scorer',   'finance', 'Fraud scoring job',     'workload', '{training_job}')
on conflict (id) do nothing;
