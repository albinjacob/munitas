# Munitas console

## Identity

There is one identity mechanism: a real login at `/auth/login`, backed by
Ory Kratos (`docker compose up -d kratos-migrate kratos` first). Sign in as
any of the identities `infra/kratos/seed-identities.py` connected (every
tenant it covers, read from `infra/kratos/identities.json`), which all
share one password, printed by that script. This is what a real visitor
sees; there is no other way in.

```powershell
npm run dev
```

An earlier version of this console had a second mode: a locally-picked
identity in localStorage, kept for exploration without Kratos running.
It was retired once every session-gated write endpoint (`request_lease`
and friends, platform/api/app/auth.py's `current_session`) required a real
session regardless. A picked-but-unproven identity could no longer exercise those flows at all,
so keeping it meant a button that looked clickable and silently failed.

## Running the Playwright suite

One dev server, one mode, every suite runs against it:

```powershell
npm run dev
npx playwright test
```

---

# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.
