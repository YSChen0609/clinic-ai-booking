# OAuth setup — Google + Outlook (stage 6)

Local `.env` only (from `.env.example`). **Do not commit secrets.**  
Unit tests use fakes; this is for `NOTIFY_MODE=real` smoke → [smoke_test.md](smoke_test.md#google--outlook-notify-stage-6--real-credentials-local-only).

Prefer a dedicated clinic mailbox. Personal Gmail is OK for local-only testing.  
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

This app calls **Microsoft Graph** as the **clinic mailbox**: create calendar events (patient as attendee) + `sendMail`. No patient OAuth.

Refs: [Client credentials](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow) · [Entra admin center](https://entra.microsoft.com/) · [Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-reference) · [Create event](https://learn.microsoft.com/en-us/graph/api/user-post-events) · [sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail) · [Admin consent](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent)

### Prerequisites

| Need | Why |
|------|-----|
| Microsoft Entra **tenant** (work/school) | Holds the app registration |
| A **mailbox** in that tenant | Events/mail are created as this user |
| Someone who can **Grant admin consent** | Required for Application permissions |

**Personal `@outlook.com` / consumer Microsoft accounts are a poor fit** for the documented app-only path. For a quick personal smoke, use **Google only**. For Outlook use:

- Microsoft 365 business/education trial, or  
- [Microsoft 365 Developer Program](https://developer.microsoft.com/en-us/microsoft-365/dev-program) (sandbox tenant + users)

### Env vars

| Var | Required? | Notes |
|-----|-----------|--------|
| `MS_TENANT_ID` | Yes | Directory (tenant) ID (GUID) |
| `MS_CLIENT_ID` | Yes | Application (client) ID (GUID) |
| `MS_CLIENT_SECRET` | Yes | Secret **Value** (shown once at creation) |
| `MS_USER_UPN` | Yes | Clinic mailbox UPN, e.g. `clinic@contoso.onmicrosoft.com` |
| `MS_CALENDAR_ID` | No | Empty = that user’s **default** calendar |
| `MS_REFRESH_TOKEN` | No for smoke | Leave **empty** for app-only (Path A) |

Code treats Outlook as ready when tenant + client id + secret + user UPN are all set.

### Path A (recommended): app-only / client credentials

No browser login when booking. The app uses client id + secret and calls Graph as `MS_USER_UPN`.

#### 1. Register the app

1. Open [Entra admin center](https://entra.microsoft.com/) as a tenant admin.
2. **Identity** → **Applications** → **App registrations** → **New registration**.
3. Name: e.g. `clinic-ai-booking-notify`.
4. Supported account types: **Accounts in this organizational directory only** (single tenant).
5. Redirect URI: not needed for client credentials — leave blank.
6. **Register**.

#### 2. Copy IDs (Overview)

- **Application (client) ID** → `MS_CLIENT_ID`
- **Directory (tenant) ID** → `MS_TENANT_ID`

Do not use **Object ID** for these env vars.

#### 3. Client secret

1. **Certificates & secrets** → **Client secrets** → **New client secret**.
2. Add description + expiry → **Add**.
3. Copy the **Value** immediately → `MS_CLIENT_SECRET`.  
   (Secret **ID** ≠ secret. The value is hidden after you leave the page.)

Rotate before expiry; update `.env` and recreate the app.

#### 4. API permissions — Application (not Delegated)

1. **API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions**.
2. Add:
   - `Calendars.ReadWrite` — create/update/delete calendar events  
   - `Mail.Send` — send mail (we send as `MS_USER_UPN`)
3. **Add permissions**.
4. **Grant admin consent for \<tenant\>** → confirm. Both rows need a green granted status.

Without admin consent, token acquisition may work but Graph calls often return **403**.

#### 5. Clinic mailbox (`MS_USER_UPN`)

Use the **User principal name** of a mailbox in the tenant (often the sign-in email):

- Examples: `clinic@yourtenant.onmicrosoft.com`, `frontdesk@yourdomain.com`
- Entra → **Users** → user → copy **User principal name**
- Events land on that mailbox’s Outlook calendar; mail is sent From that address

The patient is only an attendee / email recipient (no Entra account required).

#### 6. Optional `MS_CALENDAR_ID`

Leave empty to use the default calendar (`/users/{upn}/events`).

For a secondary calendar, list calendars (with a working token):

`GET https://graph.microsoft.com/v1.0/users/{UPN}/calendars`

Copy the calendar `id` into `MS_CALENDAR_ID`.

#### 7. Leave `MS_REFRESH_TOKEN` empty

Path A uses:

`grant_type=client_credentials` + `scope=https://graph.microsoft.com/.default`

### Path B (optional): delegated + refresh token

Only if the tenant forbids application `Mail.Send` / calendar app roles. Then: interactive login once as the clinic user, store `MS_REFRESH_TOKEN`, and use **Delegated** `Calendars.ReadWrite` + `Mail.Send` + `offline_access`. Supported in code; prefer Path A when admin consent is available.

### Outlook-only `.env` example

```env
NOTIFY_MODE=real
EMAIL_PROVIDER=outlook
MS_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MS_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MS_CLIENT_SECRET=your-secret-value
MS_USER_UPN=clinic@yourtenant.onmicrosoft.com
# MS_CALENDAR_ID=
# MS_REFRESH_TOKEN=
```

With Google **and** Outlook set, calendars dual-write; `EMAIL_PROVIDER=auto` prefers Outlook for patient email when both are ready.

### Optional: verify token + mailbox

```bash
curl -s -X POST "https://login.microsoftonline.com/TENANT_ID/oauth2/v2.0/token" \
  -d "client_id=CLIENT_ID" \
  -d "client_secret=CLIENT_SECRET" \
  -d "grant_type=client_credentials" \
  -d "scope=https://graph.microsoft.com/.default"
```

Expect JSON with `access_token`. Then:

```bash
curl -s -H "Authorization: Bearer ACCESS_TOKEN" \
  "https://graph.microsoft.com/v1.0/users/MS_USER_UPN/mailboxSettings"
```

| Result | Meaning |
|--------|---------|
| 200 | UPN has a mailbox the app can reach |
| 404 | Bad UPN or no Exchange mailbox |
| 403 | Missing Application permissions or admin consent |

### Common failures

| Symptom | Likely cause |
|---------|----------------|
| Logs still show `fake_calendar` / `fake_email` | `MS_*` commented/empty, or app not recreated after `.env` change |
| `Microsoft OAuth token request failed` | Wrong tenant/client/secret, or secret expired |
| Graph **403** on events / sendMail | Not **Application** permissions, or no **admin consent** |
| Graph **404** on user | Wrong `MS_USER_UPN` or user has no mailbox |
| Graph Explorer works, app fails | You tested as a **user** (delegated); app uses **application** roles |
| Tenant `common` / `consumers` | Client credentials need the real **Directory (tenant) ID** |

### After Outlook `.env` is filled

```bash
docker compose up -d --force-recreate app
docker compose logs app --tail 50
```

Confirm notify wiring uses Outlook adapters (not only fakes), then [smoke_test.md — stage 6](smoke_test.md#google--outlook-notify-stage-6--real-credentials-local-only).

---

## `.env` then smoke

**Google-only:**

```env
NOTIFY_MODE=real
EMAIL_PROVIDER=auto
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
GOOGLE_CALENDAR_ID=primary
GOOGLE_SENDER=you@gmail.com
```

**Outlook-only:** see Path A example above. **Both:** fill `GOOGLE_*` and `MS_*`.

```bash
docker compose up -d --force-recreate app
```

Then [smoke_test.md — stage 6](smoke_test.md#google--outlook-notify-stage-6--real-credentials-local-only).
