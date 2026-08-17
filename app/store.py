"""Persistência das transcrições em disco.

Por que disco e não banco: o estado é pequeno, tem prazo de validade curto e
não é consultado por nenhum critério além do id. Um Postgres aqui seria
infraestrutura para guardar dado que a política de retenção manda apagar em
uma hora.

O nome original do arquivo enviado **não** é guardado. Nomes de holerite e
cartão de ponto costumam ser "FULANO DE TAL - 03-2020.pdf": guardar isso é
guardar PII sem necessidade, e vazá-lo em log é pior ainda.
"""

from __future__ import annotations

import json
import logging
import secrets  # gera o id do job de forma aleatória e segura
import shutil  # apagar pastas inteiras (rmtree)
import threading  # trava (lock) para evitar condição de corrida ao gravar
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

# Os três status possíveis de um job, como constantes (evita erro de digitação
# ao escrever a string à mão em vários lugares do código).
STATUS_PROCESSANDO = "processando"
STATUS_CONCLUIDO = "concluido"
STATUS_ERRO = "erro"


@dataclass
class Job:
    """Representa uma transcrição em andamento (ou já concluída/com erro)."""

    id: str
    tipo: str
    status: str = STATUS_PROCESSANDO
    erro: str | None = None
    criado_em: str = ""
    atualizado_em: str = ""
    paginas: int = 0
    #: Quantas páginas precisaram de OCR — útil na tela e no diagnóstico.
    paginas_ocr: int = 0
    notas: list[str] = field(default_factory=list)


def _agora() -> str:
    """Data/hora atual em UTC, no formato ISO (ex.: '2026-08-17T12:00:00+00:00')."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    """Lê e grava os jobs em disco, um por pasta, dentro de `storage_dir/jobs`."""

    def __init__(self, raiz: Path | None = None) -> None:
        self.raiz = (raiz or settings.storage_dir) / "jobs"
        self.raiz.mkdir(parents=True, exist_ok=True)  # cria a pasta se não existir
        self._lock = threading.RLock()  # protege escritas concorrentes no mesmo job

    # -- caminhos ---------------------------------------------------------

    def _dir(self, job_id: str) -> Path:
        """Devolve a pasta de um job, validando o id antes de tocar no disco."""
        # Ids são gerados por nós, mas o valor chega pela URL: rejeitamos
        # qualquer coisa que não seja o alfabeto esperado antes de tocar disco.
        if not job_id or not all(c.isalnum() or c in "-_" for c in job_id):
            raise KeyError(job_id)
        return self.raiz / job_id

    def caminho_pdf(self, job_id: str) -> Path:
        return self._dir(job_id) / "entrada.pdf"

    # -- escrita ----------------------------------------------------------

    def criar(self, tipo: str, conteudo: bytes) -> Job:
        """Cria um novo job: gera um id, salva o PDF e grava os metadados iniciais."""
        job_id = secrets.token_urlsafe(9)  # id aleatório, seguro para usar em URL
        destino = self._dir(job_id)
        destino.mkdir(parents=True, exist_ok=False)  # falha se já existir (não deveria)

        (destino / "entrada.pdf").write_bytes(conteudo)
        job = Job(id=job_id, tipo=tipo, criado_em=_agora(), atualizado_em=_agora())
        self._gravar_meta(job)
        return job

    def _gravar_meta(self, job: Job) -> None:
        """Grava meta.json de forma atômica, para nunca deixar o arquivo pela metade."""
        job.atualizado_em = _agora()
        caminho = self._dir(job.id) / "meta.json"
        temporario = caminho.with_suffix(".tmp")
        # Escreve primeiro num arquivo temporário e só depois renomeia por cima
        # do definitivo. Se o processo cair no meio, o meta.json antigo continua
        # íntegro (rename é uma operação atômica no sistema de arquivos).
        temporario.write_text(json.dumps(asdict(job), ensure_ascii=False), encoding="utf-8")
        temporario.replace(caminho)  # troca atômica: nunca meta.json pela metade

    def atualizar(self, job: Job) -> None:
        with self._lock:
            self._gravar_meta(job)

    def gravar_resultado(self, job: Job, value: dict, spans: list[dict]) -> None:
        """Salva o resultado da extração e marca o job como concluído."""
        with self._lock:
            destino = self._dir(job.id)
            (destino / "value.json").write_text(
                json.dumps(value, ensure_ascii=False), encoding="utf-8"
            )
            (destino / "spans.json").write_text(
                json.dumps(spans, ensure_ascii=False), encoding="utf-8"
            )
            job.status = STATUS_CONCLUIDO
            job.erro = None
            self._gravar_meta(job)

    def gravar_erro(self, job: Job, mensagem: str) -> None:
        with self._lock:
            job.status = STATUS_ERRO
            job.erro = mensagem
            self._gravar_meta(job)

    def substituir_value(self, job_id: str, value: dict) -> None:
        """Sobrescreve o `value` de um job já concluído (correção manual via PUT)."""
        with self._lock:
            destino = self._dir(job_id)
            if not (destino / "meta.json").exists():
                raise KeyError(job_id)
            (destino / "value.json").write_text(
                json.dumps(value, ensure_ascii=False), encoding="utf-8"
            )

    # -- leitura ----------------------------------------------------------

    def obter(self, job_id: str) -> Job:
        caminho = self._dir(job_id) / "meta.json"
        if not caminho.exists():
            raise KeyError(job_id)
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return Job(**dados)  # reconstrói o dataclass a partir do dicionário salvo

    def obter_value(self, job_id: str) -> dict | None:
        caminho = self._dir(job_id) / "value.json"
        if not caminho.exists():
            return None  # job ainda processando (ou deu erro): não tem value ainda
        return json.loads(caminho.read_text(encoding="utf-8"))

    def obter_spans(self, job_id: str) -> list[dict]:
        caminho = self._dir(job_id) / "spans.json"
        if not caminho.exists():
            return []
        return json.loads(caminho.read_text(encoding="utf-8"))

    def ler_pdf(self, job_id: str) -> bytes:
        caminho = self.caminho_pdf(job_id)
        if not caminho.exists():
            raise KeyError(job_id)
        return caminho.read_bytes()

    # -- retenção ---------------------------------------------------------

    def limpar_expirados(self) -> int:
        """Apaga tudo que passou do prazo. Documento e transcrição juntos."""
        if settings.retention_minutes <= 0:
            return 0  # retenção desligada: nunca apaga nada

        limite = time.time() - settings.retention_minutes * 60
        removidos = 0
        for pasta in list(self.raiz.iterdir()):  # cada pasta = um job
            if not pasta.is_dir():
                continue
            try:
                if pasta.stat().st_mtime < limite:  # data da última modificação
                    shutil.rmtree(pasta, ignore_errors=True)  # apaga a pasta toda
                    removidos += 1
            except OSError:
                # Pasta pode ter sido apagada por outra execução concorrente — ignora.
                continue

        if removidos:
            log.info("retenção: %d transcrições expiradas removidas", removidos)
        return removidos


# Instância única, compartilhada por toda a aplicação — importe `store` daqui.
store = JobStore()
