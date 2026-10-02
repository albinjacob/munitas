-- A worked example of a customer organisation, in tenant `health`.
--
-- This exists because the organisation was previously created by typing into
-- psql during testing, which meant it vanished on a volume reset while the
-- console carried on offering custodians the database had never heard of. Every
-- approval by one of those people would have been refused by a foreign key, and
-- the console would have looked broken rather than unseeded.
--
-- Everything here is invented. It is a demonstration organisation, not a
-- template: a real deployment registers its own departments and custodians, and
-- the platform should never invent who is accountable for clinical data.
--
-- Written idempotently so it can be applied to a running database by hand.
-- `docker-entrypoint-initdb.d` only executes on an empty data directory, so an
-- existing volume needs:
--
--   Get-Content .\infra\postgres\seed-organisation.sql -Raw |
--     docker compose exec -T postgres psql -U munitas -d platform -f -
--
--
-- Why `health` and not `t1`
-- ----------------------
-- The organisation used to share tenant `t1` with every verification run, so a
-- console listing datasets showed seventy test fixtures beside the two real
-- ones, and nothing could be cleaned up because sealed versions cannot be
-- deleted. The platform now runs four tenants:
--
--   health   this file. The organisation somebody logs in to.
--   canary   seed-canary.sql. Verification traffic, and only that.
--   finance  seed-finance.sql. A second worked example, deliberately not
--            healthcare, so the platform doesn't read as healthcare-specific.
--   t1       what was already on the machine. Retired: read-only, and not
--            offered in the console.
--
-- The people move rather than being copied, because `directory.id` is a single
-- primary key across every tenant and six foreign keys point at it. Moving them
-- keeps their ids, so nothing in the console, the tests or the policy has to
-- change, and every lease `t1` ever approved still resolves the name of whoever
-- approved it. That is the whole reason those foreign keys refuse to let a
-- directory entry be deleted.
--
-- Departments and projects do not move. `t1` holds seventy-odd datasets that
-- point at its departments, and repointing them at another tenant's department
-- would be a cross-tenant reference invented purely for tidiness. `health` gets
-- its own instead.

insert into tenant (id, isolation_level, key_ref, purpose, note)
values ('health', 'shared', 'key/health', 'production', 'A hospital, the worked example most screenshots and walkthroughs use')
on conflict (id) do update set note = excluded.note;

-- Move the people, if they are still in the old tenant. A no-op on a fresh
-- database, and a no-op the second time it is run.
--
-- Named one by one rather than moving everything in `t1`. Old verification runs
-- invented principals in that tenant before the scripts had one of their own,
-- and those belong to the tenant they were created in, not to the demonstration
-- organisation. A blanket move brought one of them across, which is how a test
-- fixture ends up on a customer's directory page.
update directory set tenant_id = 'health'
where tenant_id = 't1'
  and id in ('cust-hartley','cust-okonjo','dpo-nakamura','sam-researcher',
             'eng-devi','ops-priya','health-pipeline','svc-trainer',
             'annotation-tool');

-- The people. Roles match role_floor in platform/policy/access.rego; a role
-- here that the policy does not know would be denied for a confusing reason.
insert into directory (id, tenant_id, label, kind, roles) values
  ('cust-hartley',   'health', 'Hartley',  'human', '{data_custodian}'),
  ('cust-okonjo',    'health', 'Okonjo',   'human', '{data_custodian}'),
  ('dpo-nakamura',   'health', 'Nakamura', 'human', '{dpo}'),
  ('rev-imani',      'health', 'Imani',    'human', '{deid_reviewer}'),
  ('sam-researcher', 'health', 'Sam',      'human', '{notebook_explore}'),
  ('eng-devi',       'health', 'Devi',     'human', '{pipeline_operator}'),
  ('ops-priya',      'health', 'Priya',    'human', '{platform_admin,hybridops}'),
  -- A second platform administrator. A legal hold is recorded by one and approved by a
  -- different one, so the platform needs at least two.
  ('ops-ravi',       'health', 'Ravi',     'human', '{platform_admin,hybridops}'),
  ('cust-mensah',    'health', 'Mensah',   'human', '{data_custodian}'),
  ('cust-lindqvist', 'health', 'Lindqvist','human', '{data_custodian}'),
  -- A dedicated identity, the same pattern every other role here already
  -- follows: the role_grant/decide flow is meant to be how anyone gets a
  -- role after this, but it does not yet actually take effect
  -- (SESSION_STATUS.md), so at least one real holder is seeded, the same
  -- bootstrap every role needs before self-service can work at all.
  ('arch-turner',    'health', 'Turner',   'human', '{network_architect}')
on conflict (id) do nothing;

-- Four departments, each with its own custodian, so custodian scoping is
-- something a person can watch happen rather than only a policy test.
-- Hartley cannot approve access to Radiology's data and Okonjo cannot approve
-- access to Cardiology's. Oncology and Orthopaedics exist because the three
-- consultation recordings in docs/audio-samples/ are about cardiology,
-- oncology and orthopaedics, and each is seeded into the department it is
-- about (scripts/seed/seed-health-example.py).
insert into department (id, tenant_id, name, custodian) values
  ('d0000000-0000-4000-8000-000000000011', 'health', 'Cardiology', 'cust-hartley'),
  ('d0000000-0000-4000-8000-000000000012', 'health', 'Radiology',  'cust-okonjo'),
  ('d0000000-0000-4000-8000-000000000013', 'health', 'Oncology',   'cust-mensah'),
  ('d0000000-0000-4000-8000-000000000014', 'health', 'Orthopaedics', 'cust-lindqvist')
on conflict (tenant_id, name) do nothing;

-- A project is a named purpose with an end date, which is what stops `purpose`
-- being whatever somebody typed into a form.
insert into project (id, tenant_id, name, purpose, lead, starts_at, ends_at) values
  ('c0000000-0000-4000-8000-000000000011', 'health', 'Arrhythmia detection study',
   'training a model to flag irregular rhythms in consultation audio',
   'sam-researcher', now(), now() + interval '180 days')
on conflict (tenant_id, name) do nothing;

-- The workloads. They hold no approving role and appear here so that anything
-- naming a principal, including the audit log, can resolve them to a label.
--
-- No agent_runtime identity here on purpose. Registering an agent creates
-- its own, named after that agent, in the same call: see
-- platform/api/app/agents.py's register(). A pre-seeded one would sit
-- unclaimed until something registered against it, and nothing here does.
insert into directory (id, tenant_id, label, kind, roles) values
  ('health-pipeline', 'health', 'Pipeline action',  'workload', '{pipeline_action}'),
  ('svc-trainer',     'health', 'Training job',     'workload', '{training_job}'),
  ('annotation-tool', 'health', 'Annotation tool',  'workload', '{annotation_tool}')
on conflict (id) do nothing;

-- Close the old tenant, last, so nothing above trips over the write refusal.
-- Its datasets, versions, leases and audit records stay exactly where they are
-- and stay readable. Retiring is not deletion: it is the state a tenant is in
-- when nothing more should be written to it.
update tenant set purpose = 'retired' where id = 't1';
