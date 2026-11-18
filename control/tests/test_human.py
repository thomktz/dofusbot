import math

from dofus_control.human import HumanProfile, cubic_bezier, ease_in_out, path_duration_ms, plan_move


def test_ease():
    assert ease_in_out(0) == 0 and abs(ease_in_out(1) - 1) < 1e-12
    assert abs(ease_in_out(0.5) - 0.5) < 1e-12
    assert ease_in_out(0.1) < 0.1 and ease_in_out(0.9) > 0.9   # slow start / slow end


def test_bezier_endpoints():
    p = cubic_bezier((0, 0), (1, 5), (4, 5), (5, 0), 0)
    q = cubic_bezier((0, 0), (1, 5), (4, 5), (5, 0), 1)
    assert p == (0, 0) and q == (5, 0)


def test_durations_in_range_and_monotone_with_distance():
    p = HumanProfile(seed=1, move_ms_variation=0)
    ds = [p.move_duration(d) for d in (0, 50, 200, 600, 2000)]
    assert all(120 <= d <= 450 for d in ds)
    assert ds == sorted(ds)
    p2 = HumanProfile(seed=2)
    for d in range(0, 3000, 37):
        assert 120 <= p2.move_duration(d) <= 450


def test_plan_move_ends_exactly_and_is_curved():
    p = HumanProfile(seed=3, overshoot_prob=0)
    path = plan_move((100, 100), (900, 500), p)
    assert path[-1][:2] == (900, 500)
    assert 120 <= path_duration_ms(path) <= 450 + 1e-6
    # curved: some point is off the straight line
    def off(pt):
        x, y = pt[0] - 100, pt[1] - 100
        return abs(x * 400 - y * 800) / math.hypot(800, 400)
    assert max(off(pt) for pt in path) > 5
    # all steps reasonably small
    prev = (100, 100)
    for x, y, _ in path:
        assert math.hypot(x - prev[0], y - prev[1]) < 40
        prev = (x, y)


def test_overshoot():
    p = HumanProfile(seed=4, overshoot_prob=1.0)
    path = plan_move((0, 500), (1000, 500), p)
    assert max(pt[0] for pt in path) > 1000   # went past the target
    assert path[-1][:2] == (1000, 500)


def test_seeded_reproducible():
    a = plan_move((0, 0), (500, 300), HumanProfile(seed=42))
    b = plan_move((0, 0), (500, 300), HumanProfile(seed=42))
    c = plan_move((0, 0), (500, 300), HumanProfile(seed=43))
    assert a == b and a != c


def test_jitter_and_samplers():
    p = HumanProfile(seed=5)
    for _ in range(200):
        x, y = p.jitter(100, 100)
        assert abs(x - 100) <= 2 and abs(y - 100) <= 2
        assert 40 <= p.hold() <= 110
        assert 60 <= p.key_gap() <= 180
        assert 150 <= p.post_pause() <= 600
    assert p.jitter(100, 100, exact=True) == (100, 100)
    fast = HumanProfile(seed=1, speed=0)
    assert fast.hold() == 0 and path_duration_ms(plan_move((0, 0), (300, 300), fast)) == 0
