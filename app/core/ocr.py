"""OCR com Tesseract.

Escolha do Tesseract e não de um serviço de nuvem: os documentos trazem CPF,
matrícula e salário de pessoas reais, e mandar isso para um terceiro é uma
decisão de privacidade que o desafio não pede. Rodando local, o PDF não sai do
container.

O importante aqui não é o texto — é a confiança por palavra. É ela que
alimenta a marcação de `?`, então descartá-la em favor de uma string simples
inviabilizaria o critério de honestidade.
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from app.config import settings
from app.core.words import Word

if TYPE_CHECKING:  # pragma: no cover
    import fitz

log = logging.getLogger(__name__)

_tesseract_ready: bool | None = None


class OCRIndisponivel(RuntimeError):
    """Tesseract não está instalado ou não respondeu."""


def _ensure_tesseract() -> None:
    """Confirma que o binário do Tesseract responde. Resultado fica em cache."""
    global _tesseract_ready
    if _tesseract_ready:
        return  # já checamos antes nesta execução, não precisa checar de novo

    import pytesseract  # import tardio: só carrega essa lib se OCR for realmente usado

    if settings.tesseract_cmd:
        # Caminho explícito do binário (ex.: em ambiente onde não está no PATH).
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

    try:
        pytesseract.get_tesseract_version()  # chamada simples só pra testar se funciona
    except Exception as exc:
        raise OCRIndisponivel(
            "Tesseract não encontrado. Instale o binário ou defina TESSERACT_CMD."
        ) from exc

    _tesseract_ready = True


def tesseract_disponivel() -> bool:
    """Versão "segura" de `_ensure_tesseract`: devolve True/False em vez de lançar erro."""
    try:
        _ensure_tesseract()
    except OCRIndisponivel:
        return False
    return True


def _idiomas_disponiveis(pytesseract) -> set[str]:
    """Idiomas (pacotes de treino) instalados no Tesseract, ex.: {"por", "eng"}."""
    try:
        return set(pytesseract.get_languages(config=""))
    except Exception:  # noqa: BLE001 - versões antigas não expõem a chamada
        return set()


def ocr_page(page: fitz.Page, psm: int = 6) -> tuple[list[Word], float]:
    """Renderiza a página e devolve as palavras reconhecidas, em pontos de PDF.

    As coordenadas voltam para o sistema do PDF (72 dpi) para que uma palavra
    vinda de OCR e uma vinda da camada de texto sejam intercambiáveis para o
    resto do pipeline.
    """
    import pytesseract
    from PIL import Image

    _ensure_tesseract()

    dpi = max(settings.ocr_dpi, 72)  # nunca renderiza abaixo da resolução nativa do PDF
    escala = 72.0 / dpi  # fator para converter pixel da imagem de volta a "ponto" de PDF

    import fitz

    # Renderiza a página como imagem em tons de cinza, na resolução configurada.
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), colorspace=fitz.csGRAY)
    imagem = Image.open(io.BytesIO(pix.tobytes("png")))

    lang = settings.ocr_lang
    disponiveis = _idiomas_disponiveis(pytesseract)
    if disponiveis and lang not in disponiveis:
        # Idioma configurado (ex.: "por") não está instalado: usa inglês, ou
        # qualquer outro idioma disponível, em vez de falhar o job inteiro.
        fallback = "eng" if "eng" in disponiveis else next(iter(disponiveis))
        log.warning("idioma OCR %r indisponível, usando %r", lang, fallback)
        lang = fallback

    # image_to_data devolve cada palavra reconhecida com posição e confiança
    # (em vez de só o texto corrido), que é o que o resto do pipeline precisa.
    dados = pytesseract.image_to_data(
        imagem,
        lang=lang,
        config=f"--oem 3 --psm {psm}",
        output_type=pytesseract.Output.DICT,
    )

    palavras: list[Word] = []
    confs: list[float] = []
    total = len(dados.get("text", []))
    for i in range(total):
        texto = (dados["text"][i] or "").strip()
        if not texto:
            continue  # posição sem texto (espaço, linha em branco) — ignora
        try:
            conf = float(dados["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            # -1 é ruído do motor (separadores, blocos vazios), não texto lido.
            continue

        # Coordenadas do Tesseract vêm em pixels da imagem renderizada;
        # multiplicamos pela escala para voltar à unidade "ponto" do PDF.
        left = float(dados["left"][i]) * escala
        top = float(dados["top"][i]) * escala
        largura = float(dados["width"][i]) * escala
        altura = float(dados["height"][i]) * escala

        palavras.append(
            Word(
                text=texto,
                x0=left,
                y0=top,
                x1=left + largura,
                y1=top + altura,
                conf=conf,
                src="ocr",
            )
        )
        confs.append(conf)

    media = sum(confs) / len(confs) if confs else 0.0  # confiança média da página inteira
    return palavras, media
