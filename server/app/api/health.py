import httpx
from fastapi import APIRouter

from app.config import settings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health():
    ollama = {"available": False, "models": []}
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(f"{settings.ollama_url}/api/tags")
            r.raise_for_status()
            ollama["available"] = True
            ollama["models"] = [m["name"] for m in r.json().get("models", [])]
    except httpx.HTTPError:
        pass
    return {"status": "ok", "ollama": ollama}
