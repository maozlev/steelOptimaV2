"""Drawing → proposed plan items, for the planning tab's attach/whiteboard flow.

Stateless on purpose: nothing is persisted here. The response is a proposal
list; the plan itself lives client-side until the user accepts items.
"""

from fastapi import APIRouter, HTTPException, UploadFile

from app.planning.analyze import analyze_image, analyze_pdf
from app.vlm.client import OllamaVlmClient

router = APIRouter(prefix="/api/planning", tags=["planning"])

MAX_BYTES = 40 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


@router.post("/analyze")
async def analyze(file: UploadFile):
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "file too large (40 MB max)")
    if not data:
        raise HTTPException(422, "empty file")

    name = (file.filename or "").lower()
    ctype = (file.content_type or "").lower()

    if ctype == "application/pdf" or name.endswith(".pdf"):
        try:
            return analyze_pdf(data)
        except Exception as e:
            raise HTTPException(422, f"could not read the PDF: {e}")

    if ctype in IMAGE_TYPES or name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        client = OllamaVlmClient()
        if not client.available():
            raise HTTPException(
                503,
                "reading an image needs the local vision model and it is not "
                "available — is Ollama running? (PDFs are read without it)",
            )
        return analyze_image(data, client)

    raise HTTPException(422, f"unsupported file type {ctype or name!r} — send PDF, PNG or JPEG")
