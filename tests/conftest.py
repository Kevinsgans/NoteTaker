import cv2
import numpy as np
import pytest


@pytest.fixture
def page_image():
    image = np.full((600, 800, 3), 30, np.uint8)
    cv2.rectangle(image, (120, 40), (690, 560), (235, 238, 240), -1)
    for y in range(100, 530, 45):
        cv2.putText(
            image,
            "Eigenvectors A v = lambda v",
            (150, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (30, 30, 30),
            2,
        )
    return image
