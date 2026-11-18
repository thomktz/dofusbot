from dofus_brain.world.session_plan import SessionPlan, SessionPlanStore
import time


def test_session_plan_persists_account_scope_jobs_and_checkpoint(tmp_path):
    store = SessionPlanStore(tmp_path)
    plan = SessionPlan(44, 240, [{"kind": "quest", "id": 1}], checkpoint={"map_id": 12})
    store.save(plan)
    loaded = store.load(44)
    assert loaded and loaded.account_id == 44 and loaded.jobs[0]["kind"] == "quest"
    assert loaded.status()["remaining_seconds"] <= 240 * 60


def test_session_plan_reports_expiry_without_dispatching_more_work():
    plan = SessionPlan(1, 1, started_at=int(time.time()) - 61)
    assert plan.status()["expired"] is True
