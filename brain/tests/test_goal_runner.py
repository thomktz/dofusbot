from dofus_brain.world.goal_runner import AgentGoalRun


def test_goal_run_keeps_a_bounded_audit_trail():
    run = AgentGoalRun(7, "farm wheat")
    for index in range(45):
        run.record({"action": "use_interactive", "args": {"element_id": index}}, f"node {index}")
    audit = run.payload()
    assert audit["steps"] == 45
    assert len(audit["history"]) == 40
    assert audit["history"][-1]["narration"] == "node 44"


def test_goal_run_records_safe_pause_reason():
    run = AgentGoalRun(7, "quest")
    run.set_state("paused", error="waiting for live confirmation")
    assert run.payload()["error"] == "waiting for live confirmation"
