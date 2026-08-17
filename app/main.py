"""Ponto de entrada da aplicação."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.pipeline import encerrar
from app.store import store

# Pasta com o frontend (HTML/CSS/JS), servida direto pelo FastAPI.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def configurar_log() -> None:
    """Liga o log da aplicação no nível configurado (INFO por padrão)."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # O access log do uvicorn imprime a URL, e a URL tem o id da transcrição.
    # O id não é PII, mas também não precisa ficar em log de produção.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


async def _varredor_retencao() -> None:
    """Aplica a política de retenção enquanto a aplicação estiver de pé.

    Um loop infinito que dorme e acorda de tempos em tempos (`sweep_seconds`)
    para apagar transcrições vencidas. `asyncio.to_thread` roda a limpeza numa
    thread separada, porque ela mexe em disco e não pode travar o event loop.
    """
    while True:
        try:
            await asyncio.to_thread(store.limpar_expirados)
        except Exception:
            logging.getLogger(__name__).exception("varredor de retenção falhou")
        await asyncio.sleep(max(30, settings.sweep_seconds))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Código que roda uma vez ao ligar e uma vez ao desligar a aplicação."""
    configurar_log()
    store.limpar_expirados()  # limpeza inicial, antes de aceitar requisições
    tarefa = asyncio.create_task(_varredor_retencao())  # agenda a limpeza periódica
    try:
        yield  # a aplicação fica no ar aqui, atendendo requisições
    finally:
        # Ao desligar: para o varredor e o pool de threads de processamento.
        tarefa.cancel()
        encerrar()


app = FastAPI(
    title="Transcrição de documentos trabalhistas",
    description="Cartão de ponto e holerite em PDF → planilha revisável.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS só é ligado se houver origem configurada (CORS_ORIGINS). Sem isso, o
# navegador bloqueia chamadas de um domínio diferente do da API.
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["*"],
    )

app.include_router(router)

# Serve o frontend estático (index.html, app.js, estilo.css) na raiz "/".
# Precisa vir por último: rotas da API são resolvidas antes, e o resto cai aqui.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
