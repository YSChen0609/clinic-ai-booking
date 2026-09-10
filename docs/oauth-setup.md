# OAuth setup — Google + Outlook (stage 6)

Local `.env` only (from `.env.example`). **Do not commit secrets.**  
Unit tests use fakes; this is for `NOTIFY_MODE=real` smoke → [smoke_test.md](smoke_test.md#google--outlook-notify-stage-6--real-credentials-local-only). Clinic-facing summary: [../external.md](../external.md).

Prefer a dedicated clinic mailbox. Personal Gmail is OK for local-only Google testing.  
After editing `.env`, restart: `docker compose up -d --force-recreate app`.

You can enable **Google only**, **Outlook only**, or both.

---

## Google (`GOOGLE_*`)

| Var | Required | Source |
|-----|----------|--------|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Yes | Cloud Console OAuth client |
| `GOOGLE_REFRESH_TOKEN` | Yes | Playground (below) — app has no browser login |
| `GOOGLE_CALENDAR_ID` | Yes | Use `primary` (not in Cloud Console) |
| `GOOGLE_SENDER` | Recommended | Same Gmail you authorize |

Refs: [OAuth overview](https://developers.google.com/identity/protocols/oauth2) · [Cloud Console](https://console.cloud.google.com/) · [Playground](https://developers.google.com/oauthplayground/)

### Client id / secret

1. Enable **Calendar API** + **Gmail API**.
2. OAuth consent screen: **External**, **Testing**, add your Gmail as **Test user**.
3. Create OAuth client → type **Web application** (not Desktop).
4. Authorized redirect URI (exact):

   `https://developers.google.com/oauthplayground`

5. Copy client id + secret into `.env`.

`redirect_uri_mismatch` / 已封鎖存取權 → missing that redirect URI, or you used a Desktop client with Playground.

### Refresh token (both scopes in one authorize)

1. Open [OAuth Playground](https://developers.google.com/oauthplayground/).
2. Gear → **Use your own OAuth credentials** → paste id/secret.
3. **Step 1:** use **Input your own scopes** (skip the long list). Paste:

   ```text
   https://www.googleapis.com/auth/calendar
   https://www.googleapis.com/auth/gmail.send
   ```

   **Authorize APIs** → sign in → Allow.  
   (One refresh token must cover **both**; don’t authorize calendar alone then try to “add” Gmail later.)
4. **Step 2:** **Exchange authorization code for tokens** → copy **Refresh token**.
5. Skip Step 3.

If no refresh token: [revoke app access](https://myaccount.google.com/permissions) and authorize again.

### Calendar id

Not in Cloud Console. For smoke:

```env
GOOGLE_CALENDAR_ID=primary
```

Other calendar: [Google Calendar](https://calendar.google.com) → calendar ⋮ → **Settings and sharing** → **Integrate calendar** → Calendar ID.

---

## Outlook / Microsoft Graph (`MS_*`)

**Assume** you already have a Microsoft 365 org with a working **mailbox** (not Entra ID Free alone) and admin access. Getting that tenant is outside this doc:

- [Try or buy Microsoft 365](https://learn.microsoft.com/en-us/microsoft-365/commerce/try-or-buy-microsoft-365) (Business Basic trial is enough)
- [M365 Developer Program](https://developer.microsoft.com/en-us/microsoft-365/dev-program) ([who qualifies](https://learn.microsoft.com/en-us/office/developer-program/microsoft-365-developer-program-faq#who-qualifies-for-a-microsoft-365-e5-developer-subscription-))
- Cancel a trial before it bills: [Cancel your subscription](https://learn.microsoft.com/en-us/microsoft-365/commerce/subscriptions/cancel-your-subscription)

You need a licensed user whose Outlook works at [outlook.office.com](https://outlook.office.com/). Entra ID Free **without** Business Basic/Standard (etc.) has no mailbox — skip until mail works.

| Var | Required | Source |
|-----|----------|--------|
| `MS_TENANT_ID` | Yes | Entra → Overview → **Directory (tenant) ID** |
| `MS_CLIENT_ID` | Yes | App registration → Overview → **Application (client) ID** |
| `MS_CLIENT_SECRET` | Yes | App registration → Certificates & secrets → secret **Value** |
| `MS_USER_UPN` | Yes | Mailbox user principal name, e.g. `admin@org.onmicrosoft.com` |
| `MS_CALENDAR_ID` | No | Leave empty for default calendar |
| `MS_REFRESH_TOKEN` | No | Leave **empty** (app-only / client credentials) |

Refs: [Register an app](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app) · [Client credentials](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow) · [Add credentials (secret)](https://learn.microsoft.com/en-us/graph/auth-register-app-v2) · [Admin consent](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent) · [Entra admin center](https://entra.microsoft.com/)

### Tenant id + mailbox UPN

1. Sign in as tenant admin → [Entra admin center](https://entra.microsoft.com/).
2. **Overview** → copy **Tenant ID** → `MS_TENANT_ID`.
3. **Users** → clinic mailbox user → copy **User principal name** → `MS_USER_UPN`.

### Client id / secret + Graph permissions

Follow Microsoft’s [app registration quickstart](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app) (single-tenant; no redirect URI needed for client credentials), then:

1. Overview → **Application (client) ID** → `MS_CLIENT_ID` (not Object ID).
2. **Certificates & secrets** → **New client secret** → copy **Value** once → `MS_CLIENT_SECRET`.
3. **API permissions** → Microsoft Graph → **Application** permissions (not Delegated):
   - `Calendars.ReadWrite`
   - `Mail.Send`
4. **Grant admin consent** for the tenant (green checks).

Leave `MS_REFRESH_TOKEN` empty. Optional `MS_CALENDAR_ID`: only if not using the default calendar (`GET /users/{upn}/calendars`).

### Quick check (optional)

```bash
curl -s -X POST "https://login.microsoftonline.com/TENANT_ID/oauth2/v2.0/token" \
  -d "client_id=CLIENT_ID&client_secret=CLIENT_SECRET" \
  -d "grant_type=client_credentials&scope=https://graph.microsoft.com/.default"
```

Then `GET https://graph.microsoft.com/v1.0/users/MS_USER_UPN/mailboxSettings` with the access token → **200** means mailbox is reachable.

| Symptom | Likely cause |
|---------|----------------|
| Still `fake_calendar` in app logs | `MS_*` empty/commented, or app not recreated |
| Token request failed | Bad tenant/client/secret |
| Graph **403** | Missing Application permissions or admin consent |
| Graph **404** | Bad UPN or no mailbox license |

---

## `.env` then smoke

```env
NOTIFY_MODE=real
EMAIL_PROVIDER=auto
# Google and/or:
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
GOOGLE_CALENDAR_ID=primary
GOOGLE_SENDER=you@gmail.com
# Outlook and/or:
MS_TENANT_ID=...
MS_CLIENT_ID=...
MS_CLIENT_SECRET=...
MS_USER_UPN=admin@yourorg.onmicrosoft.com
```

Both set → dual-write calendars; `EMAIL_PROVIDER=auto` prefers Outlook for the one patient email.

```bash
docker compose up -d --force-recreate app
```

Then [smoke_test.md — stage 6](smoke_test.md#google--outlook-notify-stage-6--real-credentials-local-only).
