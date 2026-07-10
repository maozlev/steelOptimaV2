import fitz

# a page is "raster" when a scanned image dominates it and vector content is negligible
FULL_PAGE_IMAGE_COVERAGE = 0.8
MIN_VECTOR_DRAWINGS = 5


def classify_page(page: fitz.Page) -> str:
    drawings = len(page.get_drawings())
    page_area = abs(page.rect)

    image_coverage = 0.0
    for img in page.get_images(full=True):
        for rect in page.get_image_rects(img[0]):
            image_coverage = max(image_coverage, abs(rect) / page_area)

    has_full_page_image = image_coverage >= FULL_PAGE_IMAGE_COVERAGE
    has_vector_content = drawings >= MIN_VECTOR_DRAWINGS

    if has_vector_content and has_full_page_image:
        return "mixed"
    if has_vector_content:
        return "vector"
    if has_full_page_image:
        return "raster"
    return "mixed"
