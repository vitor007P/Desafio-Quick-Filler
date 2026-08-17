"""Contrato HTTP.

Os quatro endpoints de `/api/transcricoes` e o `/healthz` são literais como o
enunciado define. Os extras (`/arquivo`, `/grade`, `/spans`) existem para a
interface e não alteram nenhum dos obrigatórios.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.pdfio import PdfInvalido, validar_pdf
from app.grid import montar_grade
from app.models import (
    TranscricaoCreated,
    TranscricaoOut,
    TranscricaoUpdateIn,
    parse_value,
)
from app.pipeline import TIPOS, enfileirar
from app.spreadsheet.writers import FORMATOS, gerar
from app.store import STATUS_CONCLUIDO, store

log = logging.getLogger(__name__)

router = APIRouter()

_TAMANHO_BLOCO = 256 * 1024  # lê o upload em pedaços de 256 KB


async def _ler_com_limite(arquivo: UploadFile) -> bytes:
    """Lê o upload abortando no limite, em vez de depois de recebido inteiro."""
    limite = settings.max_upload_bytes
    partes: list[bytes] = []
    total = 0

    while True:
        bloco = await arquivo.read(_TAMANHO_BLOCO)
        if not bloco:
            break  # acabou o arquivo
        total += len(bloco)
        if total > limite:
            # Corta a leitura assim que passa do limite, em vez de esperar o
            # upload inteiro terminar para só então rejeitar.
            raise HTTPException(
                status_code=413,
                detail=f"Arquivo maior que o limite de {settings.max_upload_mb} MB.",
            )
        partes.append(bloco)

    return b"".join(partes)  # junta todos os pedaços num único bytes


@router.post("/api/transcricoes", status_code=202, response_model=TranscricaoCreated)
async def criar_transcricao(
    arquivo: UploadFile = File(...),  # noqa: B008 - padrão do FastAPI: resolvido por request, não no import
    tipo: str = Form(...),
) -> JSONResponse:
    """Recebe o PDF, valida, salva e enfileira o processamento. Não espera terminar."""
    if tipo not in TIPOS:
        raise HTTPException(
            status_code=400,
            detail=f"tipo inválido. Use um de: {', '.join(TIPOS)}.",
        )

    conteudo = await _ler_com_limite(arquivo)

    # Validação antes de enfileirar: um .txt renomeado para .pdf é recusado com
    # 4xx, não vira job que termina em erro cinco minutos depois.
    try:
        paginas = validar_pdf(conteudo)
    except PdfInvalido as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = store.criar(tipo=tipo, conteudo=conteudo)
    enfileirar(job.id)  # processamento acontece em background, fora deste request

    # Sem nome de arquivo no log: ele carrega o nome da pessoa.
    log.info("job %s criado: tipo=%s páginas=%d bytes=%d", job.id, tipo, paginas, len(conteudo))

    return JSONResponse(status_code=202, content={"id": job.id})


@router.get("/api/transcricoes/{transcricao_id}", response_model=TranscricaoOut)
def obter_transcricao(transcricao_id: str) -> TranscricaoOut:
    """Consulta o status/resultado de uma transcrição (o cliente faz polling aqui)."""
    job = _job(transcricao_id)
    value = store.obter_value(job.id) if job.status == STATUS_CONCLUIDO else None
    return TranscricaoOut(
        id=job.id,
        tipo=job.tipo,  # type: ignore[arg-type]
        status=job.status,  # type: ignore[arg-type]
        erro=job.erro,
        value=value,
    )


@router.put("/api/transcricoes/{transcricao_id}", response_model=TranscricaoOut)
def atualizar_transcricao(transcricao_id: str, corpo: TranscricaoUpdateIn) -> TranscricaoOut:
    """Aplica uma correção manual feita pelo usuário na tela de revisão."""
    job = _job(transcricao_id)

    try:
        validado = parse_value(job.tipo, corpo.value)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"value fora do formato de {job.tipo}: {exc}"
        ) from exc

    store.substituir_value(job.id, validado.model_dump())
    if job.status != STATUS_CONCLUIDO:
        # Correção manual sobre um job que falhou também é transcrição válida.
        job.status = STATUS_CONCLUIDO
        job.erro = None
        store.atualizar(job)

    log.info("job %s atualizado pela revisão", job.id)
    return TranscricaoOut(
        id=job.id,
        tipo=job.tipo,  # type: ignore[arg-type]
        status=job.status,  # type: ignore[arg-type]
        erro=job.erro,
        value=store.obter_value(job.id),
    )


@router.get("/api/transcricoes/{transcricao_id}/planilha")
def baixar_planilha(
    transcricao_id: str,
    formato: str = Query("xlsx", pattern="^(xlsx|csv|json)$"),
) -> Response:
    """Gera e devolve o arquivo para download, no formato pedido."""
    job = _job(transcricao_id)
    value = store.obter_value(job.id)
    if value is None:
        raise HTTPException(status_code=409, detail="Transcrição ainda não está pronta.")

    grade = montar_grade(job.tipo, value)
    conteudo = gerar(grade, formato)
    extensao, mime = FORMATOS[formato]

    return Response(
        content=conteudo,
        media_type=mime,
        headers={
            "Content-Disposition": (
                f'attachment; filename="transcricao-{job.id}.{extensao}"'
            )
        },
    )


# --------------------------------------------------------------------------
# Apoio à interface
# --------------------------------------------------------------------------


@router.get("/api/transcricoes/{transcricao_id}/grade")
def obter_grade(transcricao_id: str) -> dict:
    """Tabela pronta com os avisos já calculados — o mesmo dado da planilha."""
    job = _job(transcricao_id)
    value = store.obter_value(job.id)
    if value is None:
        raise HTTPException(status_code=409, detail="Transcrição ainda não está pronta.")
    return {"tipo": job.tipo, "grade": montar_grade(job.tipo, value).to_dict()}


@router.get("/api/transcricoes/{transcricao_id}/spans")
def obter_spans(transcricao_id: str) -> dict:
    """Coordenadas de origem de cada valor, para a rastreabilidade visual."""
    job = _job(transcricao_id)
    return {"spans": store.obter_spans(job.id)}


@router.get("/api/transcricoes/{transcricao_id}/arquivo")
def obter_arquivo(transcricao_id: str) -> Response:
    """Devolve o PDF original enviado, para a interface mostrar lado a lado."""
    job = _job(transcricao_id)
    try:
        conteudo = store.ler_pdf(job.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Documento não está mais disponível.") from exc
    return Response(
        content=conteudo,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/info")
def info() -> dict:
    """Configurações que a interface precisa saber antes de deixar enviar um arquivo."""
    from app.core.ocr import tesseract_disponivel

    return {
        "tipos": list(TIPOS),
        "max_upload_mb": settings.max_upload_mb,
        "max_pages": settings.max_pages,
        "retencao_minutos": settings.retention_minutes,
        "ocr": settings.ocr_enabled and tesseract_disponivel(),
    }


@router.get("/healthz")
def healthz() -> dict:
    """Usado pelo Docker/Vercel para saber se a aplicação está no ar."""
    return {"status": "ok"}


def _job(transcricao_id: str):
    """Busca o job pelo id ou devolve 404 — atalho usado em quase toda rota acima."""
    try:
        return store.obter(transcricao_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Transcrição não encontrada.") from exc
