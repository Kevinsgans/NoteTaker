"""Game rules in logical pixels; independent of Pygame and camera hardware."""

import math
from dataclasses import dataclass

WIDTH, HEIGHT = 800, 500
TOP, PADDLE_Y, PADDLE_W, RADIUS = 65, 452, 112, 8
LOST_AFTER = 0.5


@dataclass
class Wrist:
    x: float = 0.5
    last_seen: float = -math.inf

    def observe(self, camera_x: float, stamp: float):
        if not 0 <= camera_x <= 1 or not math.isfinite(stamp) or stamp <= self.last_seen:
            return
        dt = stamp - self.last_seen
        target = 1 - camera_x
        weight = 1 if dt >= LOST_AFTER else -math.expm1(-dt / 0.075)
        self.x += weight * (target - self.x)
        self.last_seen = stamp

    def visible(self, now):
        return 0 <= now - self.last_seen < LOST_AFTER


@dataclass
class Game:
    state: str = "ready"
    score: int = 0
    best: int = 0
    paddle: float = WIDTH / 2
    x: float = WIDTH / 2
    y: float = HEIGHT / 2
    vx: float = 135
    vy: float = 210

    def start(self):
        if self.state != "playing":
            self.score = 0
            self.x, self.y = WIDTH / 2, HEIGHT / 2
            self.vx, self.vy = 135, 210
            self.state = "playing"

    def update(self, dt, position, tracked=True):
        half = PADDLE_W / 2
        self.paddle = half + min(1, max(0, position)) * (WIDTH - PADDLE_W)
        if self.state != "playing" or not tracked:
            return
        # Small steps preserve collision order. Discard long window/suspend stalls.
        remaining = min(dt, 0.1)
        while remaining > 0 and self.state == "playing":
            step = min(remaining, 1 / 240)
            remaining -= step
            previous_y = self.y
            self.x += self.vx * step
            self.y += self.vy * step
            if self.x <= RADIUS or self.x >= WIDTH - RADIUS:
                self.x = max(RADIUS, min(WIDTH - RADIUS, self.x))
                self.vx = abs(self.vx) if self.x == RADIUS else -abs(self.vx)
            if self.y < TOP + RADIUS:
                self.y, self.vy = TOP + RADIUS, abs(self.vy)
            if (
                self.vy > 0
                and previous_y + RADIUS <= PADDLE_Y <= self.y + RADIUS
                and abs(self.x - self.paddle) <= half
            ):
                offset = (self.x - self.paddle) / half
                speed = min(540, math.hypot(self.vx, self.vy) + 12)
                self.vx, self.vy = speed * math.sin(offset), -speed * math.cos(offset)
                self.y = PADDLE_Y - RADIUS
                self.score += 1
                self.best = max(self.best, self.score)
            if self.y - RADIUS > HEIGHT:
                self.state = "over"
