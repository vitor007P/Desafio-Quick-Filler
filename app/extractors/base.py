"""Contrato comum dos extratores.

Cartão de ponto e holerite compartilham tudo até aqui — leitura do PDF,
palavras posicionadas, saneamento de campos — e voltam a compartilhar tudo
depois: grade, planilha, revisão, download. O que muda é só o que está abaixo
desta interface.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.core.words import Page, Word


@dataclass(slots=True)
class Span:
    """De onde, no PDF, saiu um valor da transcrição.

    Fica fora do JSON do contrato de propósito: o formato de saída é literal e
    acrescentar campos seria divergir dele. As coordenadas viajam num arquivo
    paralelo, indexadas pelo caminho do campo no JSON.
    """

    path: str
    page: int
    bbox: tuple[float, float, float, float]
    origem: str = "text"


@dataclass(slots=True)
class Extracao:
    value: dict
    spans: list[Span] = field(default_factory=list)
    #: Diagnóstico livre para o SOLUCAO.md e para depuração — não vai na API.
    notas: list[str] = field(default_factory=list)


class Extrator(Protocol):
    """Interface que todo extrator (cartão de ponto, holerite, ...) implementa."""

    tipo: str

    def extrair(self, paginas: Sequence[Page]) -> Extracao: ...


# --------------------------------------------------------------------------
# Utilitários de texto para casar rótulos de cabeçalho
# --------------------------------------------------------------------------


def normalizar(texto: str) -> str:
    """Minúsculas sem acento — para comparar rótulos, nunca para guardar dados.

    Cabeçalhos chegam como "SAÍDA", "Saida", "SAlDA" (OCR). Comparar a forma
    normalizada é o que faz o mesmo código ler layouts diferentes.
    """
    # NFKD separa a letra do acento (ex.: "á" -> "a" + acento combinante);
    # descartando os caracteres "combining" sobra só a letra sem acento.
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento.lower().strip()


def contem_alguma(texto: str, termos: Sequence[str]) -> bool:
    """True se pelo menos um dos termos aparece no texto (comparação normalizada)."""
    alvo = normalizar(texto)
    return any(t in alvo for t in termos)


def contar_termos(texto: str, termos: Sequence[str]) -> int:
    """Quantos dos termos aparecem no texto (comparação normalizada)."""
    alvo = normalizar(texto)
    return sum(1 for t in termos if t in alvo)


def juntar(palavras: Sequence[Word], sep: str = " ") -> str:
    """Concatena o texto de várias palavras num único texto, separado por `sep`."""
    return sep.join(w.text for w in palavras).strip()


def envelope(palavras: Sequence[Word]) -> tuple[float, float, float, float]:
    """Caixa que contém todas as palavras — usada para rastreabilidade visual."""
    if not palavras:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(w.x0 for w in palavras),
        min(w.y0 for w in palavras),
        max(w.x1 for w in palavras),
        max(w.y1 for w in palavras),
    )


def menor_conf(palavras: Sequence[Word]) -> float:
    """A confiança mais baixa entre as palavras — o "elo mais fraco" do grupo."""
    return min((w.conf for w in palavras), default=100.0)


def origem_de(palavras: Sequence[Word]) -> str:
    """"ocr" se qualquer palavra do grupo veio de OCR; senão "text"."""
    return "ocr" if any(w.src == "ocr" for w in palavras) else "text"
