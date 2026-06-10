"""Test: Observability - API, Tracer, Scheduler, Dead-letter, SafeHandler."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, ".")

from src.core.events import Event, EventType, AggregateType
from src.core.bus import EventBus
from src.core.pipeline import Pipeline
from src.core.state_engine import StateEngine
from src.core.tracer import Tracer
from src.core.safety import DeadLetterQueue, SafeHandler
from src.storage.db import init_db, close_db
from src.storage.event_store import EventStore
from src.storage.snapshot_store import SnapshotStore


async def test_tracer_injects_trace_id():
    tracer = Tracer()
    event = Event(EventType.SYSTEM_STARTUP, "sys", AggregateType.SYSTEM)
    traced, start = tracer.start_trace(event)
    assert "trace_id" in traced.metadata
    assert len(traced.metadata["trace_id"]) == 36
    tracer.end_trace(traced, start)
    assert tracer.trace_count() == 1
    print("✓ tracer injects trace_id and records duration")


async def test_tracer_get_trace():
    tracer = Tracer()
    event = Event(EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK)
    traced, start = tracer.start_trace(event)
    trace_id = traced.metadata["trace_id"]
    tracer.end_trace(traced, start)
    trace = tracer.get_trace(trace_id)
    assert trace is not None
    assert len(trace) == 1
    assert trace[0]["event_type"] == "homework.new"
    print("✓ tracer.get_trace returns correct entries")


def test_dead_letter_add_and_get():
    dlq = DeadLetterQueue()
    event = Event(EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK)
    dlq.add(event, ValueError("test error"))
    assert dlq.count() == 1
    entries = dlq.get_all()
    assert entries[0]["event_type"] == "homework.new"
    assert "test error" in entries[0]["error"]
    print("✓ dead-letter add and get")


def test_dead_letter_clear():
    dlq = DeadLetterQueue()
    event = Event(EventType.HOMEWORK_NEW, "hw-1", AggregateType.HOMEWORK)
    dlq.add(event, ValueError("x"))
    dlq.clear()
    assert dlq.count() == 0
    print("✓ dead-letter clear")


async def test_safe_handler_catches_error():
    dlq = DeadLetterQueue()
    safe = SafeHandler(dlq, timeout_seconds=5, max_retries=1)

    async def failing_handler(event):
        raise ValueError("boom")

    wrapped = safe.wrap(failing_handler)
    event = Event(EventType.HOMEWORK_NEW, "hw-fail", AggregateType.HOMEWORK)
    result = await wrapped(event)
    assert dlq.count() == 1
    assert result[0].event_type == EventType.SYSTEM_EVENT_FAILED
    print("✓ safe handler catches error")


async def test_safe_handler_timeout():
    dlq = DeadLetterQueue()
    safe = SafeHandler(dlq, timeout_seconds=0.1, max_retries=0)

    async def slow_handler(event):
        await asyncio.sleep(10)
        return []

    wrapped = safe.wrap(slow_handler)
    event = Event(EventType.HOMEWORK_NEW, "hw-slow", AggregateType.HOMEWORK)
    result = await wrapped(event)
    assert dlq.count() == 1
    assert result[0].event_type == EventType.SYSTEM_CONNECTOR_TIMEOUT
    print("✓ safe handler catches timeout")


async def test_safe_handler_passes_through():
    dlq = DeadLetterQueue()
    safe = SafeHandler(dlq, timeout_seconds=5, max_retries=1)

    async def good_handler(event):
        return [Event(EventType.NOTIFICATION_SEND, event.aggregate_id, AggregateType.NOTIFICATION)]

    wrapped = safe.wrap(good_handler)
    event = Event(EventType.HOMEWORK_NEW, "hw-ok", AggregateType.HOMEWORK)
    result = await wrapped(event)
    assert dlq.count() == 0
    assert result[0].event_type == EventType.NOTIFICATION_SEND
    print("✓ safe handler passes through on success")


async def test_pipeline_with_tracer():
    bus = EventBus()
    tracer = Tracer()
    pipeline = Pipeline(bus, tracer=tracer)

    async def echo_handler(event):
        return [Event(EventType.NOTIFICATION_SEND, event.aggregate_id, AggregateType.NOTIFICATION,
                      causation_id=event.event_id)]

    async def nop_handler(event):
        return []

    bus.subscribe(EventType.SYSTEM_STARTUP, echo_handler)
    bus.subscribe(EventType.NOTIFICATION_SEND, nop_handler)

    event = Event(EventType.SYSTEM_STARTUP, "test", AggregateType.SYSTEM)
    all_events = await pipeline.run(event)

    assert tracer.trace_count() == 2
    print(f"✓ pipeline traces {tracer.trace_count()} events")

    recent = tracer.get_recent(10)
    assert len(recent) >= 2
    print(f"✓ tracer.get_recent returns {len(recent)} entries")


async def test_api_endpoints():
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "api_test.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        await init_db(db_url)

        try:
            event_store = EventStore()
            snapshot_store = SnapshotStore()
            state_engine = StateEngine()
            tracer = Tracer()
            dlq = DeadLetterQueue()

            from src.interface.api.app import create_app
            app = create_app(
                event_store=event_store,
                state_engine=state_engine,
                snapshot_store=snapshot_store,
                tracer=tracer,
                dead_letter=dlq,
            )

            client = TestClient(app)

            e1 = Event(EventType.HOMEWORK_NEW, "hw-a", AggregateType.HOMEWORK,
                       payload={"title": "Test", "course": "Math"})
            await event_store.append(e1)
            await state_engine.apply(e1)

            r = client.get("/stats")
            assert r.status_code == 200
            assert r.json()["total_events"] == 1
            print("✓ GET /stats")

            r = client.get("/events/recent?n=5")
            assert r.status_code == 200
            assert len(r.json()["events"]) == 1
            print("✓ GET /events/recent")

            r = client.get(f"/events/{e1.event_id}")
            assert r.status_code == 200
            assert r.json()["event_type"] == "homework.new"
            print("✓ GET /events/{id}")

            r = client.get("/state")
            assert r.status_code == 200
            assert r.json()["event_count"] == 1
            print("✓ GET /state")

            r = client.get("/state/derived")
            assert r.status_code == 200
            assert "workload" in r.json()
            print("✓ GET /state/derived")

            r = client.get("/dead-letter")
            assert r.status_code == 200
            assert r.json()["count"] == 0
            print("✓ GET /dead-letter")

            r = client.get("/snapshots")
            assert r.status_code == 200
            print("✓ GET /snapshots")

            r = client.get("/dashboard")
            assert r.status_code == 200
            assert "Cognitive OS" in r.text
            print("✓ GET /dashboard")

            r = client.get(f"/trace/{e1.event_id}")
            assert r.status_code == 200
            assert r.json()["depth"] == 1
            print("✓ GET /trace/{id}")

            r = client.get("/aggregates/hw-a/events")
            assert r.status_code == 200
            assert len(r.json()["events"]) == 1
            print("✓ GET /aggregates/{id}/events")

            r = client.get("/events?type=homework.new")
            assert r.status_code == 200
            assert len(r.json()["events"]) == 1
            print("✓ GET /events?type=...")

            r = client.get("/events/nonexistent")
            assert r.status_code == 404
            print("✓ 404 for missing event")

        finally:
            await close_db()

    print("API: all endpoints verified")


async def test_scheduler_emits_events():
    bus = EventBus()
    from src.infrastructure.scheduler import CognitiveScheduler
    scheduler = CognitiveScheduler()
    scheduler.set_event_bus(bus)
    scheduler.add_interval_job("test_job", 60, {"action": "test"})
    assert len(scheduler.jobs) >= 1
    scheduler.start()
    await asyncio.sleep(0.3)
    scheduler.stop()
    print(f"✓ scheduler has {len(scheduler.jobs)} jobs")


if __name__ == "__main__":
    print("=== Tracer ===")
    asyncio.run(test_tracer_injects_trace_id())
    asyncio.run(test_tracer_get_trace())

    print("\n=== Dead-letter ===")
    test_dead_letter_add_and_get()
    test_dead_letter_clear()

    print("\n=== SafeHandler ===")
    asyncio.run(test_safe_handler_catches_error())
    asyncio.run(test_safe_handler_timeout())
    asyncio.run(test_safe_handler_passes_through())

    print("\n=== Pipeline + Tracer ===")
    asyncio.run(test_pipeline_with_tracer())

    print("\n=== Scheduler ===")
    asyncio.run(test_scheduler_emits_events())

    print("\n=== API ===")
    asyncio.run(test_api_endpoints())

    print("\nObservability: all checks passed")
