# Additional API endpoints for safety and scheduler

SAFETY_SCHEDULER_ROUTES = r"""
    # ── Safety / Dead-letter ─────────────────────────────────────

    @app.get("/dead-letter")
    async def get_dead_letter():
        dlq = getattr(app.state, 'dead_letter', None)
        if dlq is None:
            raise HTTPException(status_code=503, detail="Dead-letter queue not available")
        return {"count": dlq.count(), "entries": dlq.get_all()}

    @app.delete("/dead-letter")
    async def clear_dead_letter():
        dlq = getattr(app.state, 'dead_letter', None)
        if dlq is None:
            raise HTTPException(status_code=503, detail="Dead-letter queue not available")
        dlq.clear()
        return {"status": "cleared"}

    # ── Scheduler ────────────────────────────────────────────────

    @app.get("/scheduler/jobs")
    async def get_scheduler_jobs():
        sched = getattr(app.state, 'scheduler', None)
        if sched is None:
            raise HTTPException(status_code=503, detail="Scheduler not available")
        return {"jobs": sched.jobs}
"""
