import pytest

from app.audit import record_event
from app.models import AuditEvent


@pytest.mark.parametrize("event_type", ["call.input_received", "call.response_sent"])
def test_voice_call_events_are_recorded(session, event_type):
    record_event(session, None, event_type, {"session_id": "ATVId_1"})
    session.commit()

    (event,) = session.query(AuditEvent).all()
    assert (event.transaction_id, event.event_type, event.payload) == (
        None,
        event_type,
        {"session_id": "ATVId_1"},
    )


@pytest.mark.parametrize("event_type", ["stream.started", "stream.stopped", "time.nudge"])
def test_retired_realtime_events_are_rejected(session, event_type):
    with pytest.raises(ValueError, match=f"^Unknown audit event type: {event_type}$"):
        record_event(session, None, event_type)
    assert list(session.new) == []
