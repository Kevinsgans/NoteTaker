import math
from types import SimpleNamespace

import pytest

from notetaker.paddle.game import PADDLE_W, PADDLE_Y, RADIUS, WIDTH, Game, Wrist
from notetaker.paddle.pose_bridge import wrist_x


def test_one_point_per_return_and_round_restart():
    game = Game()
    game.start()
    game.y = PADDLE_Y - RADIUS - 1
    game.update(0.05, 0.5)
    assert game.score == game.best == 1 and game.vy < 0
    game.update(0.05, 0.5)
    assert game.score == 1
    game.x, game.y, game.vy = 20, PADDLE_Y, 300
    for _ in range(5):
        game.update(0.1, 1)
    assert game.state == "over" and game.paddle == WIDTH - PADDLE_W / 2
    game.start()
    assert (game.state, game.score, game.best) == ("playing", 0, 1)


def test_elapsed_time_and_pause():
    games = [Game(), Game()]
    for game, hz in zip(games, (30, 60)):
        game.start()
        for _ in range(hz):
            game.update(1 / hz, 0.5)
    assert games[0].x == pytest.approx(games[1].x)
    assert games[0].y == pytest.approx(games[1].y)
    game = games[0]
    before = (game.x, game.y, game.score)
    for _ in range(100):
        game.update(1 / 60, 0.5, tracked=False)
    assert (game.x, game.y, game.score) == before


def test_smoothing_mirroring_timeout_and_reacquisition():
    wrist = Wrist()
    wrist.observe(0.2, 1)
    assert wrist.x == pytest.approx(0.8)
    wrist.observe(0.6, 1.03)
    assert 0.4 < wrist.x < 0.8
    assert wrist.visible(1.5)
    assert not wrist.visible(1.54)
    wrist.observe(math.nan, 1.55)
    wrist.observe(-1, 1.56)
    assert not wrist.visible(1.57)
    wrist.observe(1, 2)
    assert wrist.x == 0 and wrist.visible(2)
    wrist.observe(0, 1.8)
    assert wrist.x == 0


def test_hailo_wrist_transformed_from_person_box():
    point = SimpleNamespace(x=lambda: 0.75, confidence=lambda: 0.8)
    landmarks = SimpleNamespace(get_points=lambda: [None] * 10 + [point])
    detection = SimpleNamespace(
        get_label=lambda: "person",
        get_confidence=lambda: 0.9,
        get_objects_typed=lambda _: [landmarks],
        get_bbox=lambda: SimpleNamespace(xmin=lambda: 0.2, width=lambda: 0.4),
    )
    hailo = SimpleNamespace(HAILO_LANDMARKS=1)
    assert wrist_x([detection], hailo) == pytest.approx(0.5)
    point.confidence = lambda: 0.1
    assert wrist_x([detection], hailo) is None
