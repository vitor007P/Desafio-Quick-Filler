"""Extrator de cartão de ponto.

O documento é uma lista de dias; cada linha tem uma data e as batidas do
funcionário. O trabalho real é separar as batidas das colunas de totalização
(normais, extras, faltas), que também são `HH:MM` e moram na mesma linha.

Fazemos isso localizando as colunas pelo cabeçalho, com uma checagem de ordem
como rede de segurança quando o cabeçalho não aparece. Nada de posição fixa: um
`x` cravado quebra no primeiro layout diferente — e às vezes entre páginas do
mesmo PDF.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.fields import RE_DATA, RE_HORA, ler_data, sanear_data, sanear_hora
from app.core.words import (
    ColumnMap,
    Line,
    Page,
    Word,
    build_columns,
    group_lines,
    merge_adjacent,
)
from app.extractors.base import (
    Extracao,
    Span,
    envelope,
    normalizar,
    origem_de,
)

# Rótulos que indicam a coluna da data.
TERMOS_DATA = ("data", "dia", "dt")

# Rótulos das colunas de marcação. A ordem importa: os mais específicos
# primeiro, para "entrada" não ser engolido por "ent".
TERMOS_ENTRADA = ("entrada", "ent.", "ent ", "e1", "e2", "e3", "e4")
TERMOS_SAIDA = ("saida", "sai.", "sai ", "s1", "s2", "s3", "s4")
TERMOS_BATIDA_GENERICA = (
    "marcac", "batida", "registro", "apontament", "horario", "ponto", "movimentac"
)

# Colunas de totalização: tudo à direita da primeira delas é resultado
# calculado pelo sistema de ponto, não batida do funcionário.
TERMOS_RESUMO = (
    "normai", "extra", "falta", "adicional", "noturn", "total", "atraso", "abono",
    "dsr", "saldo", "interval", "carga", "jornada", "previst", "trabalhad",
    "credito", "debito", "banco", "observ", "ocorrenc", "justific", "apurad",
    "resultado", "diurn", "he50", "he100", "compensa", "acumulad", "devid",
)

# Linhas de contexto do documento que contêm data mas não são dia de trabalho.
TERMOS_NAO_DIA = (
    "periodo", "período", "referenc", "admiss", "demiss", "emiss", "competenc",
    "nasciment", "cnpj", "cpf", "matricula", "empresa", "funcionario", "cargo",
    "departament", "assinatura", "pagina", "folha", "resumo", "totais", "empregad",
)

DIAS_SEMANA = {
    "seg", "ter", "qua", "qui", "sex", "sab", "dom",
    "segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo",
    "2a", "3a", "4a", "5a", "6a",
}

RE_SO_DIA = re.compile(r"^[0-9?]{1,2}$")


@dataclass(slots=True)
class _ColunaBatida:
    """Uma coluna de horário (entrada, saída ou genérica) já localizada na página."""

    key: str
    kind: str | None  # "IN", "OUT" ou None quando o rótulo é genérico
    x0: float
    x1: float


@dataclass(slots=True)
class _Layout:
    """O que aprendemos sobre a tabela numa página."""

    colunas: ColumnMap | None
    coluna_data: tuple[float, float] | None
    batidas: list[_ColunaBatida]
    limite_batidas: float  # a partir daqui na horizontal, é coluna de totalização
    y_cabecalho: float  # altura da linha de cabeçalho, pra não confundi-la com um dia


class ExtratorCartaoPonto:
    """Lê um PDF de cartão de ponto e devolve os dias/batidas encontrados."""

    tipo = "cartao-ponto"

    def extrair(self, paginas: Sequence[Page]) -> Extracao:
        pages_out: list[dict] = []
        spans: list[Span] = []
        notas: list[str] = []
        layout: _Layout | None = None  # aprendido na 1ª página, reaproveitado nas seguintes

        for pagina in paginas:
            linhas = group_lines(pagina.words)  # agrupa palavras soltas em linhas visuais
            novo_layout = _detectar_layout(linhas, pagina.width)
            if novo_layout is not None:
                layout = novo_layout
            elif layout is not None:
                # Páginas seguintes de um mesmo documento repetem a tabela sem
                # repetir o cabeçalho. Reaproveitamos o que já aprendemos.
                layout = _Layout(
                    colunas=layout.colunas,
                    coluna_data=layout.coluna_data,
                    batidas=layout.batidas,
                    limite_batidas=layout.limite_batidas,
                    y_cabecalho=-1.0,
                )
            else:
                notas.append(f"página {pagina.number}: cabeçalho não encontrado, usando heurística")

            dias = _extrair_dias(linhas, layout, pagina, spans, len(pages_out))
            pages_out.append({"page": pagina.number, "days": dias})

            if pagina.origem == "ocr":
                notas.append(f"página {pagina.number}: lida por OCR")

        return Extracao(value={"pages": pages_out}, spans=spans, notas=notas)


# --------------------------------------------------------------------------
# Cabeçalho e colunas
# --------------------------------------------------------------------------


def _classificar_cabecalho(texto: str) -> str | None:
    """Decide o "papel" de uma célula de cabeçalho: data, entrada, saída, etc."""
    alvo = normalizar(texto)
    if not alvo:
        return None
    if any(t in alvo for t in TERMOS_RESUMO):
        return "resumo"
    if any(t in alvo for t in TERMOS_ENTRADA):
        return "entrada"
    if any(t in alvo for t in TERMOS_SAIDA):
        return "saida"
    if any(t in alvo for t in TERMOS_BATIDA_GENERICA):
        return "batida"
    if any(alvo == t or alvo.startswith(t) for t in TERMOS_DATA):
        return "data"
    return None


def _detectar_layout(linhas: list[Line], largura: float) -> _Layout | None:
    """Acha a linha de cabeçalho da tabela e traduz em bandas horizontais."""
    melhor: tuple[int, Line, list[tuple[str, Word]]] | None = None  # (pontuação, linha, células reconhecidas)

    for linha in linhas:
        celulas = merge_adjacent(linha.words, max_gap_ratio=1.0)
        marcados = [
            (papel, celula)
            for celula in celulas
            if (papel := _classificar_cabecalho(celula.text)) is not None
        ]
        papeis = {p for p, _ in marcados}
        if not papeis:
            continue  # linha sem nenhuma célula parecida com cabeçalho: ignora

        # Um cabeçalho de verdade tem ao menos duas colunas reconhecíveis, e
        # pelo menos uma delas é de marcação — senão é linha de resumo.
        tem_batida = bool(papeis & {"entrada", "saida", "batida"})
        if len(marcados) < 2 or not tem_batida:
            continue

        # Mais células reconhecidas = cabeçalho mais provável; ter a coluna de
        # data conta pontos extras, porque é o sinal mais forte de que é ela.
        pontuacao = len(marcados) + (2 if "data" in papeis else 0)
        if melhor is None or pontuacao > melhor[0]:
            melhor = (pontuacao, linha, marcados)

    if melhor is None:
        return None  # nenhuma linha parece um cabeçalho de tabela

    _, linha, marcados = melhor
    chaves = [(f"{papel}_{i}", palavra) for i, (papel, palavra) in enumerate(marcados)]
    colmap = build_columns(linha, chaves, largura)

    coluna_data: tuple[float, float] | None = None
    batidas: list[_ColunaBatida] = []
    x_resumo = largura

    for (chave, _), coluna in zip(chaves, colmap.columns):
        papel = chave.rsplit("_", 1)[0]  # desfaz o sufixo "_0", "_1"... usado como id único
        if papel == "data" and coluna_data is None:
            coluna_data = (coluna.x0, coluna.x1)
        elif papel in {"entrada", "saida", "batida"}:
            kind = {"entrada": "IN", "saida": "OUT"}.get(papel)  # None para coluna genérica
            batidas.append(_ColunaBatida(chave, kind, coluna.x0, coluna.x1))
        elif papel == "resumo":
            x_resumo = min(x_resumo, coluna.x0)  # guarda a mais à esquerda das colunas de resumo

    # A fronteira é a primeira coluna de totalização que fica depois das
    # marcações; colunas de resumo à esquerda (raras) não cortam nada.
    if batidas:
        x_ultima_batida = max(b.x1 for b in batidas)
        limite = x_resumo if x_resumo > min(b.x0 for b in batidas) else largura
        limite = max(limite, x_ultima_batida)
    else:
        limite = x_resumo

    return _Layout(
        colunas=colmap,
        coluna_data=coluna_data,
        batidas=batidas,
        limite_batidas=limite,
        y_cabecalho=linha.y1,
    )


# --------------------------------------------------------------------------
# Linhas de dia
# --------------------------------------------------------------------------


def _eh_linha_de_contexto(linha: Line) -> bool:
    """True se a linha é texto de cabeçalho/rodapé do documento, não um dia."""
    alvo = normalizar(linha.text)
    if any(t in alvo for t in TERMOS_NAO_DIA):
        return True
    # "01/05/2019 a 31/05/2019": duas datas e nenhuma batida é período, não dia.
    return len(RE_DATA.findall(linha.text)) >= 2 and not RE_HORA.search(linha.text)


def _achar_data(linha: Line, layout: _Layout | None) -> tuple[str, list[Word]] | None:
    """Localiza a data da linha, preferindo a banda da coluna de data."""
    candidatas: list[Word] = []
    if layout is not None and layout.coluna_data is not None:
        candidatas = linha.words_between(*layout.coluna_data)
    if not candidatas:
        # Sem coluna conhecida, a data mora no começo da linha — não no meio.
        candidatas = linha.words[:3]

    for palavra in candidatas:
        if RE_DATA.search(palavra.text):
            return sanear_data(palavra.text, palavra.conf, palavra.src), [palavra]

    # Layout "01 QUA": o número do dia sozinho, seguido do dia da semana.
    if len(candidatas) >= 1 and RE_SO_DIA.fullmatch(candidatas[0].text.strip()):
        vizinhos = linha.words[1:3]
        if any(normalizar(v.text).rstrip(".") in DIAS_SEMANA for v in vizinhos):
            palavra = candidatas[0]
            return sanear_data(palavra.text, palavra.conf, palavra.src), [palavra]

    return None  # nenhuma data reconhecível nesta linha


def _coletar_batidas(
    linha: Line, layout: _Layout | None, excluir: set[int]
) -> list[tuple[Word, str | None]]:
    """Horários da linha que são batida, com o `kind` da coluna quando existir."""
    limite = layout.limite_batidas if layout else float("inf")
    resultado: list[tuple[Word, str | None]] = []

    for palavra in linha.words:
        if id(palavra) in excluir:
            continue  # já foi usada como a data desta linha
        if not RE_HORA.fullmatch(palavra.text.strip()):
            continue  # não parece um horário
        if palavra.x0 >= limite:
            continue  # já é coluna de totalização

        kind = None
        if layout and layout.batidas:
            for coluna in layout.batidas:
                if coluna.x0 <= palavra.cx <= coluna.x1:
                    kind = coluna.kind
                    break
        resultado.append((palavra, kind))

    resultado.sort(key=lambda item: item[0].x0)
    return resultado


def _cortar_por_ordem(batidas: list[tuple[Word, str | None]]) -> list[tuple[Word, str | None]]:
    """Rede de segurança para quando não há cabeçalho.

    Batidas de um dia crescem ao longo da linha; uma coluna de totalização
    quebra essa ordem (18:00 seguido de 09:00 é jornada trabalhada, não saída).
    Cortamos na primeira quebra. O preço é o plantão que vira o dia — 22:00
    seguido de 02:00 —, que perde as batidas depois da virada; por isso isto é
    fallback, e não a regra principal.
    """
    if len(batidas) < 2:
        return batidas  # nada para comparar

    saida = [batidas[0]]
    for palavra, kind in batidas[1:]:
        anterior = saida[-1][0].text
        if _minutos(palavra.text) < _minutos(anterior):
            break  # horário voltou no tempo: aqui começa a coluna de totalização
        saida.append((palavra, kind))
    return saida


def _minutos(hhmm: str) -> int:
    """Converte "HH:MM" em minutos desde meia-noite, para dar pra comparar."""
    hora, _, minuto = hhmm.strip().replace(".", ":").partition(":")
    try:
        return int(hora) * 60 + int(minuto)
    except ValueError:
        return -1  # com "?" não dá para comparar; não corta nada


def _extrair_dias(
    linhas: list[Line],
    layout: _Layout | None,
    pagina: Page,
    spans: list[Span],
    indice_pagina: int,
) -> list[dict]:
    dias: list[dict] = []
    y_min = layout.y_cabecalho if layout else -1.0  # ignora tudo acima do cabeçalho

    for linha in linhas:
        if linha.y0 < y_min:
            continue  # está na área do cabeçalho, não é linha de dado
        if _eh_linha_de_contexto(linha):
            continue

        achado = _achar_data(linha, layout)
        batidas_brutas = _coletar_batidas(
            linha, layout, excluir={id(w) for w in (achado[1] if achado else [])}
        )
        if layout is None or not layout.batidas:
            batidas_brutas = _cortar_por_ordem(batidas_brutas)

        if achado is None:
            # Sem data: só aproveitamos como continuação do dia anterior, e
            # apenas quando a linha não traz mais nada além de horários.
            if dias and batidas_brutas and len(batidas_brutas) == len(linha.words):
                _anexar_batidas(
                    dias[-1], batidas_brutas, pagina, spans, indice_pagina, len(dias) - 1
                )
            continue

        date_raw, palavras_data = achado
        dia = {"date_raw": date_raw, "punches": []}
        dias.append(dia)
        indice_dia = len(dias) - 1

        spans.append(
            Span(
                path=f"pages.{indice_pagina}.days.{indice_dia}.date_raw",
                page=pagina.number,
                bbox=envelope(palavras_data),
                origem=origem_de(palavras_data),
            )
        )
        _anexar_batidas(dia, batidas_brutas, pagina, spans, indice_pagina, indice_dia)

    return dias


def _anexar_batidas(
    dia: dict,
    batidas: list[tuple[Word, str | None]],
    pagina: Page,
    spans: list[Span],
    indice_pagina: int,
    indice_dia: int,
) -> None:
    for palavra, kind_coluna in batidas:
        indice = len(dia["punches"])
        # O cabeçalho manda quando existe: numa linha em que falta a primeira
        # entrada, alternar cegamente rotularia todas as batidas trocadas.
        kind = kind_coluna or ("IN" if indice % 2 == 0 else "OUT")
        time_raw, time_hhmm = sanear_hora(palavra.text, palavra.conf, palavra.src)
        dia["punches"].append({"kind": kind, "time_raw": time_raw, "time_hhmm": time_hhmm})
        spans.append(
            Span(
                path=f"pages.{indice_pagina}.days.{indice_dia}.punches.{indice}",
                page=pagina.number,
                bbox=palavra.bbox(),
                origem=palavra.src,
            )
        )


def datas_do_documento(value: dict) -> list[str]:
    """Atalho usado pelos avisos derivados e pelos testes."""
    return [d["date_raw"] for p in value.get("pages", []) for d in p.get("days", [])]


__all__ = ["ExtratorCartaoPonto", "datas_do_documento", "ler_data"]
