-- The canary organisation: where verification runs, and nothing else.
--
-- Every verify script used to write into `t1`, the same tenant the demonstration
-- organisation lives in, so a console listing datasets showed seventy fixtures
-- beside two real ones and none of them could be removed. Sealed versions cannot
-- be deleted, which is the guarantee V1 exists to prove, so the answer was never
-- to clean up afterwards. It was to run the tests somewhere else.
--
-- A second tenant earns its keep for a better reason than tidiness. The platform
-- claims tenant isolation and has a policy test for cross-tenant denial, but
-- until there were two tenants carrying real traffic nothing exercised it at
-- run time. U28 does that, and it needs this file to exist.
--
-- `purpose` is `canary`, which is what makes these versions' objects eligible
-- for reclamation by scripts/admin/reclaim-storage.py. That frees the bytes and keeps the
-- rows: 81 versions of metadata came to 9 MB while their objects came to 502 MB, so the
-- bytes were the problem. The rows became one later, because they show in screens (a
-- custodian's queue lists the oldest 100 arrivals), so scripts/admin/tidy-canary.py removes
-- this tenant's old fixtures, rows and files, and this tenant's only. It is test-harness
-- housekeeping, not platform behaviour, and run-verification.ps1 runs it after each
-- recorded run.
--
-- Applied the same way as seed-organisation.sql:
--
--   Get-Content .\infra\postgres\seed-canary.sql -Raw |
--     docker compose exec -T postgres psql -U munitas -d platform -f -

insert into tenant (id, isolation_level, key_ref, purpose, note)
values ('canary', 'shared', 'key/canary', 'canary', 'Where the verification suite writes, so test data never mixes with real data')
on conflict (id) do update set note = excluded.note;

-- Named so nobody mistakes them for people. The demonstration organisation has
-- Hartley and Devi; this one has roles with a prefix, because a screenshot of
-- the canary tenant should be obvious at a glance.
--
-- The ids cannot be reused from `health`. `directory.id` is one primary key across
-- every tenant, so the same principal cannot exist in two of them, which is also
-- why verify/common.py keeps these ids beside the tenant rather than letting
-- each script name its own.
insert into directory (id, tenant_id, label, kind, roles) values
  ('canary-custodian',  'canary', 'Canary custodian',   'human', '{data_custodian}'),
  ('canary-elsewhere',  'canary', 'Canary custodian, other department',
                                                        'human', '{data_custodian}'),
  ('canary-engineer',   'canary', 'Canary engineer',    'human', '{pipeline_operator}'),
  ('canary-researcher', 'canary', 'Canary researcher',  'human', '{notebook_explore}'),
  ('canary-dpo',        'canary', 'Canary oversight',   'human', '{dpo}'),
  ('canary-risk-owner', 'canary', 'Canary AI risk owner', 'human', '{ai_risk_owner}'),
  ('canary-reviewer',   'canary', 'Canary reviewer',    'human', '{deid_reviewer}'),
  ('canary-auditor',    'canary', 'Canary privacy auditor', 'human', '{privacy_auditor}'),
  -- A dedicated identity, the same pattern every other role here already
  -- follows (canary-reviewer holds deid_reviewer and nothing else): the
  -- role_grant/decide flow is meant to be how anyone gets a role after
  -- this, but it does not yet actually take effect (SESSION_STATUS.md),
  -- so at least one real holder is seeded, the same bootstrap every role
  -- needs before self-service can work at all.
  ('canary-architect',  'canary', 'Canary architect',   'human', '{network_architect}'),
  -- The platform administrator the console tests act as. Health's administrator is for walkthroughs only; tests run here.
  ('canary-admin',      'canary', 'Canary administrator', 'human', '{platform_admin,hybridops}'),
  -- A second one, because closing an organisation or placing a legal hold takes two different administrators.
  ('canary-admin-second', 'canary', 'Canary administrator, second', 'human', '{platform_admin,hybridops}')
on conflict (id) do nothing;

insert into directory (id, tenant_id, label, kind, roles) values
  ('canary-pipeline',   'canary', 'Canary pipeline',    'workload', '{pipeline_action}'),
  ('canary-trainer',    'canary', 'Canary training job','workload', '{training_job}'),
  ('canary-agent',      'canary', 'Canary agent',       'workload', '{agent_runtime}'),
  ('canary-annotation', 'canary', 'Canary annotation',  'workload', '{annotation_tool}')
on conflict (id) do nothing;

-- Two departments with different custodians, because several checks turn on a
-- custodian being refused something outside their own department. One would let
-- those checks pass without ever exercising the scoping.
insert into department (id, tenant_id, name, custodian) values
  ('d0000000-0000-4000-8000-0000000000c1', 'canary', 'Verification', 'canary-custodian'),
  ('d0000000-0000-4000-8000-0000000000c2', 'canary', 'Elsewhere',    'canary-elsewhere')
on conflict (tenant_id, name) do nothing;

-- A second test organisation, `neighbour`. The checks that prove one organisation cannot reach another need somebody from somewhere else, and that used to
-- be the demonstration organisation (health), which is for walkthroughs only. Nothing here is a walkthrough: it holds a custodian, an engineer, a
-- researcher and an administrator, and what a check puts in it for itself.
insert into tenant (id, isolation_level, key_ref, purpose, note)
values ('neighbour', 'shared', 'key/neighbour', 'canary', 'Another test organisation, so a check can act as somebody from somewhere else')
on conflict (id) do update set note = excluded.note;

insert into directory (id, tenant_id, label, kind, roles) values
  ('neighbour-custodian',  'neighbour', 'Neighbour custodian',  'human', '{data_custodian}'),
  ('neighbour-engineer',   'neighbour', 'Neighbour engineer',   'human', '{pipeline_operator}'),
  ('neighbour-researcher', 'neighbour', 'Neighbour researcher', 'human', '{notebook_explore}'),
  ('neighbour-admin',      'neighbour', 'Neighbour administrator', 'human', '{platform_admin,hybridops}')
on conflict (id) do nothing;

insert into department (id, tenant_id, name, custodian) values
  ('d0000000-0000-4000-8000-0000000000c3', 'neighbour', 'Neighbouring', 'neighbour-custodian')
on conflict (tenant_id, name) do nothing;
