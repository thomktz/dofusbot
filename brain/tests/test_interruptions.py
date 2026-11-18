from dofus_brain.world.interruptions import Interruption, InterruptionKind, InterruptionPolicy


def test_verified_invites_default_deny_only_when_the_required_id_exists():
    policy = InterruptionPolicy()
    unknown = policy.observe(Interruption(1, InterruptionKind.GROUP_INVITE))
    assert unknown["default_action"] is None
    policy.closed(1)
    party = policy.observe(Interruption(1, InterruptionKind.GROUP_INVITE, reference_id=7))
    assert party["default_action"] == "party_invitation_refuse"
    policy.closed(1)
    fight = policy.observe(Interruption(1, InterruptionKind.FIGHT_INVITE, reference_id=8))
    assert fight["default_action"] == "friendly_fight_answer"
