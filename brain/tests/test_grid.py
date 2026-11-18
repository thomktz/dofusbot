import pytest

from dofus_brain.game.grid import Grid, direction_between, map_distance


@pytest.fixture
def g():
    return Grid(ox=180, oy=40, cw=68, ch=34)


def test_formula_matches_protocol(g):
    # protocol §1: x = ox + col*cw + (row%2)*cw/2, y = oy + row*ch/2
    assert g.center(0) == (180, 40)
    assert g.center(14) == (180 + 34, 40 + 17)   # row 1 col 0 shifted half a cell
    assert g.center(559) == (180 + 13 * 68 + 34, 40 + 39 * 17)
    assert g.rc(559) == (39, 13) and g.cell_id(39, 13) == 559


def test_round_trips(g):
    for c in range(560):
        r, col = g.rc(c)
        assert g.cell_id(r, col) == c
        assert g.from_uv(*g.uv(c)) == c
        x, y = g.center(c)
        assert g.cell_at(x, y) == c
        assert g.cell_at(x + g.cw * 0.2, y + g.ch * 0.2) == c  # inside the diamond


def test_off_grid(g):
    assert g.cell_at(-500, -500) is None
    with pytest.raises(ValueError):
        g.rc(560)


def test_neighbours_and_distance(g):
    # odd row 1 col 1 (=15) touches rows 0 and 2, cols 1 and 2
    assert sorted(g.neighbours(15)) == [1, 2, 29, 30]
    assert sorted(g.neighbours(0)) == [14]        # corner cell
    assert g.distance(0, 28) == 2                 # two half-rows straight down
    assert g.distance(0, 1) == 2                  # next cell in the same row = 2 iso steps
    for c in (0, 100, 300):
        for n in g.neighbours(c):
            assert g.distance(c, n) == 1


def test_in_line(g):
    a = g.cell_id(10, 5)
    u, v = g.uv(a)
    assert g.in_line(a, g.from_uv(u + 3, v))
    assert not g.in_line(a, g.from_uv(u + 2, v + 1))


def test_los_straight_line(g):
    a = g.cell_id(10, 5)
    u, v = g.uv(a)
    b = g.from_uv(u + 4, v)
    mid = g.from_uv(u + 2, v)
    assert g.has_los(a, b, [])
    assert not g.has_los(a, b, [mid])
    assert g.has_los(a, b, [b])                  # the target cell itself does not block
    assert set(g.line_cells(a, b)) == {g.from_uv(u + i, v) for i in (1, 2, 3, 4)}


def test_los_diagonal_corner_rule(g):
    a = g.cell_id(10, 5)
    u, v = g.uv(a)
    b = g.from_uv(u + 2, v + 2)
    s1, s2 = g.from_uv(u + 1, v), g.from_uv(u, v + 1)
    assert g.has_los(a, b, [s1])                 # one side of the corner open -> visible
    assert not g.has_los(a, b, [s1, s2])         # both sides blocked -> no LOS
    assert not g.has_los(a, b, [g.from_uv(u + 1, v + 1)])


def test_los_symmetric_random(g):
    import random

    rnd = random.Random(1)
    blockers = set(rnd.sample(range(560), 80))
    for _ in range(200):
        a, b = rnd.sample(range(560), 2)
        assert g.has_los(a, b, blockers) == g.has_los(b, a, blockers)


def test_bfs_reachable(g):
    a = g.cell_id(10, 5)
    u, v = g.uv(a)
    r = g.reachable(a, 2)
    assert r[a] == 0 and len(r) == 1 + 4 + 8      # diamond of radius 2
    # wall all four neighbours but one
    walls = [g.from_uv(u + 1, v), g.from_uv(u - 1, v), g.from_uv(u, v + 1)]
    r = g.reachable(a, 3, blocked=walls)
    assert all(w not in r for w in walls)
    assert g.from_uv(u, v - 1) in r
    assert r[g.from_uv(u + 1, v - 1)] == 2        # around the wall
    occ = g.from_uv(u, v - 1)
    r = g.reachable(a, 3, blocked=walls, occupied=[occ])
    assert set(r) == {a}                           # fully boxed in


def test_path(g):
    a = g.cell_id(10, 5)
    u, v = g.uv(a)
    b = g.from_uv(u + 3, v)
    p = g.path(a, b, blocked=[g.from_uv(u + 1, v)])
    assert p[0] == a and p[-1] == b and len(p) - 1 == 5


def test_edges_and_map_distance(g):
    assert all(g.rc(c)[1] == 13 for c in g.edge_cells("right"))
    assert all(g.rc(c)[0] == 0 for c in g.edge_cells("top"))
    assert map_distance((4, -18), (6, -15)) == 5
    assert direction_between((4, -18), (4, -19)) == "top"
    assert direction_between((4, -18), (5, -18)) == "right"
