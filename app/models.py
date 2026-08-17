"""Os formatos de saída, exatamente como o enunciado define.

Estes modelos são o contrato. Nada de campo extra: o que a aplicação precisa
saber além disso (coordenadas, origem OCR/texto) viaja em arquivos paralelos,
para que o JSON entregue seja literalmente o especificado.

Valores monetários e horários são `str` de propósito — guardamos o que estava
impresso. Converter "2.389,77" para float perde formato e introduz erro de
arredondamento.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Literal = só esses valores são aceitos (funciona como um "enum" de string).
TipoDocumento = Literal["cartao-ponto", "holerite"]
StatusTranscricao = Literal["processando", "concluido", "erro"]


class _Strict(BaseModel):
    """Rejeita campos desconhecidos — vale tanto na saída quanto no PUT de correção."""

    # extra="forbid": se o JSON tiver um campo que o modelo não conhece, dá erro
    # em vez de simplesmente ignorar. Evita erro de digitação silencioso.
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Cartão de ponto
# --------------------------------------------------------------------------


class Punch(_Strict):
    """Uma marcação de ponto: entrada ou saída, num horário."""

    kind: Literal["IN", "OUT"]
    time_raw: str  # exatamente como impresso no documento (ex.: "08:03")
    time_hhmm: str  # normalizado no formato HH:MM


class Day(_Strict):
    """Um dia do cartão de ponto, com todas as marcações daquele dia."""

    date_raw: str
    punches: list[Punch] = Field(default_factory=list)  # lista vazia por padrão


class CartaoPontoPage(_Strict):
    """Uma página do PDF, com os dias encontrados nela."""

    page: int  # número da página (começa em 1)
    days: list[Day] = Field(default_factory=list)


class CartaoPontoValue(_Strict):
    """O documento inteiro: todas as páginas do cartão de ponto."""

    pages: list[CartaoPontoPage] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Holerite
# --------------------------------------------------------------------------


class HoleriteField(_Strict):
    """Uma linha do holerite (ex.: código 001, "Salário Base", valor)."""

    code: str
    label: str
    reference: str
    value: str


class HoleriteBase(_Strict):
    """Uma linha de "base de cálculo" (ex.: Base INSS, Base FGTS)."""

    label: str
    value: str


class HoleritePage(_Strict):
    """Uma página do holerite: mês/ano de referência e as linhas encontradas."""

    page: int
    year: str
    month: str
    fields: list[HoleriteField] = Field(default_factory=list)
    bases: list[HoleriteBase] = Field(default_factory=list)


class HoleriteValue(_Strict):
    """O documento inteiro: todas as páginas do holerite."""

    pages: list[HoleritePage] = Field(default_factory=list)


# Mapa "tipo de documento" -> modelo Pydantic correspondente.
# Usado para validar o `value` de acordo com o tipo (cartao-ponto ou holerite).
VALUE_MODELS: dict[str, type[BaseModel]] = {
    "cartao-ponto": CartaoPontoValue,
    "holerite": HoleriteValue,
}


def parse_value(tipo: str, raw: dict) -> BaseModel:
    """Valida um `value` recebido (ex.: no PUT) contra o modelo do tipo."""
    model = VALUE_MODELS[tipo]
    return model.model_validate(raw)


# --------------------------------------------------------------------------
# Envelope da API
# --------------------------------------------------------------------------


class TranscricaoOut(BaseModel):
    """O que a API devolve quando o cliente consulta uma transcrição (GET)."""

    id: str
    tipo: TipoDocumento
    status: StatusTranscricao
    erro: str | None = None  # só preenchido quando status == "erro"
    value: dict | None = None  # só preenchido quando status == "concluido"


class TranscricaoCreated(BaseModel):
    """O que a API devolve ao criar uma transcrição (POST) — só o id do job."""

    id: str


class TranscricaoUpdateIn(BaseModel):
    """O corpo esperado ao corrigir manualmente uma transcrição (PUT)."""

    model_config = ConfigDict(extra="forbid")

    value: dict
