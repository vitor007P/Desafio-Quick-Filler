"""Orquestração: do PDF guardado até a transcrição gravada.

O processamento não acontece dentro do request. Um cartão de ponto escaneado
de 30 páginas leva minutos em OCR, e o proxy da plataforma de deploy corta a
conexão bem antes disso. O `POST` só enfileira; o cliente descobre o fim pelo
`GET`.

A fila é um pool de threads com tamanho fixo (`MAX_CONCURRENT_JOBS`). Uploads
simultâneos entram na fila em vez de disputar CPU: com OCR, dez requisições
paralelas não são dez vezes mais rápidas — são dez vezes mais lentas cada uma,
e um container morto por memória.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from app.config import settings
from app.core.ocr import OCRIndisponivel
from app.core.pdfio import PdfIlegivel, ProcessamentoExpirou, carregar_paginas
from app.core.words import Page
from app.extractors.base import Extracao, normalizar
from app.extractors.cartao_ponto import ExtratorCartaoPonto
from app.extractors.holerite import ExtratorHolerite
from app.store import Job, store

log = logging.getLogger(__name__)

# Um extrator por tipo de documento suportado. Adicionar um tipo novo = criar
# um extrator novo e cadastrar aqui.
EXTRATORES = {
    "cartao-ponto": ExtratorCartaoPonto(),
    "holerite": ExtratorHolerite(),
}

TIPOS = tuple(EXTRATORES)  # ("cartao-ponto", "holerite")

# O pool de threads é criado sob demanda (lazy) e guardado aqui.
_executor: ThreadPoolExecutor | None = None


def executor() -> ThreadPoolExecutor:
    """Devolve o pool de threads, criando-o na primeira chamada."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=max(1, settings.max_concurrent_jobs),
            thread_name_prefix="transcricao",
        )
    return _executor


def encerrar() -> None:
    """Desliga o pool de threads. Chamado quando a aplicação está parando."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


def enfileirar(job_id: str) -> None:
    """Coloca o job na fila para ser processado em background por uma thread."""
    executor().submit(processar_job, job_id)


# --------------------------------------------------------------------------


def processar_job(job_id: str) -> None:
    """Roda a extração de um job. Nunca levanta — o erro vira status."""
    inicio = time.monotonic()  # relógio que só anda pra frente, bom pra medir duração
    try:
        job = store.obter(job_id)
    except KeyError:
        # Retenção pode ter apagado o job antes de ele sair da fila.
        log.info("job %s não existe mais, descartado", job_id)
        return

    try:
        conteudo = store.ler_pdf(job_id)
        paginas = carregar_paginas(conteudo, deadline=inicio + settings.job_timeout_seconds)

        job.paginas = len(paginas)
        job.paginas_ocr = sum(1 for p in paginas if p.origem == "ocr")

        extracao = EXTRATORES[job.tipo].extrair(paginas)

        if _vazia(job.tipo, extracao):
            # Nenhuma linha saiu de nenhuma página: melhor avisar do que devolver
            # uma planilha vazia com cara de sucesso (ver docstring de `_vazia`).
            store.gravar_erro(
                job,
                "Não consegui reconhecer este documento como "
                f"{job.tipo.replace('-', ' ')}. Nenhuma linha foi extraída — "
                "provavelmente é um layout que ainda não sei ler, ou o tipo "
                "escolhido no envio não corresponde ao arquivo.",
            )
            log.info("job %s: layout não reconhecido (%d páginas)", job_id, job.paginas)
            return

        job.notas = extracao.notas
        store.gravar_resultado(job, extracao.value, [asdict(s) for s in extracao.spans])
        log.info(
            "job %s concluído: %d páginas (%d por OCR) em %.1fs",
            job_id,
            job.paginas,
            job.paginas_ocr,
            time.monotonic() - inicio,
        )

    # Cada exceção abaixo vira uma mensagem de erro amigável, guardada no job.
    # O processamento roda em background, então esse é o único jeito do
    # usuário saber o que deu errado — por isso capturamos tudo, sem deixar
    # a thread simplesmente morrer em silêncio.
    except ProcessamentoExpirou as exc:
        store.gravar_erro(job, str(exc))
        log.warning("job %s expirou após %.0fs", job_id, time.monotonic() - inicio)
    except OCRIndisponivel as exc:
        store.gravar_erro(job, str(exc))
        log.error("job %s: OCR indisponível", job_id)
    except PdfIlegivel as exc:
        store.gravar_erro(job, str(exc))
        log.warning("job %s: PDF ilegível", job_id)
    except MemoryError:
        store.gravar_erro(job, "Documento grande demais para processar.")
        log.error("job %s: memória insuficiente", job_id)
    except Exception as exc:
        # A mensagem original pode conter trecho do documento — e o documento
        # tem CPF e salário. Vai para o log só o tipo da exceção.
        store.gravar_erro(job, "Falha inesperada ao processar o documento.")
        log.exception("job %s falhou: %s", job_id, type(exc).__name__)


def _vazia(tipo: str, extracao: Extracao) -> bool:
    """Documento em que nenhuma linha saiu de nenhuma página.

    Dizer "não sei ler este documento" é melhor que devolver uma planilha
    vazia com cara de transcrição bem-sucedida. Páginas vazias isoladas
    continuam sendo resultado válido — e viram aviso, não erro.
    """
    paginas = extracao.value.get("pages", [])
    if not paginas:
        return True
    if tipo == "cartao-ponto":
        # Nenhuma página tem nenhum dia com marcações -> considera vazio.
        return all(not p.get("days") for p in paginas)
    # Holerite: nenhuma página tem campo (fields) nem base de cálculo (bases).
    return all(not p.get("fields") and not p.get("bases") for p in paginas)


# --------------------------------------------------------------------------
# Bônus: detecção do tipo
# --------------------------------------------------------------------------

# Palavras que costumam aparecer em cada tipo de documento. Quanto mais dessas
# palavras o texto tiver, mais confiança de que é aquele tipo.
PISTAS_CARTAO = (
    "cartao de ponto", "cartao ponto", "espelho de ponto", "folha de ponto",
    "registro de ponto", "marcac", "batida", "entrada", "saida", "jornada",
    "horas normais", "apontament",
)
PISTAS_HOLERITE = (
    "recibo de pagamento", "demonstrativo de pagamento", "holerite", "contracheque",
    "vencimento", "provent", "descont", "valor liquido", "liquido a receber",
    "base inss", "base fgts", "irrf", "salario base", "competenc",
)


def detectar_tipo(paginas: Sequence[Page]) -> tuple[str, float]:
    """Adivinha o tipo pelo vocabulário do documento.

    Devolve `(tipo, confianca)` — a confiança é a margem entre os dois lados,
    para a interface poder dizer "acho que é holerite" em vez de afirmar.
    """
    # Só olha as 3 primeiras páginas: já é o suficiente pra saber o tipo, e
    # evita processar o documento inteiro só para um palpite.
    texto = normalizar(
        " ".join(w.text for p in paginas[:3] for w in p.words)
    )
    ponto = sum(1 for t in PISTAS_CARTAO if t in texto)
    folha = sum(1 for t in PISTAS_HOLERITE if t in texto)

    total = ponto + folha
    if total == 0:
        # Nenhuma pista bateu: chuta cartão de ponto, mas com confiança zero.
        return "cartao-ponto", 0.0
    if ponto >= folha:
        return "cartao-ponto", (ponto - folha) / total
    return "holerite", (folha - ponto) / total


__all__ = ["TIPOS", "Job", "detectar_tipo", "encerrar", "enfileirar", "processar_job"]
