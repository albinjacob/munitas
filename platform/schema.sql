-- Munitas: platform metadata and lineage schema.
--
-- Two guarantees live in this file rather than in application code, on the
-- principle that the unsafe action should not be expressible:
--   1. A sealed dataset version cannot be updated (rule below).
--   2. A lease cannot be approved by the principal who holds it (check below).

create extension if not exists vector;

-- ---------------------------------------------------------------- tenancy --

create table if not exists tenant (
  id              text primary key,
  isolation_level text not null
                  check (isolation_level in ('shared','dedicated','byo')),
  key_ref         text not null,              -- tenant key id in the crypto module
  created_at      timestamptz not null default now()
);

-- What a tenant is for, which decides how it is treated rather than what it
-- holds.
--
--   production  a customer. Writable, offered in the console, never reclaimed.
--   canary      internal verification traffic. Writable, and its objects are
--               reclaimed on a schedule so test runs cannot fill the disk.
--   retired     closed. No writes, not offered, and its objects are reclaimable
--               once somebody asks for that explicitly.
--   scratch     declared disposable before it ever held anything. Everything
--               canary is, and deletable outright: the immutability rules
--               below do not apply to it, so a throwaway tenant can be
--               removed without switching those rules off.
--
-- `scratch` exists because the alternative was worse. The verification suite
-- mints a fresh tenant per run for the checks that can only be proved once
-- about an organisation, such as a bucket being created on its first write,
-- and something has to remove them or they accumulate until the storage
-- engine runs out of volumes. Removing one means deleting its sealed
-- versions, which used to mean disabling the rewrite rules that make a
-- sealed version immutable. A guarantee that gets switched off by whatever
-- tidies up is not a guarantee.
--
-- So disposability is declared at creation instead of enforced away at
-- deletion. The trade is stated plainly rather than hidden: the guarantee is
-- no longer "a sealed version can never be deleted" but "a sealed version
-- can never be deleted unless its organisation was declared disposable
-- before it held anything". The second clause is what the trigger below
-- enforces, and without it the first clause would be worthless, because
-- anybody could relabel a real organisation and delete its history.
--
-- `canary` deliberately keeps full immutability. verify/v1_immutability.py
-- proves the guarantee using a canary tenant, so exempting canary would
-- have left the platform's central claim untested by the only kind of
-- tenant the suite can create.
--
-- `retired` exists because a tenant cannot be deleted. Its sealed versions, the
-- leases approved inside it and the directory entries those approvals name are
-- all immutable on purpose, so a customer who leaves needs an honest state
-- rather than a row that quietly disappears. The records survive; the tenant is
-- shut.
--
-- The default is `production`, so a tenant nobody updates stays writable and
-- stays visible. A default that closed a tenant would let a migration take a
-- customer offline without anybody asking for it.
alter table tenant add column if not exists purpose text not null default 'production';

-- Dropped and recreated rather than added-if-absent. The `exception when
-- duplicate_object then null` form this used to take is idempotent for
-- creating a constraint and silently useless for changing one: re-applying
-- this file with a new list of purposes hit the duplicate and swallowed it,
-- leaving the old check in place while the file claimed otherwise. A schema
-- applied to a live database has to be idempotent about the end state, not
-- just about not erroring.
-- Why this organisation exists, in a sentence somebody reading a screen
-- would recognise. `purpose` above says how the platform treats it
-- (production, canary, retired, scratch), which is a different question and
-- is internal vocabulary: a console showing "canary" beside a customer's
-- name is the project's own tracker leaking onto a page somebody is trying
-- to work on. This is the human half, set at onboarding by whoever knows.
--
-- Null is honest for an organisation nobody wrote one for, and the screen
-- says so rather than inventing a description.
alter table tenant add column if not exists note text;

alter table tenant drop constraint if exists tenant_purpose_known;
alter table tenant add constraint tenant_purpose_known
  check (purpose in ('production','canary','retired','scratch'));

-- Disposability is declared, never acquired.
--
-- Without this, the exemption above would be an open door: relabel any
-- organisation `scratch` and its sealed history becomes deletable, which is
-- precisely the guarantee the rules exist to make. So a tenant may only be
-- called scratch while it holds nothing sealed. Say it at creation, when it
-- is empty and the claim is honest, or never.
--
-- Deliberately one-way in the other direction too: a scratch tenant may be
-- promoted out of scratch freely, because becoming *more* protected needs no
-- permission.
create or replace function refuse_late_disposability() returns trigger as $$
begin
  if new.purpose = 'scratch' and coalesce(old.purpose, '') <> 'scratch' then
    if exists (select 1 from dataset_version dv
                where dv.tenant_id = new.id and dv.sealed) then
      raise exception
        'tenant % already holds sealed versions, so it cannot be declared '
        'disposable now. Disposability is declared before an organisation '
        'holds anything, or not at all.', new.id;
    end if;
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists refuse_late_disposability_on_tenant on tenant;
create trigger refuse_late_disposability_on_tenant
  before insert or update on tenant
  for each row execute function refuse_late_disposability();

-- ------------------------------------------------------ the organisation --

-- Who is accountable for a dataset, and on whose authority a lease is granted.
--
-- The platform had an access model and no ownership model. It could enforce
-- that two names differed and could not say who was accountable, so an approval
-- was any string that was not the requester's own. These tables are what turn
-- separation of duties from a string comparison into a relationship.
--
-- The customer organisation is the data controller and this platform is a
-- processor, so nothing here is decided by the platform. It records the
-- customer's structure and enforces the decisions that follow from it.

-- A principal that exists. Every approver must appear here, which is what makes
-- an unregistered name refusable by the database rather than by a code path
-- somebody can edit.
create table if not exists directory (
  id          text primary key,
  tenant_id   text not null references tenant(id),
  label       text not null,
  kind        text not null check (kind in ('human','workload')),
  roles       text[] not null default '{}',
  created_at  timestamptz not null default now()
);

-- The real identity behind this row, once one exists. Null for every row
-- until Ory Kratos is wired up for it, and null forever for a workload: a
-- pipeline or an agent has no login, its identity is the runtime that holds
-- it (agent/identity.py), not a session. This column only links a *human*
-- directory row to a verifiable login; nothing yet reads it to enforce
-- anything, so adding it does not change what any existing endpoint accepts.
alter table directory add column if not exists kratos_identity_id text unique;

-- Owns data assets and appoints the custodian who approves access to them.
--
-- Information asset ownership sits here rather than on the project on purpose.
-- Projects routinely span departments, so a custodian attached to the project
-- would let a cross-departmental study approve its own access, which is
-- self-approval moved up one organisational level and much harder to notice.
create table if not exists department (
  id          uuid primary key,
  tenant_id   text not null references tenant(id),
  name        text not null,
  -- The accountable approver. May be a person or, in research settings, the
  -- chair of a data access committee: the model needs one accountable answer,
  -- not one human.
  custodian   text not null references directory(id),
  created_at  timestamptz not null default now(),
  unique (tenant_id, name)
);

-- A named, approved purpose with a lifespan.
--
-- This is what stops `purpose` being free text. A lease that references a
-- project makes purpose limitation a constraint instead of a declaration.
create table if not exists project (
  id           uuid primary key,
  tenant_id    text not null references tenant(id),
  name         text not null,
  purpose      text not null check (purpose <> ''),
  lead         text not null references directory(id),
  starts_at    timestamptz not null default now(),
  ends_at      timestamptz not null,
  created_at   timestamptz not null default now(),
  unique (tenant_id, name),
  -- A purpose that has already expired cannot justify new access.
  constraint project_ends_after_start check (ends_at > starts_at)
);

-- ------------------------------------------------------- schema contracts --

-- Contracts are immutable. An edit produces a new row with a new content_hash.
create table if not exists schema_contract (
  id            uuid primary key,
  tenant_id     text not null references tenant(id),
  name          text not null,
  fields        jsonb not null,               -- [{name,type,format,sensitivity,addedBy}]
  primary_key   text[] not null,
  content_hash  text not null unique,
  created_at    timestamptz not null default now()
);

-- ------------------------------------------------------------- datasinks --

create table if not exists datasink (
  id              uuid primary key,
  tenant_id       text not null references tenant(id),
  name            text not null,
  modality        text[] not null,
  retention_days  int not null check (retention_days between 1 and 3650),
  schema_id       uuid not null references schema_contract(id),
  created_at      timestamptz not null default now(),
  unique (tenant_id, name)
);

-- --------------------------------------------------------------- datasets --

create table if not exists dataset (
  id          uuid primary key,
  tenant_id   text not null references tenant(id),
  name        text not null,
  created_at  timestamptz not null default now(),
  unique (tenant_id, name)
);

-- Which department owns this asset, and therefore whose custodian approves
-- access to it. Nullable, because datasets created before the organisation
-- model existed have no department and backfilling one would be inventing an
-- owner. An unowned dataset is a real state and the console must show it as
-- one rather than defaulting it to somebody.
alter table dataset
  add column if not exists department_id uuid references department(id);

-- Where the data came from, which is a different question from how sensitive
-- it is.
--
-- A public benchmark corpus and a de-identified clinical dataset can both be
-- low sensitivity, and only one of them may be redistributed: de-identified
-- data still carries re-identification risk and a consent basis that did not
-- cover republishing. Sensitivity decides who may read it. Provenance decides
-- whether it may leave.
--
-- Conflating the two either blocks download of public data, which protects
-- nothing and makes the platform tiresome for non-regulated work, or permits
-- export of de-identified clinical data, which is the serious mistake.
alter table dataset
  add column if not exists provenance text not null default 'internal_regulated';

do $$
begin
  alter table dataset add constraint dataset_provenance_check
    check (provenance in ('external_public', 'external_licensed', 'internal_regulated'));
exception
  when duplicate_object then null;
end $$;

-- Who brought it in.
alter table dataset add column if not exists registered_by text references directory(id);
alter table dataset add column if not exists registered_at timestamptz;

-- The sensitivity claim, and whose it is.
--
-- Declaring data more restricted is free. Declaring it less restricted is a
-- claim, so it carries a name and a time, and the console shows it as a claim
-- rather than as a fact. Training makes somebody accountable; it does not
-- verify anything, and the record is what the accountability rests on.
alter table dataset add column if not exists declared_class text;
alter table dataset add column if not exists declared_by text references directory(id);
alter table dataset add column if not exists declared_at timestamptz;

-- How the claim is founded.
--
-- `verified_source` means the platform fetched the data itself and can show the
-- origin, so nobody had to be believed. `asserted` means a person said so. The
-- distinction is the entire reason this column exists: anything that can be
-- looked up should be looked up, and only what cannot is left to judgement.
alter table dataset add column if not exists declaration_basis text;

do $$
begin
  alter table dataset add constraint dataset_declaration_basis_check
    check (declaration_basis is null
           or declaration_basis in ('verified_source', 'asserted'));
exception
  when duplicate_object then null;
end $$;

-- The custodian agreeing with the claim. Null until they do.
--
-- Not a rubber stamp: until this is set, the data cannot be released above the
-- class it was declared at, so one person's judgement is never the only thing
-- between an upload and everybody reading it.
alter table dataset add column if not exists classification_confirmed_by text
  references directory(id);
alter table dataset add column if not exists classification_confirmed_at timestamptz;

do $$
begin
  alter table dataset add constraint dataset_confirmation_not_self
    check (classification_confirmed_by is null
           or classification_confirmed_by <> declared_by);
exception
  when duplicate_object then null;
end $$;

-- What kind of thing this holds: audio, text, image, tabular.
--
-- Recorded at registration rather than inferred from file extensions, because
-- an inferred modality is a guess that looks like a fact. Null for everything
-- registered before this existed, and the console says "not recorded" rather
-- than picking one.
alter table dataset add column if not exists modality text[];

-- What a HuggingFace licence actually permits, once the platform has fetched
-- it and looked the tag up (see `licenses.py`). Null for manual uploads and
-- for anything not yet fetched: there is nothing to look up until then.
--
-- `provenance` above is a human's free choice, asked because nothing can be
-- checked for a manual upload. For a HuggingFace source it stops being asked
-- and becomes derived from these two columns instead, the same "looked up,
-- not trusted" shape `declaration_basis` already uses for the sensitivity
-- claim. When the derived fact disagrees with what was originally chosen,
-- the derived fact wins while nothing has been confirmed, and the original
-- choice is kept here rather than overwritten silently.
alter table dataset add column if not exists license_tag text;
alter table dataset add column if not exists license_export_unmodified boolean;
alter table dataset add column if not exists license_export_modified boolean;
alter table dataset add column if not exists provenance_registered_as text;
alter table dataset add column if not exists provenance_overridden_at timestamptz;

-- Where a dataset's contents were fetched from.
--
-- One row per source. Separate from `datasink`, which describes a system that
-- accumulates records over time: a source is what filled this particular
-- dataset, once.
create table if not exists dataset_source (
  id           uuid primary key,
  dataset_id   uuid not null references dataset(id),
  kind         text not null check (kind in ('upload', 'huggingface', 'datasink', 'external')),
  -- A filename, a URL, a query. What it means depends on the kind.
  locator      text not null,
  licence      text,
  -- Of the bytes as received, so a later copy can be shown to be the same file.
  checksum     text,
  bytes        bigint,
  fetched_by   text references directory(id),
  fetched_at   timestamptz not null default now()
);

create index if not exists dataset_source_dataset_idx on dataset_source (dataset_id);

create table if not exists dataset_action (
  id          uuid primary key,
  tenant_id   text not null references tenant(id),
  name        text not null,
  source_schema_id uuid references schema_contract(id),
  target_schema_id uuid references schema_contract(id),
  output_class     text
                   check (output_class in
                          ('RAW','UNDER_REVIEW','OPEN_FOR_ANNOTATION','OPEN_FOR_TRAINING','PUBLISHED')),
  unique (tenant_id, name)
);

create table if not exists action_run (
  id               uuid primary key,
  tenant_id        text not null references tenant(id),
  action_id        uuid not null references dataset_action(id),
  code_hash        text not null,
  image_digest     text not null,
  params           jsonb not null default '{}'::jsonb,
  input_versions   uuid[] not null default '{}',
  output_version   uuid,
  status           text not null
                   check (status in ('running','succeeded','failed')),
  operator         text not null,
  started_at       timestamptz not null default now(),
  ended_at         timestamptz,
  idempotency_key  text not null unique
);

create table if not exists dataset_version (
  id                  uuid primary key,
  tenant_id           text not null references tenant(id),
  dataset_id          uuid not null references dataset(id),
  version             int not null,
  visibility_class    text not null
                      check (visibility_class in
                             ('RAW','UNDER_REVIEW','OPEN_FOR_ANNOTATION','OPEN_FOR_TRAINING','PUBLISHED')),
  iceberg_snapshot_id bigint,
  storage_prefix      text not null,          -- s3 prefix holding this version
  object_manifest     jsonb not null default '[]'::jsonb,
  schema_id           uuid not null references schema_contract(id),
  produced_by_run     uuid references action_run(id),
  record_count        bigint not null default 0,
  content_hash        text not null,
  sealed              boolean not null default true,
  created_at          timestamptz not null default now(),
  unique (dataset_id, version)
);

alter table action_run
  drop constraint if exists action_run_output_version_fkey;
alter table action_run
  add constraint action_run_output_version_fkey
  foreign key (output_version) references dataset_version(id)
  deferrable initially deferred;

-- Immutability, enforced by the database.
-- A rule rewrites the statement, so an UPDATE against a sealed row becomes a
-- no-op rather than an error. V1 asserts the row is unchanged afterwards.
-- A sealed version cannot be changed or removed, unless its organisation was
-- declared disposable before it held anything (see `scratch` above). The
-- exemption is a subquery rather than a column on this table on purpose: the
-- answer belongs to the tenant, and copying it here would be a second place
-- for it to be wrong.
drop rule if exists dataset_version_no_update on dataset_version;
create rule dataset_version_no_update as
  on update to dataset_version
  where old.sealed
    and not exists (select 1 from tenant t
                     where t.id = old.tenant_id and t.purpose = 'scratch')
  do instead nothing;

drop rule if exists dataset_version_no_delete on dataset_version;
create rule dataset_version_no_delete as
  on delete to dataset_version
  where old.sealed
    and not exists (select 1 from tenant t
                     where t.id = old.tenant_id and t.purpose = 'scratch')
  do instead nothing;

-- --------------------------------------------------------- class changes --

-- gate_evidence holds the score card ids that justified the promotion. It is
-- written by the workflow engine and never by a model, which is what makes a
-- promotion auditable rather than merely asserted.
create table if not exists class_transition (
  id                 uuid primary key,
  dataset_version_id uuid not null references dataset_version(id),
  from_class         text not null,
  to_class           text not null,
  decided_by         text not null,
  decided_by_kind    text not null
                     check (decided_by_kind in ('human','workload')),
  gate_evidence      jsonb not null,
  at                 timestamptz not null default now()
);

-- ------------------------------------------------------ access and audit --

-- A lease is asked for and granted in two separate acts by two separate people.
-- Requests live here; only approval writes an access_lease row. Splitting the
-- tables is what makes "requested but never granted" a state the system can
-- report on, rather than an absence indistinguishable from nothing happening.
create table if not exists lease_request (
  id                 uuid primary key,
  tenant_id          text not null references tenant(id),
  principal          text not null,
  dataset_version_id uuid not null references dataset_version(id),
  purpose            text not null check (purpose <> ''),
  justification      text not null check (justification <> ''),
  requested_ttl_hours int not null check (requested_ttl_hours between 1 and 720),
  state              text not null default 'pending'
                     check (state in ('pending','approved','rejected')),
  decided_by         text,
  decided_at         timestamptz,
  lease_id           uuid,
  created_at         timestamptz not null default now()
);

-- The approved purpose this request is made under. Nullable for the same reason
-- as dataset.department_id: requests predating the model have no project.
alter table lease_request
  add column if not exists project_id uuid references project(id);

create table if not exists access_lease (
  id                 uuid primary key,
  tenant_id          text not null references tenant(id),
  principal          text not null,
  dataset_version_id uuid not null references dataset_version(id),
  purpose            text not null check (purpose <> ''),
  approved_by        text not null,
  revoked            boolean not null default false,
  expires_at         timestamptz not null,
  created_at         timestamptz not null default now(),
  -- Separation of duties, in the schema rather than in a review process.
  constraint lease_no_self_approval check (approved_by <> principal)
);

-- A task's proven right to write into one prefix it legitimately opened.
--
-- The write-side counterpart to access_lease, and deliberately not shaped
-- like it. A lease is issued ahead of a read and can be revoked; a write
-- grant is issued at the moment a real, proven task (task_credential.py)
-- asks to write into the *next* version-location for a dataset it names,
-- computed by the platform itself from versions.next_version(), never from
-- anything the caller supplies (see docs/internal/design/write-credential-rationale.md
-- for why authorization here is "a real task, of this tenant, writing into a
-- version-location the platform computed" rather than ownership -- a
-- dataset_action targets a schema, not a specific dataset, so there is no
-- ownership relationship to check). Once granted it never needs to expire or
-- be revoked: the prefix it names is a sealed version's storage_prefix, which
-- dataset_version's own no-update/no-delete rules already make write-once, so
-- nothing should ever legitimately be written there a second time either.
--
-- platform/api/app/grants.py's justified_write_pairs() reads this the same
-- way justified_pairs() reads access_decision: distinct (role, bucket,
-- storage_prefix), compiled into the permissions document as Write grants,
-- replacing pipeline_action's old blanket every_bucket Write.
create table if not exists write_grant (
  id             uuid primary key,
  tenant_id      text not null references tenant(id),
  role           text not null,
  bucket         text not null,
  storage_prefix text not null,
  task_kind      text not null
                check (task_kind in ('action_run', 'pipeline_run', 'huggingface_fetch_job')),
  task_id        uuid not null,
  principal      text not null,
  created_at     timestamptz not null default now(),
  -- A retried request for the same task and prefix (Temporal replay, or a
  -- second write-credential request within the same run) is the same grant,
  -- not a second one; the insert below is ON CONFLICT DO NOTHING against
  -- this so the register never accumulates duplicates of it.
  unique (tenant_id, task_kind, task_id, storage_prefix)
);

create index if not exists write_grant_role_idx on write_grant (role, bucket, storage_prefix);

-- huggingface_fetch_job added after this table's first release: a HuggingFace
-- fetch job proves itself the same way an action_run or pipeline_run does
-- (task_credential.py), so its write into the dataset it names goes through
-- this same table rather than a parallel one.
alter table write_grant drop constraint if exists write_grant_task_kind_check;
alter table write_grant add constraint write_grant_task_kind_check
  check (task_kind in ('action_run', 'pipeline_run', 'huggingface_fetch_job'));

-- ------------------------------------------------------------ roles held --

-- When somebody stopped being able to act, or null while they still can.
--
-- Nobody is ever deleted: the foreign key from access_lease refuses to remove
-- anyone whose approval is on record, because an approval naming somebody
-- unresolvable is not an audit trail. So leaving is recorded rather than
-- erased, and everything that person ever approved stays attributable.
--
-- The console already says some people are "registered, but unable to act".
-- Until this column that was true of exactly one case, a data custodian with
-- no department, and overstated for everybody else.
alter table directory add column if not exists ended_at timestamptz;

-- Asking for a role, and the answer.
--
-- Deliberately the same shape as lease_request above. A role is a larger
-- claim than access to one dataset version, so it gets the same treatment
-- rather than a weaker one: somebody asks and says why, somebody else
-- decides, and both halves are on the record.
create table if not exists role_request (
  id            uuid primary key,
  tenant_id     text not null references tenant(id),
  principal     text not null references directory(id),
  role          text not null check (role <> ''),
  justification text not null check (justification <> ''),
  requested_days int not null check (requested_days between 1 and 365),
  state         text not null default 'pending'
                check (state in ('pending','approved','rejected')),
  decided_by    text references directory(id),
  decided_at    timestamptz,
  grant_id      uuid,
  created_at    timestamptz not null default now(),
  -- The role that administers roles is not obtainable from the running
  -- system, at the database rather than only in policy. A second
  -- administrator is made the way the first one was: deliberately, outside
  -- this platform. Enforced here so that a bug in the API cannot create the
  -- one request nobody should be able to make.
  constraint role_request_never_admin check (role <> 'platform_admin')
);

-- A decided request cannot be re-decided.
--
-- The same instead-nothing shape dataset_version uses, narrowed to what
-- actually has to be immutable: a pending request is still being worked on,
-- and a decided one is a record of what somebody concluded. Without this the
-- approval and the reason behind it could be rewritten afterwards, which
-- would make the record worth nothing.
drop rule if exists role_request_decided_is_final on role_request;
create rule role_request_decided_is_final as
  on update to role_request
  where old.state <> 'pending'
  do instead nothing;

-- Holding a role, for a while.
--
-- Time-bound on purpose, and the reason is the one this platform already
-- learned about storage: anything nobody has to renew is something nobody
-- ever reviews. A grant that lapses makes somebody decide again, rather
-- than accumulating quietly the way an array of roles does.
create table if not exists role_grant (
  id            uuid primary key,
  tenant_id     text not null references tenant(id),
  principal     text not null references directory(id),
  role          text not null check (role <> ''),
  approved_by   text not null references directory(id),
  request_id    uuid references role_request(id),
  revoked       boolean not null default false,
  expires_at    timestamptz not null,
  created_at    timestamptz not null default now(),
  -- Who last confirmed this is still needed, and when. Null until somebody
  -- has, which is what the overdue list is built from.
  attested_by   text references directory(id),
  attested_at   timestamptz,
  -- The same separation of duties access_lease states, for the same reason
  -- and in the same place: in the schema, not in a review process.
  constraint role_grant_no_self_approval check (approved_by <> principal),
  constraint role_grant_never_admin check (role <> 'platform_admin')
);

create index if not exists role_grant_live
  on role_grant (principal, role) where not revoked;

-- The approver must be somebody the organisation registered.
--
-- Without this, `approved_by` was any string, and the self-approval check only
-- stopped you naming yourself. A requester could approve their own request by
-- typing a colleague's name, which is separation of names rather than
-- separation of duties.
--
-- NOT VALID on purpose. Leases granted before the directory existed name
-- approvers that were never registered, and inventing directory entries to make
-- them pass would fabricate a record of who was accountable. Old rows keep the
-- history they actually have; every new approval is checked.
--
-- What this still cannot do is verify that the caller is the approver. That
-- needs authentication, which the platform does not have, so an approval
-- remains an assertion by a registered person rather than a proven act.
do $$
begin
  alter table access_lease
    add constraint lease_approver_registered
    foreign key (approved_by) references directory(id) not valid;
exception
  when duplicate_object then null;
end $$;

-- Who revoked a lease, and when. Added alongside the session requirement on
-- revoke_lease: authenticating the caller and then discarding who they were
-- would defeat the point of the change.
alter table access_lease add column if not exists revoked_by text references directory(id);
alter table access_lease add column if not exists revoked_at timestamptz;

-- Every decision is recorded, including the denials. Repeated denials are the
-- signal that a permission is missing or that someone is doing something they
-- should not be; neither is visible if only allows are logged.
create table if not exists access_decision (
  id                 bigserial,
  at                 timestamptz not null default now(),
  principal          text not null,
  principal_kind     text not null check (principal_kind in ('human','workload')),
  principal_roles    text[] not null default '{}',
  tenant_id          text,
  dataset_version_id uuid,
  requested_class    text,
  purpose            text,
  allowed            boolean not null,
  reasons            text[] not null default '{}',
  lease_id           uuid,
  primary key (id, at)
) partition by range (at);

create table if not exists access_decision_2026 partition of access_decision
  for values from ('2026-01-01') to ('2027-01-01');
create table if not exists access_decision_2027 partition of access_decision
  for values from ('2027-01-01') to ('2028-01-01');

-- A credential request has two outcomes that can disagree: what policy decided,
-- and whether the grant it authorised could actually be applied. Recording only
-- the first lets the log say "allowed" when nothing was granted.
--
-- Both are written, as separate rows, because the alternative is worse in both
-- directions. Writing one row after the grant would mean a crash mid-grant
-- leaves no record at all. Updating the policy row afterwards would mean an
-- audit log that can be rewritten, and an audit log that can be rewritten is
-- not one.
--
-- The grant row is always written, never only on failure. Silence meaning
-- success is exactly the pattern that hid this bug.
alter table access_decision
  add column if not exists phase text not null default 'policy';

do $$
begin
  alter table access_decision
    add constraint access_decision_phase_check
    check (phase in ('policy', 'grant'));
exception
  when duplicate_object then null;
end $$;

create index if not exists access_decision_phase_idx
  on access_decision (phase, allowed, at desc);

create index if not exists access_decision_principal_idx
  on access_decision (principal, at desc);
create index if not exists access_decision_allowed_idx
  on access_decision (allowed, at desc);

-- ----------------------------------------------- keys, deletion, tombstone --

-- One wrapped data key per record. Destroying the key makes the record
-- unreadable in every dataset version that ever held it, which is how deletion
-- and immutability are reconciled.
create table if not exists record_key (
  record_id     text primary key,
  tenant_id     text not null references tenant(id),
  wrapped_key   bytea,                        -- null once destroyed
  destroyed_at  timestamptz,
  created_at    timestamptz not null default now()
);

-- The record that a record existed survives its deletion, otherwise erasure
-- breaks the audit trail it was meant to serve.
create table if not exists tombstone (
  record_id   text primary key,
  tenant_id   text not null references tenant(id),
  reason      text not null,
  requested_by text not null,
  at          timestamptz not null default now()
);

-- ------------------------------------------------- reclaiming object bytes --

-- One row per dataset version whose stored objects were deleted to free space.
--
-- The same move as destroying a record key one layer out: the bytes go and the
-- record stays. A version's row, its lineage and every access decision ever made
-- about it survive, so what happened is still answerable. Only the files are
-- gone.
--
-- This cannot be a column on `dataset_version`. That table is immutable by
-- rewrite rule, which is exactly why the record of reclamation has to sit beside
-- it rather than inside it. Same reasoning as `class_transition`.
--
-- Append-only in use, and nothing in the platform deletes from it, because this
-- row is the only remaining evidence that the bytes ever existed.
create table if not exists storage_reclamation (
  dataset_version_id uuid primary key references dataset_version(id),
  tenant_id          text not null references tenant(id),
  bytes_freed        bigint not null,
  object_count       int not null,
  reclaimed_by       text not null,
  reason             text not null check (reason <> ''),
  at                 timestamptz not null default now()
);

create index if not exists storage_reclamation_tenant_idx
  on storage_reclamation (tenant_id, at desc);

-- ------------------------------------------------- a closed tenant is shut --

-- Nothing new may be written to a retired tenant.
--
-- In the database rather than in the API, for the same reason the immutability
-- rules are. There is no single place in the control plane where `tenant_id` is
-- resolved: every endpoint takes it from its own request body, so a check in
-- code would have to be repeated at each one, and the one that got missed would
-- keep working.
--
-- Three tables are deliberately exempt.
--
--   access_decision      the audit log. A refused write is exactly the kind of
--                        thing that must still be recorded, and a log that goes
--                        silent when a tenant closes is worse than no log.
--   storage_reclamation  winding a closed tenant down is the one thing left to
--                        do to it.
--   tenant               reopening or closing a tenant has to remain possible.
create or replace function refuse_write_to_retired_tenant() returns trigger as $$
declare
  target text := coalesce(new.tenant_id, old.tenant_id);
begin
  if (select purpose from tenant where id = target) = 'retired' then
    raise exception
      'tenant % is retired: its records are readable but nothing more may be written to it', target
      using errcode = 'read_only_sql_transaction';
  end if;
  return new;
end;
$$ language plpgsql;

do $$
declare t text;
begin
  foreach t in array array[
    'schema_contract','dataset','dataset_version','dataset_action','action_run',
    'lease_request','access_lease','department','project','directory',
    'record_key','tombstone','datasink','write_grant'
  ] loop
    execute format('drop trigger if exists %I on %I', 'refuse_retired_' || t, t);
    execute format(
      'create trigger %I before insert or update on %I
         for each row execute function refuse_write_to_retired_tenant()',
      'refuse_retired_' || t, t);
  end loop;
end $$;

-- ------------------------------------------------- who may approve what --

-- For a dataset version, the custodian entitled to approve access to it.
--
-- A view rather than logic in the API, so the console, the policy input and any
-- future caller get the same answer. Two implementations of "who is accountable
-- here" is two answers that can drift, and the drift would only show up in an
-- audit.
--
-- A null custodian is meaningful: the dataset has no department, so nobody is
-- accountable and nobody can approve access to it. The console must show that
-- as an unowned asset rather than as an empty dropdown.
create or replace view version_custodian as
select
  dv.id            as dataset_version_id,
  dv.tenant_id,
  d.id             as dataset_id,
  d.name           as dataset_name,
  dept.id          as department_id,
  dept.name        as department_name,
  dept.custodian   as custodian
from dataset_version dv
join dataset d           on d.id = dv.dataset_id
left join department dept on dept.id = d.department_id;

-- ------------------------------------------------------- effective class --

-- A sealed version's visibility_class column records the class it was sealed
-- at, and the immutability rule means it can never be rewritten. So the current
-- class is derived from the transition log instead. This is not a workaround:
-- it is the point. Promotion appends a row and grants a prefix, it never
-- rewrites history, so the class a version held on any past date stays
-- answerable.
-- Which object-storage backend a tenant's new datasets default to, and
-- which one a specific dataset is currently targeting while it still has
-- no sealed version (upload has to know where to write bytes before any
-- version exists to pin the choice onto). Neither of these is the
-- authoritative fact for a dataset that already has sealed versions:
-- dataset_version.storage_backend, set once at seal time, is. Changing
-- either of these columns later never touches an already-sealed version,
-- the same reasoning storage_prefix already relies on. Placed here,
-- before version_class, because that view's own select list needs
-- dataset_version.storage_backend to already exist, and this file is
-- applied top to bottom.
alter table tenant add column if not exists default_storage_backend text
  not null default 'seaweedfs';
alter table tenant drop constraint if exists tenant_default_storage_backend_valid;
alter table tenant add constraint tenant_default_storage_backend_valid
  check (default_storage_backend in ('seaweedfs', 'r2'));

alter table dataset add column if not exists storage_backend text;
alter table dataset drop constraint if exists dataset_storage_backend_valid;
alter table dataset add constraint dataset_storage_backend_valid
  check (storage_backend is null or storage_backend in ('seaweedfs', 'r2'));

-- Immutable once sealed, same as storage_prefix itself: this is where a
-- version's bytes really are, and it never changes after sealing, no
-- matter what the tenant's or dataset's own current default says later.
alter table dataset_version add column if not exists storage_backend text
  not null default 'seaweedfs';
alter table dataset_version drop constraint if exists dataset_version_storage_backend_valid;
alter table dataset_version add constraint dataset_version_storage_backend_valid
  check (storage_backend in ('seaweedfs', 'r2'));

-- Generic on purpose: (system, metric, period, value) rather than
-- R2-specific columns, so a future billed backend, or an unrelated
-- metered system, reuses this table with zero schema change. period is
-- caller-defined granularity ('2026-08' for R2's monthly ceiling);
-- metric names are caller-defined too ('class_a_ops', 'class_b_ops',
-- 'bytes_stored' for R2 today). Ceilings themselves live in code
-- (platform/api/app/r2.py), not here: a database-configurable limit
-- nothing enforces a floor on is not a limit.
create table if not exists usage_counter (
  system     text not null,
  metric     text not null,
  period     text not null,
  value      bigint not null default 0,
  updated_at timestamptz not null default now(),
  primary key (system, metric, period)
);

-- Which bucket, and for r2 which credential, a tenant's operations on a
-- given backend use. This table is the answer, not a hint towards it:
-- seaweed.py's and r2.py's _resolve_bucket() read the row and nothing
-- else, so a row present means that bucket and a row absent means this
-- tenant has never written on this backend.
--
-- Absent used to mean something quite different: "still on that backend's
-- shared, platform-wide bucket", inferred from the tenant holding data
-- with no row. That answer depended on the order two unrelated things
-- happened in, and pinned `finance` to the shared bucket weeks after
-- per-tenant buckets existed, because its seed registers versions with a
-- declared manifest and so had a dataset_version row before anything
-- first asked which bucket it used. Every tenant that inference used to
-- answer for now carries a row saying the same thing, written by the
-- backfill below.
--
-- The bucket itself is still created lazily, on the first real write,
-- because each one is a SeaweedFS collection drawing several volumes from
-- a fixed pool. Only the decision is eager.
-- How many storage volumes this tenant's bucket is given on its first
-- write, when it should differ from the install's own default
-- (MUNITAS_STORAGE_VOLUMES_PER_TENANT in platform/api/app/config.py).
--
-- Null means "use the default", which is the ordinary case. A number here
-- is a deliberate statement at onboarding that this tenant is expected to
-- be large, made once, by name, rather than by raising the default and
-- making every tenant pay for it. A volume is a 1 GB file SeaweedFS
-- appends objects into, never shared between tenants, so the number is
-- paid per tenant rather than per byte.
alter table tenant add column if not exists initial_storage_volumes int
  check (initial_storage_volumes is null or initial_storage_volumes > 0);

create table if not exists tenant_storage_provision (
  tenant_id      text not null references tenant(id),
  backend        text not null check (backend in ('seaweedfs', 'r2')),
  bucket         text not null,
  -- Null for seaweedfs (credential isolation deferred there; every
  -- seaweedfs operation still uses the existing shared, per-role
  -- identity). Set for r2: the tenant's own bucket-scoped Access Key ID,
  -- not secret on its own, safe alongside the wrapped secret below.
  access_key_id      text,
  -- Envelope-encrypted (crypto.EnvelopeCrypto, the same mechanism
  -- external_credential already uses for HuggingFace tokens), never
  -- plaintext at rest. Two columns because EnvelopeCrypto.seal() returns
  -- a ciphertext and a wrapped_key, both required to open() it again,
  -- the same two-column shape external_credential already uses. Both
  -- null exactly when access_key_id is.
  secret_ciphertext  bytea,
  secret_wrapped_key bytea,
  created_at     timestamptz not null default now(),
  primary key (tenant_id, backend)
);

-- refuse_write_to_retired_tenant is defined earlier in this file, before
-- this table; a retired tenant must not provision a new bucket or
-- credential any more than it may write anything else.
drop trigger if exists refuse_retired_tenant_storage_provision on tenant_storage_provision;
create trigger refuse_retired_tenant_storage_provision
  before insert or update on tenant_storage_provision
  for each row execute function refuse_write_to_retired_tenant();

-- The one-time backfill that recorded existing tenants against the shared
-- bucket was removed with that bucket. It re-ran on every idempotent apply,
-- and would have pinned any tenant that had versions before its first write
-- to a bucket that no longer exists.

create or replace view version_class as
select
  dv.id                       as dataset_version_id,
  dv.tenant_id,
  dv.dataset_id,
  dv.version,
  dv.storage_prefix,
  dv.visibility_class         as sealed_class,
  coalesce(latest.to_class, dv.visibility_class) as current_class,
  latest.at                   as class_changed_at,
  latest.decided_by           as class_decided_by,
  -- Null unless the objects were freed. Carried here so every read model gets
  -- the fact without joining for it, and so a screen offering to open a version
  -- can say the files are gone instead of failing when somebody tries.
  sr.at                       as reclaimed_at,
  -- Appended at the end, not inserted alongside storage_prefix above:
  -- CREATE OR REPLACE VIEW refuses to change the position of an existing
  -- view column (it reads a mid-list insertion as renaming whatever
  -- column already held that position), so a column added to an
  -- existing view has to go last regardless of where it reads best.
  -- Read by name everywhere this view is queried, so the position does
  -- not matter beyond satisfying that restriction.
  dv.storage_backend
from dataset_version dv
left join storage_reclamation sr on sr.dataset_version_id = dv.id
left join lateral (
  select ct.to_class, ct.at, ct.decided_by
  from class_transition ct
  where ct.dataset_version_id = dv.id
  order by ct.at desc, ct.id desc
  limit 1
) latest on true;

-- --------------------------------------------------------------- lineage --

-- How a pipeline run started, distinct from `operator`, which names the
-- workload that executed it. A scheduled nightly run, a data engineer's
-- manual one-off, and an ad-hoc single step were previously indistinguishable
-- after the fact: `operator` was a hardcoded literal, never varying.
--
-- NOT VALID on the shape constraint, for the same reason
-- `lease_approver_registered` is: every action_run row that already exists has
-- `triggered_by = null`, and validating retroactively would invent an
-- attribution that never happened. Old rows keep the history they actually
-- have; every new run is checked.
alter table action_run add column if not exists trigger_kind text not null default 'manual';
alter table action_run add column if not exists triggered_by text references directory(id);
alter table action_run add column if not exists schedule_id text;

do $$
begin
  alter table action_run add constraint action_run_trigger_kind_check
    check (trigger_kind in ('manual','scheduled'));
exception when duplicate_object then null;
end $$;

do $$
begin
  alter table action_run add constraint action_run_trigger_shape check (
    (trigger_kind = 'manual'    and triggered_by is not null and schedule_id is null)
    or
    (trigger_kind = 'scheduled' and triggered_by is null and schedule_id is not null)
  ) not valid;
exception when duplicate_object then null;
end $$;

-- Lineage questions are join questions. This view answers "what produced this
-- dataset version, from what, with which code" in one hop.
create or replace view lineage as
select
  dv.id                as dataset_version_id,
  d.name               as dataset_name,
  dv.version,
  dv.visibility_class,
  dv.content_hash,
  ar.id                as run_id,
  da.name              as action_name,
  ar.code_hash,
  ar.image_digest,
  ar.operator,
  ar.input_versions,
  ar.started_at,
  ar.ended_at,
  ar.trigger_kind,
  ar.triggered_by,
  ar.schedule_id
from dataset_version dv
join dataset d          on d.id = dv.dataset_id
left join action_run ar on ar.id = dv.produced_by_run
left join dataset_action da on da.id = ar.action_id;

-- ------------------------------------------------- provenance and grants --

-- Who asked, when it differs from who reads.
--
-- Null means the reading principal asked for themselves, which is every lease
-- ever granted before this column existed and remains the default shape for a
-- human. Set only when a human requests access on a workload's behalf: the
-- workload stays the only identity policy or lease-matching ever sees, so a
-- workload can never be made to read as the human who asked. Same rule
-- agent/identity.py already keeps for the agent runtime: identity is never
-- read from anything the caller supplies, only recorded as provenance
-- alongside it.
alter table lease_request add column if not exists requested_by text references directory(id);
alter table access_lease  add column if not exists requested_by text references directory(id);

do $$
begin
  alter table access_lease
    add constraint lease_no_self_approval_requester
    check (requested_by is null or approved_by <> requested_by);
exception when duplicate_object then null;
end $$;

-- A lease that lasts until revoked rather than until a timer runs out.
--
-- Only ever creatable for a workload. A human's access must always stay
-- bounded and re-justified; that invariant is the one thing this feature must
-- not weaken, so it is enforced here, not only in the API branch that also
-- refuses it.
alter table access_lease alter column expires_at drop not null;

create or replace function refuse_standing_lease_for_human() returns trigger as $$
begin
  if new.expires_at is null
     and (select kind from directory where id = new.principal) is distinct from 'workload' then
    raise exception
      'a standing lease (no expiry) may only be granted to a workload principal; % is not a registered workload',
      new.principal
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists access_lease_standing_workload_only on access_lease;
create trigger access_lease_standing_workload_only
  before insert or update on access_lease
  for each row execute function refuse_standing_lease_for_human();

-- The ask for a standing lease. `requested_ttl_hours` becomes optional because
-- a standing request carries none; the shape constraint keeps the two in step
-- so a request can never be standing with a TTL or bounded without one.
alter table lease_request add column if not exists standing boolean not null default false;
alter table lease_request alter column requested_ttl_hours drop not null;

do $$
begin
  alter table lease_request add constraint lease_request_ttl_shape
    check ((standing and requested_ttl_hours is null)
        or (not standing and requested_ttl_hours between 1 and 720));
exception when duplicate_object then null;
end $$;

-- A registered human's own external credential, so a fetch from a gated
-- source reflects whether *that person* has been granted access, not
-- whether some shared platform account happens to have been. The token
-- itself never lands here: `ciphertext`/`wrapped_key` are what
-- `crypto.EnvelopeCrypto` produces, the same envelope scheme `record_key`
-- already uses for data at rest, keyed here by `hf-token/{directory_id}`
-- rather than a dataset record id.
create table if not exists external_credential (
  id            uuid primary key,
  directory_id  text not null references directory(id),
  provider      text not null check (provider in ('huggingface')),
  hf_username   text not null,
  ciphertext    bytea not null,
  wrapped_key   bytea not null,
  connected_at  timestamptz not null default now(),
  last_used_at  timestamptz,
  unique (directory_id, provider)
);

-- A HuggingFace fetch running in the background, with real progress.
--
-- Not `action_run`: that table requires a `dataset_action` row (a pipeline
-- action, a concept that does not exist for a fetch), a code hash and an
-- image digest that mean nothing here, and it has no progress columns at
-- all. This is sized for what the job actually is. `workflow_id` ties the
-- row back to the Temporal workflow doing the work, so the two can be
-- cross-referenced without either one needing to duplicate the other's
-- state.
create table if not exists huggingface_fetch_job (
  id            uuid primary key,
  dataset_id    uuid not null references dataset(id),
  repo_id       text not null,
  revision      text not null,
  path          text not null default '',
  fetched_by    text not null references directory(id),
  status        text not null check (status in ('running', 'succeeded', 'failed')),
  files_total   int,
  files_done    int not null default 0,
  bytes_total   bigint,
  bytes_done    bigint not null default 0,
  error         text,
  workflow_id   text not null unique,
  started_at    timestamptz not null default now(),
  ended_at      timestamptz
);

create index if not exists huggingface_fetch_job_dataset_idx
  on huggingface_fetch_job (dataset_id);

-- ------------------------------------------------------------------ agents --

-- An agent, registered the way a dataset is: an end user names it, gives it
-- an owning department, and states its purpose. Distinct from a `Service`
-- (the frontend's static list of built-in workloads: the pipeline, the
-- annotation tool, the training job) because those are fixed infrastructure
-- nobody registers, while an agent is a resource a data engineer creates,
-- versions, and is accountable for, the same way they are for a dataset.
create table if not exists agent (
  id            uuid primary key,
  tenant_id     text not null references tenant(id),
  name          text not null,
  department_id uuid references department(id),
  registered_by text not null references directory(id),
  purpose       text not null check (purpose <> ''),
  -- The directory row that supplies this agent's runtime identity and
  -- policy floor (agent/identity.py's AgentIdentity.principal). Reused, not
  -- duplicated: role and floor are already governed by access.rego, keyed
  -- on this id.
  principal_id  text not null references directory(id),
  created_at    timestamptz not null default now(),
  unique (tenant_id, name)
);

-- A sealed, content-addressed version of an agent's code, the same
-- immutability posture as `dataset_version` and for the same reason: a
-- version somebody could quietly edit after the fact proves nothing about
-- what actually ran. Single-step rather than two-phase like a dataset,
-- because a version's content (what code, what model, what tools) is fully
-- known at registration time; there is no upload phase to wait on.
--
-- `code_hash` is never a hash of a named file. Different agent developers
-- lay out their code differently, so the last git commit to touch the
-- registrant's declared `source_path` is preferred when that checkout is a
-- git repository (it covers whatever files they actually have, in any
-- layout, and does not change when an unrelated commit elsewhere in the
-- same repository does), falling back to a hash over every file under
-- `source_path` only when there is no repository at all. This is the same
-- pattern `action_run.code_hash` already uses (worker/platform_client.py's
-- `code_hash()`), generalised rather than reinvented.
--
-- `model_id` and `tool_scope` are declared by whoever registers the
-- version, not parsed out of source code, the same trust posture the
-- platform already gives `dataset.provenance` and
-- `dataset_version.object_manifest`.
create table if not exists agent_version (
  id            uuid primary key,
  tenant_id     text not null references tenant(id),
  agent_id      uuid not null references agent(id),
  version       int not null,
  code_hash     text not null,
  source_path   text not null,
  image_digest  text not null default 'native:host-venv',
  model_id      text not null,
  tool_scope    text[] not null default '{}',
  registered_by text not null references directory(id),
  content_hash  text not null,
  sealed        boolean not null default true,
  created_at    timestamptz not null default now(),
  unique (agent_id, version)
);

create index if not exists agent_version_agent_idx on agent_version (agent_id);

-- Where this version's own code lives, when the platform is the one that
-- runs it, rather than merely recording a claim about it.
--
-- Nullable, and that nullability is the distinction: a version registered
-- through the CLI path (scripts/admin/register-agent-version.py, for an agent this
-- platform only ever *describes*, never executes) leaves these null and
-- keeps `code_hash`/`source_path` as client-claimed metadata, unchanged
-- from how this column pair has always worked. A version registered
-- through the upload endpoint (POST /agents/{id}/versions/upload) sets
-- `code_object_key` to where the uploaded archive was written in object
-- storage, and for that path `code_hash` stops being a claim: it is
-- computed server-side from the bytes actually received, the same
-- discipline `dataset_source.checksum` already applies to dataset uploads.
-- Two different trust levels sharing one column would be dishonest, so the
-- rule for telling them apart is exactly "is code_object_key set."
alter table agent_version add column if not exists code_object_key text;
alter table agent_version add column if not exists code_bytes bigint;
alter table agent_version add column if not exists entrypoint text not null default 'main.py';
alter table agent_version add column if not exists manifest jsonb;

-- Whether this version's own code reads real dataset content when it runs
-- sandboxed. Declared once, at upload time, the same reasoning tool_scope
-- and model_id already follow: a developer building an agent already
-- knows whether its code touches files, and that does not change from one
-- run to the next. 'none' (the default, matching every version sealed
-- before this column existed) skips staging entirely. Only 'copy' exists
-- today; a future 'mount' value gets added exactly when that mechanism
-- ships (docs/STORAGE_PORTABILITY_GUIDELINES.md), not speculatively ahead
-- of it. Meaningless for a native version (code_object_key null); only
-- worker/sandbox_run_activities.py's sandboxed path reads it.
alter table agent_version add column if not exists data_access text
  not null default 'none';
alter table agent_version drop constraint if exists agent_version_data_access_valid;
alter table agent_version add constraint agent_version_data_access_valid
  check (data_access in ('none', 'copy'));

drop rule if exists agent_version_no_update on agent_version;
create rule agent_version_no_update as
  on update to agent_version
  where old.sealed
  do instead nothing;

drop rule if exists agent_version_no_delete on agent_version;
create rule agent_version_no_delete as
  on delete to agent_version
  where old.sealed
  do instead nothing;

-- ---------------------------------------------------- agent deployment --

-- Which version an agent actually runs, recorded the way every other
-- state change in this schema is: as a new row, never as an overwritten
-- pointer. "The active version" is the most recent row for an agent, not
-- a column anyone can silently update. Redeploying an earlier version is
-- just another row; deploy history and rollback both fall out of this for
-- free, the same reasoning `class_transition` already uses for promotion.
create table if not exists agent_deployment (
  id                uuid primary key,
  tenant_id         text not null references tenant(id),
  agent_id          uuid not null references agent(id),
  agent_version_id  uuid not null references agent_version(id),
  deployed_by       text not null references directory(id),
  note              text,
  deployed_at       timestamptz not null default now()
);

create index if not exists agent_deployment_agent_idx
  on agent_deployment (agent_id, deployed_at desc);

create or replace view agent_active_version as
select distinct on (agent_id)
  agent_id, agent_version_id, deployed_by, deployed_at
from agent_deployment
order by agent_id, deployed_at desc;

-- ------------------------------------------------------------ agent runs --

-- One invocation of an agent, mirroring `action_run`'s shape. `agent_version_id`
-- is pinned here at insert time, never re-derived from `agent_active_version`:
-- a run records what actually executed, and that cannot retroactively change
-- if someone redeploys while it is running, the same reason `action_run.code_hash`
-- freezes what actually ran rather than pointing at whatever is current now.
create table if not exists agent_run (
  id                uuid primary key,
  tenant_id         text not null references tenant(id),
  agent_id          uuid not null references agent(id),
  agent_version_id  uuid not null references agent_version(id),
  status            text not null
                    check (status in ('running', 'awaiting_access',
                                      'awaiting_approval', 'awaiting_activation',
                                      'succeeded', 'failed', 'halted')),
  purpose           text not null check (purpose <> ''),
  requested_by      text not null references directory(id),
  tool_calls        int not null default 0,
  halted_reason     text,
  error             text,
  started_at        timestamptz not null default now(),
  ended_at          timestamptz
);

create index if not exists agent_run_agent_idx on agent_run (agent_id, started_at desc);

-- Who let this run carry on past its approval gate, and when.
--
-- The graph stops before `await_approval` on every run, so a run that has done
-- its work and is waiting for a human is the normal case, not an exception.
-- It needed a status of its own: without one, every paused run was written
-- down as `succeeded`, which said a run had finished when it had not started
-- the half a human was meant to authorise.
alter table agent_run add column if not exists approved_by text references directory(id);
alter table agent_run add column if not exists approved_at timestamptz;

-- What the run is waiting on, when it is waiting on something.
--
-- Two different waits, and conflating them would hide which one a run is in.
-- `awaiting_access` is before any work: the agent may not read the dataset it
-- was pointed at, so a lease was requested on its behalf and nothing has run
-- yet. `awaiting_approval` is after the work: the graph did its job and stopped
-- so a human can accept the findings. The first is answered by the custodian
-- who owns the data, the second by anyone in the tenant who did not start the
-- run.
--
-- `lease_request_id` is how an approval finds the run that was waiting on it,
-- rather than the approval path guessing from principal and version, which
-- would wake the wrong run when the same agent has two pending against one
-- dataset.
alter table agent_run add column if not exists lease_request_id uuid
  references lease_request(id);

-- What the run actually concluded, so the answer outlives the graph checkpoint.
--
-- Without this a finished run records that it finished and not what it decided,
-- and the ranking it produced is readable only by loading the LangGraph
-- checkpoint, which is the library's storage rather than the platform's record.
-- A run whose output cannot be read back is not auditable, and "the agent said
-- this document was urgent" is exactly the kind of claim somebody asks about
-- later.
alter table agent_run add column if not exists findings jsonb not null default '[]'::jsonb;

-- Which dataset version this run may ever request a credential for. Set once,
-- at insert, from the same value the run was launched against, never
-- updated afterward, the same informal pinning `agent_version_id` above
-- already relies on (nothing anywhere issues an UPDATE against either).
-- Nullable: a run may legitimately target no specific dataset.
--
-- This is the actual security boundary for an agent's dataset scope.
-- POST /credentials checks a workload's request against this column,
-- looked up server-side from a real, resolved agent_run row, never
-- against anything the calling code claims about itself. A `tool_scope`
-- check exists too (agent_version.tool_scope, agent/tools.py), but that one
-- is explicitly best-effort: the server has no independent way to know
-- which function an agent's own code decided to call, only this, which the
-- caller cannot talk its way around.
alter table agent_run add column if not exists dataset_version_id uuid
  references dataset_version(id);

-- Which path this run actually executed under: the platform's own built-in
-- graph, or an uploaded, sandboxed third-party agent. Set once, at insert,
-- from the deployed version's own `code_object_key` (set means 'sandboxed',
-- null means 'native'), never re-derived later, the same pinning
-- discipline `dataset_version_id` above already follows, and for the same
-- reason: a redeploy after this run started must not change what the row
-- says actually ran.
alter table agent_run add column if not exists execution_mode text
  not null default 'native' check (execution_mode in ('native', 'sandboxed'));

create index if not exists agent_run_waiting_on_lease_idx
  on agent_run (lease_request_id) where lease_request_id is not null;

alter table agent_run drop constraint if exists agent_run_status_check;
alter table agent_run
  add constraint agent_run_status_check
  check (status in ('running', 'awaiting_access', 'awaiting_approval',
                    'awaiting_activation', 'succeeded', 'failed', 'halted'));

-- Separation of duties, the same rule `lease_no_self_approval_requester` keeps
-- for a lease: whoever started the run is not who gets to wave it through.
do $$
begin
  alter table agent_run
    add constraint agent_run_no_self_approval
    check (approved_by is null or approved_by <> requested_by);
exception when duplicate_object then null;
end $$;

-- Which run's tool call produced this decision, when there is one. Nullable:
-- most rows (a human's lease request) have no run at all. The join path for
-- "which version made this decision" is agent_run_id -> agent_run.agent_version_id,
-- not a second, denormalised agent_version_id column here that could drift
-- from the first.
alter table access_decision add column if not exists agent_run_id uuid;

-- Create triggers for the agent-registry and agent-deployment tables (this must
-- run after all four are defined, which is why it lives here rather than with
-- the earlier refuse_write_to_retired_tenant trigger block).
do $$
declare t text;
begin
  foreach t in array array[
    'agent','agent_version','agent_deployment','agent_run'
  ] loop
    execute format('drop trigger if exists %I on %I', 'refuse_retired_' || t, t);
    execute format(
      'create trigger %I before insert or update on %I
         for each row execute function refuse_write_to_retired_tenant()',
      'refuse_retired_' || t, t);
  end loop;
end $$;

-- ------------------------------------------------------------------
-- The de-identification gate decision.
--
-- The pipeline used to measure and promote in one activity, so the one act
-- that widens access to clinical data had no human on it and showed nobody
-- the evidence. It now records what it found and what it would have decided,
-- and stops. A person decides.

-- One run of the pipeline, across however many workflows it comes to span.
--
-- Nothing tied a run's steps together before this: each step wrote an
-- action_run, and the only thread between them was the lineage chain, since
-- the idempotency key hashes the workflow id beyond recovery. Splitting the
-- promotion out of the pipeline is what made that gap matter.
create table if not exists pipeline_run (
  id            uuid primary key,
  tenant_id     text not null references tenant(id),
  dataset       text not null,
  workflow_id   text not null unique,
  trigger_kind  text not null default 'manual',
  triggered_by  text references directory(id),
  schedule_id   text,
  started_at    timestamptz not null default now(),
  ended_at      timestamptz
);

-- Which pipeline definition this run is: which Temporal workflow type it
-- started, not how it was triggered (trigger_kind, above, answers that).
-- Defaults every existing row to 'deidentify', the only kind that existed
-- before this column did, so nothing already recorded needs backfilling by
-- hand. platform/api/app/pipelines.py holds the matching name -> workflow
-- type table; this constraint and that dict must list the same kinds.
alter table pipeline_run add column if not exists pipeline_kind text
  not null default 'deidentify';

-- Which dataset version(s) this run was actually started against, set once
-- at insert from what the trigger declared, never updated afterward -- the
-- same pinning discipline `action_run.input_versions` already follows, one
-- level up. This exists so `adopt_version` (worker/activities.py) has
-- something to scope a task_credential.py token against: it reads a real,
-- already-sealed dataset version but deliberately opens no `action_run` of
-- its own (it produces no sealed output, so nothing would ever close one),
-- while `pipeline_run` already opens before it runs and closes once, at the
-- end, the same reason /credentials' pipeline_action check now accepts a
-- `pipeline_run`-kind task credential alongside an `action_run`-kind one.
alter table pipeline_run add column if not exists input_versions uuid[]
  not null default '{}';

-- A pipeline an operator registered themselves, versus the two kinds the
-- platform ships with. Mirrors the dataset/dataset_version and
-- agent/agent_version split: a stable identity plus an immutable, sealed
-- version, so a run always names one exact DAG rather than "whatever this
-- pipeline currently is."
create table if not exists pipeline (
  id             uuid primary key,
  tenant_id      text not null references tenant(id),
  name           text not null,
  department_id  uuid not null references department(id),
  registered_by  text references directory(id),
  created_at     timestamptz not null default now(),
  unique (tenant_id, name)
);

-- Sealed the instant it is created. There is no editing a version in
-- place, only registering the next one, the same rule a dataset version
-- and an agent version already live under: a run's evidence has to point
-- at something that cannot have quietly changed since.
create table if not exists pipeline_version (
  id               uuid primary key,
  pipeline_id      uuid not null references pipeline(id),
  version          int not null,
  dag_config       jsonb not null,
  config_hash      text not null,
  code_object_key  text not null,
  sealed           boolean not null default true,
  created_at       timestamptz not null default now(),
  unique (pipeline_id, version)
);

-- One row per step of a running pipeline_run, whether that run is a DAG or
-- (later) anything else generic enough to want the same shape. Not
-- action_run: that table is bound to sealing a whole new dataset_version
-- with a contract and a manifest, right for transcribe/detect, wrong for a
-- step whose entire output is a five-line score.
create table if not exists pipeline_step_run (
  id               uuid primary key,
  pipeline_run_id  uuid not null references pipeline_run(id),
  step_name        text not null,
  status           text not null check (status in ('running','succeeded','failed')),
  output           jsonb,
  started_at       timestamptz not null default now(),
  ended_at         timestamptz
);

create index if not exists pipeline_step_run_run_idx
  on pipeline_step_run (pipeline_run_id);

-- Which registered DAG produced this run, when pipeline_kind = 'dag'. Null
-- for the two built-in kinds, which have no pipeline_version at all.
alter table pipeline_run add column if not exists pipeline_version_id uuid
  references pipeline_version(id);

alter table pipeline_run drop constraint if exists pipeline_run_kind_valid;
alter table pipeline_run add constraint pipeline_run_kind_valid
  check (pipeline_kind in ('deidentify', 'count_records', 'dag'));

-- Nullable on purpose: rows written before this column existed belong to runs
-- nobody recorded, and backfilling a correlation that was never observed would
-- be inventing history.
alter table action_run add column if not exists pipeline_run_id uuid
  references pipeline_run(id);

create index if not exists action_run_pipeline_idx
  on action_run (pipeline_run_id) where pipeline_run_id is not null;

-- What the machine measured, what it would have decided, and what a person
-- decided instead.
create table if not exists gate_decision (
  id                    uuid primary key,
  tenant_id             text not null references tenant(id),
  pipeline_run_id       uuid references pipeline_run(id),
  dataset_version_id    uuid not null references dataset_version(id),
  to_class              text not null
                        check (to_class in ('RAW','UNDER_REVIEW','OPEN_FOR_ANNOTATION','OPEN_FOR_TRAINING','PUBLISHED')),
  score_card_id         text not null,
  metrics               jsonb not null default '{}'::jsonb,
  recommendation        text not null check (recommendation in ('pass','fail')),
  recommendation_reason text not null,
  state                 text not null default 'pending'
                        check (state in ('pending','promoted','refused')),
  triggered_by          text references directory(id),
  decided_by            text references directory(id),
  decided_at            timestamptz,
  decision_reason       text,
  handoff               jsonb not null default '{}'::jsonb,
  created_at            timestamptz not null default now()
);

-- Separation of duties, the same rule agent_run_no_self_approval keeps.
-- triggered_by is copied onto this row rather than joined, because a check
-- constraint cannot join, and the constraint is what makes the guarantee hold
-- when the application is wrong.
do $$
begin
  alter table gate_decision
    add constraint gate_decision_no_self_decision
    check (decided_by is null or triggered_by is null
           or decided_by <> triggered_by);
exception when duplicate_object then null;
end $$;

-- A row cannot claim it was decided by nobody, nor sit pending with a decider
-- already on it.
do $$
begin
  alter table gate_decision
    add constraint gate_decision_decided_together
    check ((state = 'pending') = (decided_by is null));
exception when duplicate_object then null;
end $$;

create index if not exists gate_decision_pending_idx
  on gate_decision (tenant_id, state, created_at desc);

-- The identifiers that escaped redaction.
--
-- A table of its own rather than a column on the decision, precisely because
-- this is personal data: a table can be dropped, redacted, or given retention
-- and access rules on its own, and a field inside a larger row cannot.
create table if not exists gate_leak (
  id                 uuid primary key,
  gate_decision_id   uuid not null references gate_decision(id) on delete cascade,
  record_id          text not null,
  span_start         int not null,
  span_end           int not null,
  entity             text not null,
  identifier         text not null,
  left_in_the_clear  text not null,
  coverage           numeric not null,
  direct             boolean not null
);

create index if not exists gate_leak_decision_idx
  on gate_leak (gate_decision_id);

-- The retired-tenant rule, applied here for the same reason it is applied to
-- every other table carrying a tenant_id. gate_leak is absent because it holds
-- no tenant_id of its own: it cannot be written without a gate_decision, and
-- that write is already refused.
do $$
declare t text;
begin
  foreach t in array array['pipeline_run','gate_decision'] loop
    execute format('drop trigger if exists %I on %I', 'refuse_retired_' || t, t);
    execute format(
      'create trigger %I before insert or update on %I
         for each row execute function refuse_write_to_retired_tenant()',
      'refuse_retired_' || t, t);
  end loop;
end $$;

-- What an uploaded file turned out to be, derived from its own bytes at the
-- moment it arrived. Null for a file the platform has no opinion about, which
-- is most of them: only a name ending .wav or .truth.json is inspected.
--
-- Derived at upload rather than by a later job on purpose. To read tenant data
-- afterwards a workload must ask for a credential, and POST /credentials is
-- scoped to a sealed dataset version, which by definition does not exist while
-- somebody is still deciding whether to seal.
alter table dataset_source add column if not exists audio_duration_seconds double precision;
alter table dataset_source add column if not exists audio_sample_rate int;
alter table dataset_source add column if not exists truth_has_hazard boolean;

-- Taking one upload out of the pending set without erasing that it happened.
-- Exists so that "the set must be coherent before sealing" holds with no
-- exceptions: without it, one permanently unreadable recording blocks a
-- dataset forever, which is the kind of dead end that gets worked around
-- outside the platform.
alter table dataset_source add column if not exists withdrawn_at timestamptz;
alter table dataset_source add column if not exists withdrawn_by text references directory(id);

-- Where a console-started run began, and how it was started.
--
-- Nullable and defaulted rather than backfilled: every row that exists today
-- came from run_pipeline.py reading a corpus off a disk, so 'cli' is the truth
-- about them, and they have no source version to point at because a corpus on
-- somebody's disk is not one.
alter table pipeline_run add column if not exists source_version_id uuid
  references dataset_version(id);
alter table pipeline_run add column if not exists started_from text
  not null default 'cli';

do $$ begin
  alter table pipeline_run drop constraint if exists pipeline_run_started_from_valid;
  alter table pipeline_run add constraint pipeline_run_started_from_valid
    check (started_from in ('cli', 'console', 'schedule'));
end $$;

create index if not exists pipeline_run_source_version_idx
  on pipeline_run (source_version_id) where source_version_id is not null;

-- How the run ended, recorded when it ends, with the end time. The database
-- is where a run's outcome lives: the job runner keeps a finished workflow's
-- history only for a limited time, and a run whose outcome could only be
-- read from there would lose it. The job runner is asked only about a run
-- still in progress.
--
-- 'unknown' is for a run the job runner has no record of when it is asked,
-- and for any run that ended before this column existed.
alter table pipeline_run add column if not exists status text not null default 'running';
alter table pipeline_run add column if not exists error text;
-- A retired organisation's rows refuse writes; this fills in a fact about
-- the past rather than changing one, so the refusal is lifted for this one
-- statement only. One block, so a failure part-way undoes all of it and can
-- never leave the refusal switched off.
do $$ begin
  alter table pipeline_run disable trigger refuse_retired_pipeline_run;
  update pipeline_run set status = 'unknown'
   where ended_at is not null and status = 'running';
  alter table pipeline_run enable trigger refuse_retired_pipeline_run;
end $$;

do $$ begin
  alter table pipeline_run drop constraint if exists pipeline_run_status_valid;
  alter table pipeline_run add constraint pipeline_run_status_valid
    check (status in ('running', 'succeeded', 'failed', 'cancelled',
                      'terminated', 'timed_out', 'unknown'));
  -- Running exactly when there is no end time: a run cannot have ended
  -- without an outcome, or carry an outcome while still going.
  alter table pipeline_run drop constraint if exists pipeline_run_status_matches_end;
  alter table pipeline_run add constraint pipeline_run_status_matches_end
    check ((status = 'running') = (ended_at is null));
end $$;

-- One storage key per lease, rather than one per job title.
--
-- Six key pairs served the whole platform, one per role, so every researcher
-- carried the same card and the door could only ever be told "researchers may
-- enter". Cutting one holder meant cutting them all, which meant in practice
-- cutting nobody. A key that belongs to one lease can be withdrawn when that
-- lease ends without touching anybody else's.
--
-- The secret is sealed with the same envelope crypto record keys use. It has to
-- be stored at all because the permissions document is reprinted in full and
-- would otherwise hand every consumer a new key on every print.
create table if not exists storage_identity (
  id                 uuid primary key,
  tenant_id          text not null references tenant(id),
  lease_id           uuid references access_lease(id),
  agent_run_id       uuid references agent_run(id),
  backend            text not null default 'seaweedfs',
  identity_name      text not null unique,
  access_key_id      text not null unique,
  secret_ciphertext  bytea not null,
  secret_wrapped_key bytea not null,
  created_at         timestamptz not null default now(),
  ended_at           timestamptz
);

create index if not exists storage_identity_lease_idx
  on storage_identity (lease_id) where lease_id is not null;

-- A tenant's own ingest identity has neither a lease nor an agent run behind
-- it; the subject is the tenant itself, already carried in tenant_id. Written
-- as an explicit third disjunct rather than dropped, so the constraint still
-- states its cases instead of silently becoming unconditionally true.
do $$ begin
  alter table storage_identity drop constraint if exists storage_identity_has_a_subject;
  alter table storage_identity add constraint storage_identity_has_a_subject
    check (lease_id is not null or agent_run_id is not null
           or (lease_id is null and agent_run_id is null));
end $$;

-- At most one standing ingest identity per tenant per backend. Without this a
-- second identity_for_tenant_ingest call racing the first could mint two rows
-- for the same tenant, and the projection would then emit two S3 identities
-- both claiming to be this tenant's own key.
create unique index if not exists storage_identity_one_ingest_per_tenant
  on storage_identity (tenant_id, backend)
  where lease_id is null and agent_run_id is null;

-- A retired tenant takes no writes, and minting a per-lease storage identity is
-- a write. Guarded the same way tenant_storage_provision is, so U33's check that
-- every tenant_id-bearing table is either guarded or named as an exception stays
-- true rather than this new table becoming a silent hole.
drop trigger if exists refuse_retired_tenant_storage_identity on storage_identity;
create trigger refuse_retired_tenant_storage_identity
  before insert or update on storage_identity
  for each row execute function refuse_write_to_retired_tenant();

-- A closed organisation grants no roles, takes no new asks, and gains no
-- pipelines. Guarded the same way every other tenant-scoped table is, so
-- U33's check that each one is either guarded or named as an exception stays
-- true rather than these becoming holes that let a retired tenant be written
-- to by the back door.
--
-- At the end of the file rather than beside each table because
-- refuse_write_to_retired_tenant is defined further down than role_request
-- and role_grant are, and a trigger cannot name a function that does not
-- exist yet.
do $$
declare t text;
begin
  foreach t in array array['role_request','role_grant','pipeline'] loop
    execute format('drop trigger if exists %I on %I', 'refuse_retired_' || t, t);
    execute format(
      'create trigger %I before insert or update on %I
         for each row execute function refuse_write_to_retired_tenant()',
      'refuse_retired_' || t, t);
  end loop;
end $$;

-- Storage access: approved is not the same as active.
--
-- Every print of the SeaweedFS permissions document, successful or not. An
-- allowed access decision is in effect once a successful print started after
-- it: a print compiles from the register as it stands when it starts, so only
-- one that began later can contain it. That is a query over this table, not a
-- second copy of the state, so the two cannot disagree. The same rows answer
-- when permissions last updated and how long they have been failing.
create table if not exists storage_permission_print (
  id           bigserial primary key,
  started_at   timestamptz not null default clock_timestamp(),
  finished_at  timestamptz,
  succeeded    boolean,            -- null while the print is running
  retryable    boolean,            -- set on failure: false means retrying cannot fix it
  trigger      text not null check (trigger in
                 ('request', 'provision', 'promotion', 'start-up', 'activator', 'manual', 'revocation')),
  identities   int,
  reason       text
);
-- A lease that is revoked prints at once, so its key stops working then and not at the
-- next unrelated print. Applied to an existing table too, because the list above is
-- only read when the table is first created.
alter table storage_permission_print drop constraint if exists storage_permission_print_trigger_check;
alter table storage_permission_print add constraint storage_permission_print_trigger_check
  check (trigger in ('request', 'provision', 'promotion', 'start-up', 'activator', 'manual', 'revocation'));
create index if not exists storage_permission_print_success_idx
  on storage_permission_print (started_at desc) where succeeded;

-- A run whose storage access was allowed but not yet in effect waits here and
-- is resumed by the platform once a successful print started after this
-- moment. Recorded when the run parks, which is after the decision it waits
-- on, so any print that started later contains that decision.
alter table agent_run add column if not exists awaiting_activation_since timestamptz;

-- One piece of agent work (a first run, a resume, a sandboxed run), recorded
-- before it starts and when it ends. Temporal can lose a hand-over or a
-- "done" when its database is slow, and retries the step when that happens;
-- this record is what makes the retry safe. A piece already finished returns
-- its recorded outcome without running again, and one begun but never
-- finished fails the run instead of repeating its tool calls
-- (worker/agent_attempt.py). `segment` names the Temporal execution and step,
-- which stay the same across retries of one step.
create table if not exists agent_run_attempt (
  segment      text primary key,
  run_id       uuid not null references agent_run(id),
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  outcome      jsonb
);

-- A HuggingFace fetch can now be cancelled from the console instead of only
-- running to completion or failure. 'cancelled' is distinct from 'failed':
-- nothing went wrong, somebody chose to stop it, the same distinction
-- pipeline_run's own status column already makes.
do $$ begin
  alter table huggingface_fetch_job drop constraint if exists huggingface_fetch_job_status_check;
  alter table huggingface_fetch_job add constraint huggingface_fetch_job_status_check
    check (status in ('running', 'succeeded', 'failed', 'cancelled'));
end $$;
create index if not exists agent_run_attempt_run_idx on agent_run_attempt (run_id);

-- Distinct from `reason`, which is only ever a refusal's own message
-- (`_finish_print` sets `succeeded = (reason is null)`, so a reason on a
-- successful print would misrecord it as failed). A print that exceeded
-- MAX_SHRINK but was let through anyway, because the whole excess traced to
-- leases that have since expired or been revoked, still needs that fact on
-- the record: an administrator reading storage_permission_print later
-- should be able to tell "removed a lot, but for a recognised reason, no
-- person involved" apart from an ordinary quiet print.
alter table storage_permission_print add column if not exists auto_explained text;

-- ------------------------------------------- agent egress allowlist --
--
-- Which external hosts an agent version may call, and who approved them.
-- See docs/internal/diagrams/agent-egress-allowlist and access.rego's
-- egress_approval_decision. A network tool like fetch_url (agent/tools.py)
-- is only as safe as the network it happens to run on today; this is the
-- governance half of making it safe by construction instead -- declared at
-- registration, approved by a named role before deploy, the same
-- "declare it, somebody else decides" shape a lease request or a
-- de-identification gate already uses.
--
-- Declared at version registration, not edited afterward: agent_version is
-- sealed the moment it exists (agent_version_no_update above), so its
-- requested_hosts is fixed the same way its code_hash is. A version that
-- wants a different host list is a new version, not an edit.
alter table agent_version add column if not exists requested_hosts text[] not null default '{}';

-- One row per approval request, append-only like every decision table
-- here. requested_hosts is copied onto this row (not read from
-- agent_version by joining) so the record of what was actually reviewed
-- survives even though it happens to equal the version's own column today;
-- the two are expected to agree, and a future superseding request against
-- the same version does not have to mean the same thing was asked twice.
create table if not exists agent_egress_approval (
  id                uuid primary key,
  tenant_id         text not null references tenant(id),
  agent_id          uuid not null references agent(id),
  agent_version_id  uuid not null references agent_version(id),
  requested_hosts   text[] not null,
  submitted_by      text not null references directory(id),
  state             text not null default 'pending'
                    check (state in ('pending', 'approved', 'refused')),
  decided_by        text references directory(id),
  decided_at        timestamptz,
  decision_reason   text,
  created_at        timestamptz not null default now()
);

-- Separation of duties, enforced here and not only in access.rego's
-- egress_approval_decision, the same two-layer guarantee gate_decision and
-- agent_run already use: this is what holds even if the API code that
-- calls OPA is wrong.
do $$
begin
  alter table agent_egress_approval
    add constraint agent_egress_approval_no_self_decision
    check (decided_by is null or decided_by <> submitted_by);
exception when duplicate_object then null;
end $$;

do $$
begin
  alter table agent_egress_approval
    add constraint agent_egress_approval_decided_together
    check ((state = 'pending') = (decided_by is null));
exception when duplicate_object then null;
end $$;

-- A version's requested hosts are fixed at creation, so at most one
-- approval request per version is ever waiting on somebody at once.
create unique index if not exists agent_egress_approval_one_pending_per_version
  on agent_egress_approval (agent_version_id) where state = 'pending';

create index if not exists agent_egress_approval_tenant_idx
  on agent_egress_approval (tenant_id, state, created_at desc);

-- A retired tenant's agents take no new approvals either: deciding what a
-- closed organisation's agent may reach on the network is not a decision
-- that still needs making. Guarded the same way storage_identity is, so
-- U33's check that every tenant_id-bearing table is either guarded or named
-- as an exception stays true rather than this table becoming a silent hole.
drop trigger if exists refuse_retired_agent_egress_approval on agent_egress_approval;
create trigger refuse_retired_agent_egress_approval
  before insert or update on agent_egress_approval
  for each row execute function refuse_write_to_retired_tenant();

-- Proof that a `POST /credentials` request naming this run is actually the
-- code this run launched, not merely something that learned this run's id.
-- Before this, an `agent_runtime` principal's credential request was already
-- checked against a real, resolved `agent_run` row (see the run-scope check
-- in main.py), but nothing about that row was secret: anything that could
-- read or guess a run id could ask for that run's credentials.
--
-- Holds a signed platform/api/app/task_credential.py token, not a plain
-- random string compared by equality: main.py's check verifies the token's
-- signature and expiry, then looks up the run by the task id the token
-- itself carries, never by comparing this column's value against something
-- the caller presents. That is also why this column is re-minted, not only
-- ever set once: a run parked `awaiting_access` or `awaiting_activation` can
-- sit for as long as a custodian takes to decide, longer than the token's
-- own short TTL, so agents.py mints a fresh one and writes it back here each
-- time such a run resumes. A native (non-sandboxed) run's activities
-- (worker/agent_run_activities.py's _context_for) read this column directly
-- on every tool call rather than anything a Temporal workflow started with,
-- which is the reason it is a column at all and not only a value handed to
-- the workflow once. Nullable so a run row from before this existed is not
-- retroactively broken; main.py's check refuses a request with no token
-- rather than treating a null column as an exemption.
alter table agent_run add column if not exists run_secret text;

-- Two shapes of grant, chosen by the custodian who approves the request, not
-- by the platform for everyone at once.
--
-- "strict" is every lease this platform ever granted before this column
-- existed (the default): it covers the one purpose it names, and a run
-- asking for anything else needs its own approval. That is the right shape
-- for data a custodian cannot yet vouch for beyond the specific request in
-- front of them.
--
-- "simple" exists because that same rule, applied to every principal
-- forever, is what makes a queue somebody rubber-stamps rather than reads: a
-- workload a custodian already trusts, doing recognisably the same job every
-- day, does not need a fresh decision every time its own purpose text is
-- reworded. A custodian who has that trust can say so once, and the lease
-- then covers any purpose until it expires -- the same judgment call a
-- resource owner already makes when handing a service a broad role instead
-- of a narrow one, made here per lease rather than platform-wide.
alter table access_lease add column if not exists pattern text not null default 'strict';

do $$
begin
  alter table access_lease
    add constraint access_lease_pattern_shape check (pattern in ('strict', 'simple'));
exception when duplicate_object then null;
end $$;

-- The one shape of data a custodian's trust in a principal must not be able
-- to override: raw, unreviewed, identifiable data always asks again per
-- purpose, no matter how long a workload has been in service. Enforced here,
-- not only in the API branch that also refuses it -- the same two-layer
-- guarantee `refuse_standing_lease_for_human` above already holds this
-- table to.
create or replace function refuse_simple_pattern_for_raw() returns trigger as $$
declare
  version_class_now text;
begin
  if new.pattern = 'simple' then
    select current_class into version_class_now
      from version_class where dataset_version_id = new.dataset_version_id;
    if version_class_now = 'RAW' then
      raise exception
        'a simple (any-purpose) lease may not be granted against a RAW dataset version; % is RAW',
        new.dataset_version_id
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists access_lease_no_simple_for_raw on access_lease;
create trigger access_lease_no_simple_for_raw
  before insert or update on access_lease
  for each row execute function refuse_simple_pattern_for_raw();

-- ------------------------------------------------------------ iceberg --
--
-- A sealed tabular version, also written as an Iceberg table, so a standard
-- tool can read it (platform/api/app/iceberg.py). The version row stays the
-- authority: this row says where the table is and what it held when it was
-- written, and a verification script compares the two.
--
-- Written once, with the version. The table's files live under the version's
-- own storage prefix and are part of its object manifest, so the grant that
-- covers the version covers the table, and the content hash covers the files.
-- Nothing is written under a sealed prefix afterwards, which is why a tag or
-- a branch is never moved after sealing.
--
-- One table per version, not one per dataset with a snapshot per version: a
-- credential is granted for one version's prefix, and a table spanning
-- versions would put other versions' files inside the table a reader opens.
create table if not exists iceberg_table_ref (
  dataset_version_id uuid primary key
                     references dataset_version(id) on delete cascade,
  tenant_id          text not null references tenant(id),
  dataset_id         uuid not null references dataset(id),
  namespace          text not null,          -- the dataset's name
  table_name         text not null,          -- 'v' and the version number
  location           text not null,          -- s3://bucket/prefix/iceberg
  metadata_location  text not null,          -- the table's current metadata file
  snapshot_id        bigint not null,
  format_version     int not null,
  record_count       bigint not null,
  records_sha256     text not null,          -- of the records object the rows came from
  projected_at       timestamptz not null default now(),
  unique (tenant_id, namespace, table_name)
);

create or replace function refuse_iceberg_table_ref_update() returns trigger as $$
begin
  raise exception 'iceberg_table_ref rows are written once, with the version they describe'
    using errcode = 'check_violation';
end;
$$ language plpgsql;

drop trigger if exists iceberg_table_ref_write_once on iceberg_table_ref;
create trigger iceberg_table_ref_write_once
  before update on iceberg_table_ref
  for each row execute function refuse_iceberg_table_ref_update();

-- What a person's own tool presents to the Iceberg catalog. Only a hash of the
-- token is stored, so reading this table cannot reveal a usable token. A token
-- names a person and a purpose and nothing else: every table it opens is still
-- decided on its own, by the same policy and leases as any other read.
create table if not exists catalog_token (
  id           uuid primary key,
  token_hash   text not null unique,       -- sha256, hex
  principal    text not null references directory(id),
  tenant_id    text not null references tenant(id),
  purpose      text not null,
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null,
  revoked_at   timestamptz,
  last_used_at timestamptz
);

create index if not exists catalog_token_principal_idx on catalog_token (principal);

-- A retired organisation may not gain a new token or a new table reference,
-- the same as every other table that carries its tenant_id.
do $$
declare t text;
begin
  foreach t in array array['iceberg_table_ref','catalog_token'] loop
    execute format('drop trigger if exists %I on %I', 'refuse_retired_' || t, t);
    execute format(
      'create trigger %I before insert or update on %I
         for each row execute function refuse_write_to_retired_tenant()',
      'refuse_retired_' || t, t);
  end loop;
end $$;

-- The storage keys the Iceberg catalog hands out. One row per person and per
-- version: the identity it names holds a rolling series of keys, each valid for
-- a stretch of time, derived from a secret nobody stores (grants.py). The row
-- says only that the series is wanted and which epoch it was last asked for, so
-- the series ends by itself once nobody asks, and at once when the lease it
-- rests on ends.
--
-- Not guarded against a retired organisation, and named as an exception in U33:
-- a closed organisation's records stay readable, and reading them through the
-- catalog needs this row. It holds no records and grants nothing a lease or a
-- role did not already decide.
create table if not exists catalog_key (
  id                 uuid primary key,
  tenant_id          text not null references tenant(id),
  principal          text not null,
  dataset_version_id uuid not null references dataset_version(id) on delete cascade,
  lease_id           uuid references access_lease(id),
  identity_name      text not null unique,
  issued_epoch       bigint not null,
  created_at         timestamptz not null default now(),
  unique (principal, dataset_version_id)
);

