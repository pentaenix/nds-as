from rae.easyfind.color_buckets import MODEL_THUMB_BACKGROUND_RGB, dominant_bucket_from_rgba


def _rgba_pixel(r: int, g: int, b: int, a: int = 255) -> bytes:
    return bytes([r, g, b, a])


def _checkerboard_rgba(
    width: int,
    height: int,
    *,
    foreground: tuple[int, int, int],
    background: tuple[int, int, int] = MODEL_THUMB_BACKGROUND_RGB,
) -> bytes:
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            color = foreground if (x + y) % 2 == 0 else background
            pixels.extend((*color, 255))
    return bytes(pixels)


def test_dominant_bucket_green():
    rgba = bytes([40, 200, 60, 255] * 64)
    bucket, colors, secondary, brightness, saturation, transparent = dominant_bucket_from_rgba(
        rgba, 8, 8,
    )
    assert bucket == "green"
    assert colors
    assert isinstance(secondary, list)
    assert brightness in {"mid", "bright"}
    assert saturation in {"mid", "high"}
    assert brightness in {"mid", "bright"}
    assert saturation in {"mid", "high"}


def test_dominant_bucket_red():
    rgba = bytes([220, 40, 40, 255] * 64)
    bucket, *_ = dominant_bucket_from_rgba(rgba, 8, 8)
    assert bucket == "red"


def test_ignores_model_thumbnail_background_gray():
    rgba = _checkerboard_rgba(16, 16, foreground=(40, 200, 60))
    bucket, colors, *_ = dominant_bucket_from_rgba(rgba, 16, 16, sample_stride=1)
    assert bucket == "green"
    assert "#3a3a3a" not in colors


def test_gray_model_not_confused_with_background():
    rgba = bytes(_rgba_pixel(150, 150, 150) * 64)
    bucket, *_ = dominant_bucket_from_rgba(rgba, 8, 8)
    assert bucket == "gray"


def test_brown_primary_with_green_accent_is_secondary():
    pixels = bytearray()
    for i in range(100):
        if i < 70:
            pixels.extend(_rgba_pixel(110, 65, 35))
        elif i < 85:
            pixels.extend(_rgba_pixel(140, 140, 140))
        else:
            pixels.extend(_rgba_pixel(40, 180, 60))
    bucket, _, secondary, *_ = dominant_bucket_from_rgba(bytes(pixels), 10, 10, sample_stride=1)
    assert bucket != "green"
    assert "green" in secondary


def test_background_matte_does_not_steal_dominant_color():
    pixels = bytearray()
    for i in range(64):
        if i < 40:
            pixels.extend(_rgba_pixel(*MODEL_THUMB_BACKGROUND_RGB))
        else:
            pixels.extend(_rgba_pixel(220, 40, 40))
    bucket, *_ = dominant_bucket_from_rgba(bytes(pixels), 8, 8, sample_stride=1)
    assert bucket == "red"
