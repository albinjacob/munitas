# Security Policy

Munitas governs access to sensitive data and controls what AI agents and
pipelines are allowed to read. A security issue here is not hypothetical, so
please report it privately rather than as a public issue.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.**

Report it through GitHub's private vulnerability reporting: open the
repository's **Security** tab and choose **Report a vulnerability**. Only the
maintainers see what you send.

Please include:

- What component is affected (the API, the console, the worker, a specific
  policy in `platform/policy/`, and so on)
- Steps to reproduce, or a proof of concept
- What you believe the impact is: what could an attacker actually do

## What's in scope

- Anything that lets a request bypass the policy engine (`platform/policy/`)
- Anything that lets one tenant read another tenant's data
- Anything that lets a sandboxed agent or pipeline script reach the network,
  the host filesystem, or another run's credentials outside its granted scope
- Anything that lets a run's own operator clear its own gate decision
  (segregation of duties is meant to be structurally enforced, not just
  policy-enforced)
- Leaked credentials, secrets, or master keys in the repository itself

## What's out of scope

- Issues that require an attacker to already have valid credentials for the
  tenant they're attacking, unless the issue is a privilege escalation
  within that tenant
- Denial of service against a self-hosted single-machine dev instance
- Issues that only reproduce with the development defaults

## Development defaults

Every password, key and secret written into this repository is a
development default, for running the platform on your own machine, whether
or not its value says so. That covers every value in `.env.example`, every
fallback in `docker-compose.yml`, the shared sign-in password the seed
scripts give each example person, and the Kratos cookie and encryption
secrets in `infra/kratos/kratos.yml.template`. Some are labelled
`not-for-production`; the database login (`munitas` / `munitas`) and the
storage admin key (`munitas-admin` / `munitas-admin-secret`) are not, and are
no less public. Replace all of them before running Munitas anywhere other
people can reach.

## Response

This is a young, mostly-solo project. There is no formal service level yet,
so please be patient, and expect an acknowledgment rather than an immediate
fix.
