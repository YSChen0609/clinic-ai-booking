# Booking rules

Patient-facing and agent checklist. Engine enforces these in `domain/booking.py`.

## Hours and slots

- Clinic timezone: Asia/Taipei
- Open Monday–Friday 09:00–20:00; closed weekends
- Breaks (no start): 12:00–13:00 and 17:00–18:00
- Starts on a 15-minute grid
- Normal services (A–D) must end by 20:00 and cannot overlap a break
- Service E may cross breaks / run past 20:00, must end by 22:00 same day → status `pending_doctor` when overtime or break-crossing
- Junior may book A–B only; seniors may book A–E

## Your appointments (per patient email)

- Call `list_patient_appointments` when the patient is identified; if they already have bookings, remind them before offering a new slot
- Do not rebook the same time slot they already hold
- Same service again → reschedule that booking (do not create a second of the same service)
- At most **two** active appointments total
- Those two must be **different services** at **different times** (no overlapping times)
- A third booking is not allowed until one is cancelled or rescheduled
- Cancelled bookings do not count

## Who can change a booking

- Cancel and reschedule require login (same name + email as the booking)
