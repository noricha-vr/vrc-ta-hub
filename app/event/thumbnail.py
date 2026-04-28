from PIL import Image

SLIDE_THUMBNAIL_ASPECT_RATIO = 16 / 9
SLIDE_THUMBNAIL_ASPECT_RATIO_TEXT = '16:9'


def crop_to_slide_thumbnail_aspect_ratio(image: Image.Image) -> Image.Image:
    """画像を中央基準でスライド比率にクロップする."""
    width, height = image.size
    if width <= 0 or height <= 0:
        return image

    current_ratio = width / height
    if current_ratio > SLIDE_THUMBNAIL_ASPECT_RATIO:
        new_width = int(height * SLIDE_THUMBNAIL_ASPECT_RATIO)
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))

    if current_ratio < SLIDE_THUMBNAIL_ASPECT_RATIO:
        new_height = int(width / SLIDE_THUMBNAIL_ASPECT_RATIO)
        top = (height - new_height) // 2
        return image.crop((0, top, width, top + new_height))

    return image
