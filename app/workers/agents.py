"""Celery tasks for agent workloads."""
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.agents.run_agent_task", bind=True, max_retries=2)
def run_agent_task(self, agent_id: str, task: dict, user_id: str | None = None):
    """Execute an agent task asynchronously on the agents queue."""
    import asyncio

    from app.database.postgres import async_session_factory
    from app.agents.orchestrator import AgentOrchestrator

    async def _run():
        async with async_session_factory() as db:
            orchestrator = AgentOrchestrator(db)
            return await orchestrator.dispatch(agent_id, task, user_id=user_id)

    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)
