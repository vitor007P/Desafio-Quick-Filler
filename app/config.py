"""Configuração da aplicação — tudo vem de variável de ambiente.

Nenhum segredo mora no repositório: os defaults aqui são valores operacionais
seguros para desenvolvimento, e produção sobrescreve o que precisar.
"""

from __future__ import annotations

import os  # para ler as variáveis de ambiente (os.environ)
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    """Lê uma variável de ambiente inteira; se faltar ou for inválida, usa o default."""
    raw = os.environ.get(name)  # None se a variável não existir
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        # Alguém colocou algo que não é número (ex.: "abc") -> ignora e usa o default
        return default


def _bool(name: str, default: bool) -> bool:
    """Lê uma variável de ambiente booleana. Aceita '1', 'true', 'yes', 'on', 'sim'."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _str(name: str, default: str) -> str:
    """Lê uma variável de ambiente texto, sem espaços nas pontas."""
    raw = os.environ.get(name)
    return default if raw is None or raw.strip() == "" else raw.strip()


@dataclass(frozen=True)  # frozen=True: depois de criado, o objeto não pode ser alterado
class Settings:
    # --- Armazenamento e retenção -------------------------------------------
    # Onde os PDFs e as transcrições ficam salvos em disco.
    storage_dir: Path = field(
        default_factory=lambda: Path(_str("STORAGE_DIR", "./data")).resolve()
    )
    #: Transcrições e PDFs são apagados após esse tempo. 0 = nunca (não recomendado).
    retention_minutes: int = field(default_factory=lambda: _int("RETENTION_MINUTES", 60))
    #: Intervalo do varredor que aplica a retenção.
    sweep_seconds: int = field(default_factory=lambda: _int("SWEEP_SECONDS", 300))

    # --- Limites de upload ---------------------------------------------------
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 20))
    max_pages: int = field(default_factory=lambda: _int("MAX_PAGES", 60))

    # --- Processamento -------------------------------------------------------
    #: Quantas extrações rodam ao mesmo tempo. Protege CPU/memória do OCR.
    max_concurrent_jobs: int = field(default_factory=lambda: _int("MAX_CONCURRENT_JOBS", 2))
    #: Teto de tempo por documento; estourou, o job vai para status "erro".
    job_timeout_seconds: int = field(default_factory=lambda: _int("JOB_TIMEOUT_SECONDS", 900))

    # --- OCR -----------------------------------------------------------------
    ocr_enabled: bool = field(default_factory=lambda: _bool("OCR_ENABLED", True))
    ocr_lang: str = field(default_factory=lambda: _str("OCR_LANG", "por"))
    ocr_dpi: int = field(default_factory=lambda: _int("OCR_DPI", 300))
    #: Confiança (0-100) abaixo da qual caracteres ambíguos viram "?".
    ocr_low_conf: int = field(default_factory=lambda: _int("OCR_LOW_CONF", 70))
    #: Caminho do binário, quando não está no PATH.
    tesseract_cmd: str = field(default_factory=lambda: _str("TESSERACT_CMD", ""))

    # --- Detecção de página escaneada ---------------------------------------
    #: Menos que isso de caracteres na camada de texto => a página é imagem.
    text_layer_min_chars: int = field(default_factory=lambda: _int("TEXT_LAYER_MIN_CHARS", 40))

    # --- HTTP ----------------------------------------------------------------
    cors_origins: str = field(default_factory=lambda: _str("CORS_ORIGINS", ""))
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO"))

    @property
    def max_upload_bytes(self) -> int:
        # Converte megabytes para bytes, que é a unidade que o código usa de fato.
        return self.max_upload_mb * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        # "CORS_ORIGINS" chega como texto separado por vírgula (ex.: "a.com,b.com").
        # Aqui a gente separa e tira espaço em branco de cada item.
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


# Instância única, compartilhada por toda a aplicação — importe `settings` daqui.
settings = Settings()
