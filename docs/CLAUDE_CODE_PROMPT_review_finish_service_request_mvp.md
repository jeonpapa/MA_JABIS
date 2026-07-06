# Claude Code Prompt — Review & Finish MAx AI Dashboard Service Request MVP

You are Claude Code working in the MA_JABIS service-request MVP worktree.

## Repository / worktree

```bash
cd /opt/data/MA_JABIS_service_request_mvp
```

Remote: `https://github.com/jeonpapa/MA_JABIS.git`  
Branch: `feature/service-request-mvp`  
Production app: `https://ma-ai-dossier.fly.dev` / Fly app `ma-ai-dossier`

Do **not** deploy or push unless Joseph/Hermes explicitly approves.

## Mission

Review, harden, and finish the already-started dashboard-wide **서비스 보완 요청 / 개선 요청** MVP.

The MVP goal is:

```text
Authenticated user on any MAx AI Dashboard page
→ clicks global “개선 요청” button
→ route/page/user/time/context is captured
→ user submits title/description/expected outcome/type/priority
→ user can see own requests/status
→ admin/Joseph triages, edits Claude package, performs final confirmation checklist
→ app stores/returns a Claude-ready markdown handoff package and marks request as sent
→ audit trail is preserved
```

## Current implementation exists but is uncommitted

Run first:

```bash
git status --short
git diff --stat
```

Expected current modified/tracked files:

```text
api/server.py
frontend/src/App.tsx
frontend/src/api/client.ts
frontend/src/components/feature/Sidebar.tsx
frontend/src/router/config.tsx
frontend/src/utils/authUsers.ts
```

Expected new files:

```text
agents/service_requests/__init__.py
agents/service_requests/store.py
frontend/src/api/serviceRequests.ts
frontend/src/components/service-requests/ServiceRequestButton.tsx
frontend/src/components/service-requests/ServiceRequestModal.tsx
frontend/src/components/service-requests/pageLabels.ts
frontend/src/pages/admin/service-requests/page.tsx
frontend/src/pages/service-requests/mine.tsx
tests/test_service_requests_store.py
tests/test_service_requests_api.py
```

## Non-negotiable MVP scope

Confirm/fix these items:

1. Floating global `개선 요청` button appears for authenticated users, not on login.
2. Request modal captures current route/path/query/page label/source URL/requester email/timestamp.
3. User form stores title, description, expected outcome, request type, priority, module/context.
4. Regular users can list/view only their own requests.
5. Admins can list/filter/view/update all requests.
6. Admins can generate/edit/save a Claude handoff draft/final package.
7. Final confirmation checklist is required before `send-to-claude` transition.
8. `send-to-claude` MVP does **not** call an external Claude service; it stores/returns final markdown and marks status as sent.
9. Audit events are written for create/update/package/confirm/send.
10. Existing Analog Search feedback remains intact and separate.

## Explicitly out of scope

Do not add these in this pass:

- Automatic screenshot capture.
- Attachments.
- AI auto-classification.
- Slack/email/Jira/Linear integration.
- Multi-approver workflow.
- Direct Claude API / Claude Code execution from the app.
- Deployment or git push.

## Files/patterns to inspect

Backend:

```text
api/server.py
agents/service_requests/store.py
api/auth.py
```

Frontend:

```text
frontend/src/App.tsx
frontend/src/router/config.tsx
frontend/src/components/feature/Sidebar.tsx
frontend/src/api/client.ts
frontend/src/api/serviceRequests.ts
frontend/src/components/service-requests/*
frontend/src/pages/service-requests/mine.tsx
frontend/src/pages/admin/service-requests/page.tsx
frontend/src/utils/authUsers.ts
```

Existing narrow feedback reference only:

```text
frontend/src/pages/analog-search/page.tsx
frontend/src/api/analog.ts
api/server.py routes around /api/analog/search-feedback
```

## Acceptance criteria

Backend/API:

- `POST /api/service-requests` requires auth and creates a request.
- `GET /api/service-requests/mine` returns only requester-owned records.
- `GET /api/service-requests/<id>` allows owner or admin only.
- `GET /api/admin/service-requests` requires admin.
- `PATCH /api/admin/service-requests/<id>` requires admin and records audit event.
- `POST /api/admin/service-requests/<id>/claude-package` supports generate/save draft/save final.
- `POST /api/admin/service-requests/<id>/confirm` requires complete checklist.
- `POST /api/admin/service-requests/<id>/send-to-claude` fails before confirmation and succeeds after confirmation by storing/returning final markdown.

Frontend:

- User can submit request from normal dashboard page.
- User can open `내 개선 요청` and see status/detail/timeline.
- Admin can open `서비스 보완 요청`, filter/list/detail/edit, generate/copy/save Claude package, confirm, and mark sent.
- Admin-only UI/route is not exposed to regular users beyond safe redirects/guards.

Safety:

- Do not store auth tokens/cookies/passwords in `context_json`.
- Redact sensitive context keys such as token, authorization, password, secret, api_key, jwt, session, cookie, auth.
- Generated Claude package includes safety/redaction checklist and explicit “do not deploy without approval”.

## Validation commands

Run at minimum:

```bash
cd /opt/data/MA_JABIS_service_request_mvp
python3 -m py_compile api/server.py agents/service_requests/store.py
python3 -m pytest tests/test_service_requests_store.py -q

cd /opt/data/MA_JABIS_service_request_mvp/frontend
npm run type-check
npm run build
```

If Flask/test dependencies are available, also run:

```bash
cd /opt/data/MA_JABIS_service_request_mvp
python3 -m pytest tests/test_service_requests_api.py -q
```

If API tests fail because Flask is missing in the execution environment, state that clearly and do not mark the code as untested; report store/type/build results separately.

## Required final response

Report in this exact structure:

```md
## Summary
<what you changed/fixed>

## Files changed
- ...

## API endpoints verified
- ...

## Validation
- `command` — pass/fail and key output

## Risks / follow-up
- ...

## Deployment / push status
Not deployed. Not pushed.
```
