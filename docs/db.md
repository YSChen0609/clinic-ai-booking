# Database queries

Compose Postgres (`clinic` DB). Host port is `${POSTGRES_PORT}` in `.env` (often `5433`).

```bash
# One-shot (from clinic-ai-booking/)
docker compose exec -T db psql -U clinic -d clinic -c "SQL_HERE"

# Interactive
docker compose exec db psql -U clinic -d clinic
```

## All tables (quick peek)

```bash
docker compose exec -T db psql -U clinic -d clinic -c "\dt"
```

## `professionals`

```bash
docker compose exec -T db psql -U clinic -d clinic -c \
  "SELECT id, slug, name, is_senior FROM professionals ORDER BY id;"
```

## `services`

```bash
docker compose exec -T db psql -U clinic -d clinic -c \
  "SELECT id, code, duration_minutes, seniors_only FROM services ORDER BY code;"
```

## `users`

Created on login (name + email) and/or on first successful book.

```bash
docker compose exec -T db psql -U clinic -d clinic -c \
  "SELECT id, name, email, created_at FROM users ORDER BY id;"
```

By email:

```bash
docker compose exec -T db psql -U clinic -d clinic -c \
  "SELECT id, name, email, created_at FROM users WHERE email = 'you@example.com';"
```

## `bookings`

A row with `status` `confirmed` or `pending_doctor` means that slot is taken.

```bash
docker compose exec -T db psql -U clinic -d clinic -c \
  "SELECT id, professional_id, service_id, user_id, patient_name, patient_email, starts_at, ends_at, status FROM bookings ORDER BY id;"
```

Active only:

```bash
docker compose exec -T db psql -U clinic -d clinic -c \
  "SELECT id, starts_at, ends_at, status, patient_email FROM bookings WHERE status IN ('confirmed', 'pending_doctor') ORDER BY starts_at;"
```

By email:

```bash
docker compose exec -T db psql -U clinic -d clinic -c \
  "SELECT id, starts_at, ends_at, status, user_id FROM bookings WHERE patient_email = 'you@example.com';"
```

Join user + professional + service:

```bash
docker compose exec -T db psql -U clinic -d clinic -c \
  "SELECT b.id, p.slug AS professional, s.code AS service, u.email AS user_email, b.starts_at, b.ends_at, b.status
   FROM bookings b
   JOIN professionals p ON p.id = b.professional_id
   JOIN services s ON s.id = b.service_id
   LEFT JOIN users u ON u.id = b.user_id
   ORDER BY b.id;"
```
