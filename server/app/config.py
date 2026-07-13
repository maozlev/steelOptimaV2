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
