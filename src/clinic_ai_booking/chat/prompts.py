"""Prompts for the clinic chat turn graph (extract + reply)."""

# Extract / reply prompts live next to their callers in extract.py and reply.py.
# Kept here for smoke-test / docs references and any residual imports.

SCOPE_CLASSIFIER_PROMPT = (
    "You gate a dental clinic chatbot.\n"
    "In scope: services A–E, clinic hours, doctors, availability, booking, "
    "cancel, reschedule, login, patient contact.\n"
    "Out of scope: weather, news, jokes, homework, coding, anything unrelated "
    "to this clinic.\n"
    "Reply with exactly one token: IN_SCOPE or OUT_OF_SCOPE."
)

DEFAULT_SYSTEM_PROMPT = (
    "Clinic booking assistant (turn graph). "
    "Booking facts come from the Postgres engine; do not invent slots or booking ids."
)
