"""Run in Hailo's interpreter; send only wrist coordinates to the game."""

import math
import socket
import struct
import sys
import time


def wrist_x(detections, hailo):
    for person in sorted(detections, key=lambda d: d.get_confidence(), reverse=True):
        if person.get_label() != "person":
            continue
        landmarks = person.get_objects_typed(hailo.HAILO_LANDMARKS)
        if not landmarks:
            continue
        points = landmarks[0].get_points()
        if len(points) <= 10:
            continue
        wrist = points[10]  # Anatomical right wrist in COCO's 17-point skeleton.
        if wrist.confidence() < 0.4:
            continue
        box = person.get_bbox()
        x = box.xmin() + wrist.x() * box.width()
        if math.isfinite(x) and 0 <= x <= 1:
            return x
    return None


def main():
    channel = socket.socket(fileno=int(sys.argv.pop(1)))
    channel.setblocking(False)
    from hailo_apps.python.pipeline_apps.pose_estimation import pose_estimation as pose

    class GamePipeline(pose.GStreamerPoseEstimationApp):
        def get_pipeline_string(self):
            self.video_sink = "fakesink"  # Only Pygame owns a visible window.
            return super().get_pipeline_string()

    def callback(element, buffer, user_data):
        if buffer is None:
            return
        hailo = pose.hailo
        roi = hailo.get_roi_from_buffer(buffer)
        x = wrist_x(roi.get_objects_typed(hailo.HAILO_DETECTION), hailo)
        try:
            channel.send(struct.pack("!dd", time.monotonic(), -1 if x is None else x))
        except BlockingIOError:
            pass  # Never block the GStreamer streaming thread on a slow renderer.

    with channel:
        GamePipeline(callback, pose.user_app_callback_class()).run()


if __name__ == "__main__":
    main()
