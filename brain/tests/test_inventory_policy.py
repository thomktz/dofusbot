from dofus_brain.world.inventory_policy import InventoryPolicy


def test_pod_policy_has_safe_thresholds_and_never_claims_bank_transfer():
    policy = InventoryPolicy()
    assert policy.decide({"weight": 70, "weight_max": 100}).state == "caution"
    assert policy.decide({"weight": 85, "weight_max": 100}).state == "bank_after_safe_action"
    assert policy.decide({"weight": 96, "weight_max": 100}).state == "bank_required"
    assert policy.decide({}).state == "unknown"
