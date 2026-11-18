from dofus_brain.game.grid import Grid
from dofus_brain.world.roleplay_path import find_roleplay_path, key_cell


def test_roleplay_path_uses_astra_orientation_key_encoding() -> None:
    grid = Grid()
    # cells 0 -> 1 have Astra coordinates (0, 0) -> (1, 1): RIGHT = 0.
    path = find_roleplay_path(0, 1, grid=grid)
    assert path.cells == (0, 1)
    assert path.key_cells == (key_cell(0, 0), key_cell(1, 0))


def test_roleplay_path_compresses_only_at_orientation_changes() -> None:
    grid = Grid()
    # The two successive (u + 1, v + 1) cells follow RIGHT (orientation 0).
    middle, end = grid.from_uv(1, 1), grid.from_uv(2, 2)
    assert middle is not None and end is not None
    path = find_roleplay_path(0, end, grid=grid)
    assert path.cells == (0, middle, end)
    assert path.key_cells == (key_cell(0, 0), key_cell(end, 0))


def test_roleplay_path_detours_around_blocked_cells() -> None:
    path = find_roleplay_path(0, 30, blocked=[15])
    assert path is not None
    assert 15 not in path.cells
