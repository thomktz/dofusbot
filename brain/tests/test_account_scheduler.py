from dofus_brain.world.account_scheduler import AccountScheduler


def test_scheduler_round_robins_and_skips_blocked_accounts():
    s = AccountScheduler(); s.request(1); s.request(2); s.request(1)
    assert [s.next(), s.next()] == [1, 2]
    s.block(1); assert s.next() == 2
    s.unblock(1); assert s.next() == 1
