"""Leitura do PDF e decisão entre camada de texto e OCR.

Regra: cada página decide sozinha. Documentos reais chegam misturados — um
holerite nativo com uma página escaneada anexada é comum — então tratar o
arquivo inteiro como "texto" ou "imagem" perde dados de um lado ou queima CPU
do outro.
"""

from __future__ import annotations

import logging
import re
import time

from app.config import settings
from app.core.ocr import OCRIndisponivel, ocr_page
from app.core.words import Page, Word

log = logging.getLogger(__name__)

_MAGIC = b"%PDF-"
_ALNUM = re.compile(r"[0-9A-Za-zÀ-ÿ]")


class PdfInvalido(ValueError):
    """Arquivo recusado antes de qualquer processamento."""


class PdfIlegivel(RuntimeError):
    """Arquivo é um PDF, mas não deu para ler o conteúdo."""


def validar_pdf(conteudo: bytes) -> int:
    """Valida o upload e devolve o número de páginas.

    Não confia na extensão nem no content-type do cliente: um `.txt` renomeado
    para `.pdf` chega com os dois corretos. O que vale é o arquivo abrir.
    """
    if not conteudo:
        raise PdfInvalido("Arquivo vazio.")

    if not conteudo[:1024].lstrip().startswith(_MAGIC):
        raise PdfInvalido("O arquivo não é um PDF (assinatura %PDF- ausente).")

    import fitz  # import tardio: PyMuPDF só é carregado quando um PDF chega de fato

    try:
        doc = fitz.open(stream=conteudo, filetype="pdf")
    except Exception as exc:
        raise PdfInvalido("PDF corrompido ou ilegível.") from exc

    try:
        if doc.needs_pass:
            raise PdfInvalido("PDF protegido por senha.")

        paginas = doc.page_count
        if paginas <= 0:
            raise PdfInvalido("PDF sem páginas.")
        if paginas > settings.max_pages:
            raise PdfInvalido(
                f"PDF com {paginas} páginas excede o limite de {settings.max_pages}."
            )

        # Um cabeçalho válido não garante um corpo válido: força o parse da
        # primeira página para pegar arquivo truncado agora, não no worker.
        try:
            doc.load_page(0).get_text("words")
        except Exception as exc:
            raise PdfInvalido("PDF corrompido: não foi possível ler a primeira página.") from exc

        return paginas
    finally:
        doc.close()


def _densidade_texto(words: list[Word]) -> int:
    """Conta quantos caracteres alfanuméricos "de verdade" a página tem.

    Usado para decidir se a página tem texto real (PDF nativo) ou se é
    imagem escaneada disfarçada de PDF (nesse caso a contagem fica baixa).
    """
    return sum(len(_ALNUM.findall(w.text)) for w in words)


class ProcessamentoExpirou(TimeoutError):
    """O documento passou do tempo máximo permitido por job."""


def carregar_paginas(conteudo: bytes, deadline: float | None = None) -> list[Page]:
    """Extrai as palavras posicionadas de cada página, com OCR quando preciso.

    `deadline` é um instante de `time.monotonic()`. Um PDF de 60 páginas
    escaneadas leva minutos em OCR; sem teto, um único upload segura um worker
    para sempre e a fila para de andar.
    """
    import fitz

    try:
        doc = fitz.open(stream=conteudo, filetype="pdf")
    except Exception as exc:
        raise PdfIlegivel("Não foi possível abrir o PDF.") from exc

    paginas: list[Page] = []
    try:
        for indice in range(doc.page_count):
            if deadline is not None and time.monotonic() > deadline:
                raise ProcessamentoExpirou(
                    f"Tempo máximo de processamento excedido na página {indice + 1}."
                )

            pagina = doc.load_page(indice)
            largura, altura = pagina.rect.width, pagina.rect.height

            try:
                brutas = pagina.get_text("words")  # camada de texto nativa do PDF
            except Exception:  # noqa: BLE001 - uma página quebrada não derruba o documento
                log.warning("falha ao ler camada de texto da página %d", indice + 1)
                brutas = []

            # Cada item de `brutas` é uma tupla (x0, y0, x1, y1, texto, ...);
            # convertemos para o nosso tipo Word, descartando entradas vazias.
            palavras = [
                Word(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3], conf=100.0, src="text")
                for w in brutas
                if str(w[4]).strip()
            ]

            origem = "text"
            conf_media: float | None = None

            if _densidade_texto(palavras) < settings.text_layer_min_chars:
                # Pouco texto nativo: essa página provavelmente é uma imagem
                # escaneada, então tentamos OCR nela.
                if not settings.ocr_enabled:
                    log.warning("página %d parece escaneada e OCR está desligado", indice + 1)
                else:
                    try:
                        palavras_ocr, conf_media = ocr_page(pagina)
                    except OCRIndisponivel:
                        raise  # sem Tesseract instalado: erro sobe e vira falha do job
                    except Exception as exc:  # noqa: BLE001
                        log.warning("OCR falhou na página %d: %s", indice + 1, type(exc).__name__)
                    else:
                        palavras = palavras_ocr
                        origem = "ocr"

            paginas.append(
                Page(
                    number=indice + 1,
                    width=largura,
                    height=altura,
                    words=palavras,
                    origem=origem,  # type: ignore[arg-type]
                    ocr_mean_conf=conf_media,
                )
            )
    finally:
        doc.close()

    return paginas
