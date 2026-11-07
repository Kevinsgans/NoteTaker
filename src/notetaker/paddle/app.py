"""Mouse and Hailo front ends for the same single-player paddle game."""

import argparse
import logging
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from .game import HEIGHT, PADDLE_W, PADDLE_Y, RADIUS, TOP, WIDTH, Game, Wrist

log = logging.getLogger("wrist_paddle")


class PoseInput:
    def __init__(self, interpreter, hef):
        self.wrist = Wrist()
        self.channel, sender = socket.socketpair(type=socket.SOCK_DGRAM)
        self.channel.setblocking(False)
        args = [
            interpreter,
            str(Path(__file__).with_name("pose_bridge.py")),
            str(sender.fileno()),
            "--arch",
            "hailo10h",
            "--input",
            "rpi",
        ]
        if hef:
            args.extend(["--hef-path", hef])
        try:
            self.worker = subprocess.Popen(
                args, pass_fds=(sender.fileno(),), start_new_session=True
            )
        except BaseException:
            self.channel.close()
            raise
        finally:
            sender.close()
        self.frames = 0

    def poll(self):
        if self.worker.poll() is not None:
            raise RuntimeError(
                "Pose pipeline stopped. Check the Hailo interpreter, model and camera; see README."
            )
        while True:
            try:
                packet = self.channel.recv(16)
            except BlockingIOError:
                break
            stamp, x = struct.unpack("!dd", packet)
            self.frames += 1
            self.wrist.observe(x, stamp)

    def close(self):
        # The Pi camera pipeline can spawn helpers; stop the entire isolated group.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.worker.pid, sig)
            except ProcessLookupError:
                break
            try:
                self.worker.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                continue
        self.channel.close()


def draw(pg, screen, fonts, game, tracked, mode, fps):
    bg, ink, mint, peach = "#101e28", "#e4eff0", "#76dbc0", "#ffae75"
    screen.fill(bg)

    def label(value, font, color, x, y, center=False):
        glyph = fonts[font].render(value, True, color)
        rect = glyph.get_rect()
        if center:
            rect.center = (x, y)
        else:
            rect.topleft = (x, y)
        screen.blit(glyph, rect)

    label("WRIST  /  PADDLE", 1, mint, 22, 20)
    label(f"RALLY  {game.score:02d}      BEST  {game.best:02d}", 1, ink, 520, 20)
    pg.draw.line(screen, "#324853", (0, TOP), (WIDTH, TOP))
    for x in range(20, WIDTH, 32):
        for y in range(TOP + 16, HEIGHT - 28, 32):
            pg.draw.circle(screen, "#243742", (x, y), 1)
    pg.draw.rect(
        screen, mint, (round(game.paddle - PADDLE_W / 2), PADDLE_Y, PADDLE_W, 10), border_radius=5
    )
    if tracked and game.state == "playing":
        pg.draw.circle(screen, peach, (round(game.x), round(game.y)), RADIUS)
    else:
        title, hint = {
            "ready": ("Ready to rally?", "Press SPACE to serve"),
            "over": ("Round over", "Press SPACE for another round"),
            "playing": ("Tracking paused", "Raise your right hand to resume"),
        }[game.state]
        label(title, 2, ink, WIDTH / 2, HEIGHT / 2 - 12, True)
        label(hint, 1, peach, WIDTH / 2, HEIGHT / 2 + 26, True)
    label(f"{mode.upper()}   /   {fps} FPS", 0, mint, 20, HEIGHT - 22)
    label("ESC  /  QUIT", 0, ink, WIDTH - 105, HEIGHT - 22)


def main():
    parser = argparse.ArgumentParser(
        description="Play Wrist Paddle with a mouse or a Hailo pose camera."
    )
    parser.add_argument("--input", choices=["mouse", "pose"], default="mouse")
    parser.add_argument(
        "--pose-python", default=sys.executable, help="Python in the Hailo apps environment"
    )
    parser.add_argument("--hef-path", help="Optional Hailo-10H pose model")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="[wrist_paddle] %(message)s")
    try:
        import pygame as pg
    except ImportError:
        parser.exit(1, "Install the game extra: uv sync --extra game\n")
    with ExitStack() as stack:
        pg.init()
        stack.callback(pg.quit)
        screen = pg.display.set_mode((WIDTH, HEIGHT), pg.SCALED | pg.RESIZABLE)
        pg.display.set_caption("Wrist Paddle")
        fonts = [pg.font.Font(None, size) for size in (18, 27, 60)]
        clock, game = pg.time.Clock(), Game()
        pose = None
        if options.input == "pose":
            try:
                pose = PoseInput(options.pose_python, options.hef_path)
            except (ValueError, OSError) as exc:
                parser.exit(1, f"[wrist_paddle] Cannot launch pose process: {exc}\n")
            stack.callback(pose.close)
        log.info("input=%s; Space serves, Esc quits", options.input)
        report_time, last_frames, fps = time.monotonic(), 0, 0
        while True:
            dt = clock.tick(60) / 1000
            for event in pg.event.get():
                if event.type == pg.QUIT or (event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE):
                    return
                if event.type == pg.KEYDOWN and event.key == pg.K_SPACE:
                    game.start()
            if pose:
                try:
                    pose.poll()
                except RuntimeError as exc:
                    parser.exit(1, f"[wrist_paddle] {exc}\n")
                x, tracked = pose.wrist.x, pose.wrist.visible(time.monotonic())
            else:
                x, tracked = pg.mouse.get_pos()[0] / (WIDTH - 1), True
            previous = game.state, game.score
            game.update(dt, x, tracked)
            if previous != (game.state, game.score):
                log.info("round=%s score=%d", game.state, game.score)
            now = time.monotonic()
            if now - report_time >= 1:
                fps = (
                    round((pose.frames - last_frames) / (now - report_time))
                    if pose
                    else round(clock.get_fps())
                )
                if pose:
                    last_frames = pose.frames
                    log.info("pose_fps=%d tracked=%s", fps, tracked)
                report_time = now
            draw(pg, screen, fonts, game, tracked, options.input, fps)
            pg.display.flip()
