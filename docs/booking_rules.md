# Booking rules

Patient-facing and agent checklist. Engine enforces these in `domain/booking.py`.

## Hours and slots

- Clinic timezone: Asia/Taipei
- Open Monday–Friday 09:00–20:00; closed weekends
- Breaks (no start): 12:00–13:00 and 17:00–18:00
- Starts on a 15-minute grid; **past starts (clinic now) are not offered or bookable**
- Chat offers **start windows** (inclusive ranges of legal 15-minute **start** times for the service duration — not free-until times and not appointment end). Replies label them as start times and include the service duration.
- Patient may name a rough time; the engine **snaps** to the nearest free grid start and asks to confirm when it rounded
- Doctor list copy uses name + junior/senior only (no internal slug)
- Normal services (A–D) must end by 20:00 and cannot overlap a break
- Service E may cross breaks / run past 20:00, must end by 22:00 same day → status `pending_doctor` when overtime or break-crossing
- Junior may book A–B only; seniors may book A–E
- Junior + seniors-only service → patient message lists seniors who can do it **and** offers a different service
- Multiple services in one message (e.g. “A and B”) → ask which to book first; one booking at a time (cart/queue is later)
- Unknown service letter (e.g. F) stays in scope: reject with the A–E list; do not invent another code or treat as out-of-scope

## Days

- When the patient has not named a day: offer **today’s remaining** start windows first, then ask if they want tomorrow / another day; if today is empty, offer the next open day the same way
- A bare clock after that offer (or with no day) soft-locks the offered / clinic day when that start is free
- “Same day” / “same Friday” / “that day” after a book reuses `last_day` (not an LLM guess)
- “Next Friday” (and other `next <weekday>`) is ambiguous: bot asks **this upcoming** vs **following week** with concrete dates (reply 1/2 or the date)
- “Him” / “with him” reuses `last_professional_slug` unless the user names another doctor

## Doctor choice

- When asking for a doctor after a service is set, list only professionals who can perform that service (C–E: seniors only — no junior)
- Unique clear match (e.g. “Dr. Chen”) is **locked immediately** — ask for the next missing field (service, day, …), do not ask “reply yes”
- Ambiguous phrases (e.g. “senior”) list candidates — patient must pick / confirm before times are offered

## Checkout

- Before writing to Postgres, chat asks for a final **yes** (`need_confirm_book`). Changing service/doctor/day/time clears that confirmation.

## Your appointments (per patient email)

- Call `list_patient_appointments` when the patient is identified; if they already have bookings, remind them before offering a new slot
- Do not rebook the same time slot they already hold
- Same service again → reschedule that booking (do not create a second of the same service)
- At most **two upcoming** appointments: status confirmed/pending_doctor **and** `ends_at` still after clinic now
- Finished appointments (end time passed) do **not** count toward the limit or same-service block
- Those two must be **different services** at **different times** (no overlapping times)
- A third upcoming booking is not allowed until one is cancelled, rescheduled, or finishes
- Cancelled bookings do not count

## Who can change a booking

- Cancel and reschedule require login (same name + email as the booking)
- Chat does not run cancel/reschedule yet; logged-in users are told to use the website (not “after logging in”)
