from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STEELOPTIMA_", env_file=".env")

    # kept outside the OneDrive-synced project directory by default
    data_dir: Path = Path.home() / ".steeloptima" / "data"
    render_dpi: int = 300
    ollama_url: str = "http://localhost:11434"

    vlm_enabled: bool = True
    # Ask the model to VETO confident detections, not just rescue doubtful ones. The
    # errors that reach the operator are the confident ones — a GD&T frame scores 0.98
    # because it genuinely is a circle and a square. One call per BOM group, not per
    # cutout, so 293 identical holes cost a single question.
    vlm_verify: bool = True
    vlm_model: str = "qwen3.5:9b"
    vlm_timeout_s: float = 120.0
    # warm calls are ~6-8s on this GPU; cap keeps a page under ~2 minutes
    vlm_max_calls_per_page: int = 15
    escalation_threshold: float = 0.65
    finalize_threshold: float = 0.90

    # material-table pipeline
    # VLM assist is OFF by default for table jobs: on this hardware qwen3.5:9b
    # takes 1-4 MINUTES per table crop (partially CPU-resident even at 8k ctx),
    # while the deterministic path reads 210/210 cells on the eval and anything
    # it cannot read is flagged for a human anyway. Opt in per job with
    # {"vlm": true} when a Hebrew-heavy batch justifies the wait.
    table_vlm_enabled: bool = False
    # zoom 12 (864 dpi) is what the rec-only OCR was tuned at; 600 dpi fragments
    table_ocr_dpi: int = 864
    # numeric cells below this OCR confidence get a VLM second read
    table_ocr_conf_threshold: float = 0.85
    # per-table VLM budget: 1 classify + description strips + row repairs
    table_vlm_max_calls: int = 25
    # rows whose confidence clears this AND pass every check auto-approve
    table_row_approve_threshold: float = 0.80
    # dropping 200 PDFs into a project should just start scanning them
    table_autorun_on_upload: bool = True

    # scoped Q&A chat (document / project / cross-project summary)
    chat_enabled: bool = True
    # defaults to the VLM model because it is the one guaranteed installed; a small
    # text-only model (e.g. qwen3:4b) answers much faster — set STEELOPTIMA_CHAT_MODEL
    chat_model: str = ""
    # the context JSON must fit alongside the history: 16k tokens ~ 48k chars, so a
    # 24k-char context cap leaves room for history + answer without pushing the KV
    # cache (and with it the weights) out of VRAM — see the num_ctx note in vlm/client
    chat_num_ctx: int = 16384
    chat_context_max_chars: int = 24_000
    # conversation turns replayed to the model per question
    chat_history_messages: int = 12
    chat_timeout_s: float = 300.0

    @property
    def effective_chat_model(self) -> str:
        return self.chat_model or self.vlm_model

    @property
    def db_path(self) -> Path:
        return self.data_dir / "steel_optima.db"

    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "originals"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def crops_dir(self) -> Path:
        return self.data_dir / "crops"

    @property
    def table_crops_dir(self) -> Path:
        return self.data_dir / "tables"

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.originals_dir,
            self.renders_dir,
            self.crops_dir,
            self.table_crops_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
