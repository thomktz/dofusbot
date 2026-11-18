from dofus_brain.game.grid import Grid
from dofus_brain.server import dialog_reply_click_point, map_exit_click_point


def test_exit_click_is_one_step_outside_the_selected_bottom_edge():
    grid = Grid()
    cell, x, y = map_exit_click_point(grid, "down", 280, gaussian=lambda _mu, _std: 0)
    assert cell in grid.edge_cells("bottom")
    cx, cy = grid.center(cell)
    assert (x, y) == (cx, cy + grid.ch / 2)


def test_exit_prefers_open_nearby_edge_and_clips_jitter_to_bounds():
    grid = Grid()
    border = grid.edge_cells("right")
    # The closest right-side exit to cell 0 is unavailable; use the next one.
    cells = {str(border[0]): {"movable": False}, str(border[1]): {"movable": True}}
    cell, x, y = map_exit_click_point(grid, "right", 0, cells, gaussian=lambda _mu, _std: -100)
    assert cell == border[1]
    cx, cy = grid.center(cell)
    assert (x, y) == (cx + grid.cw, cy)


def test_dialogue_reply_rows_are_centred_and_distinct():
    first = dialog_reply_click_point(1200, 900, 0, 3)
    middle = dialog_reply_click_point(1200, 900, 1, 3)
    last = dialog_reply_click_point(1200, 900, 2, 3)
    assert first[0] == middle[0] == last[0] == 600
    assert first[1] < middle[1] < last[1]
