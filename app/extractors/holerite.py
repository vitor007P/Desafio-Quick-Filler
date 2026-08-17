"""Extrator de holerite.

A decisão central é a divisão entre `fields` e `bases`. Base INSS, Total
Vencimentos e Valor Líquido não são verbas: se entram em `fields`, viram
colunas na planilha e contaminam o documento inteiro.

Separamos por **região**, não por lista de nomes: tudo dentro da tabela de
verbas é `fields`, tudo na seção abaixo dela é `bases`. A lista de rótulos
serve só para achar onde a tabela termina — assim um layout que chame o total
de "Líquido a Receber" em vez de "Valor Líquido" continua caindo do lado certo.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.fields import RE_MOEDA, sanear_moeda, sanear_referencia
from app.core.uncertainty import (
    DESCONHECIDO,
    marcar_baixa_confianca,
    restringir_charset,
)
from app.core.words import (
    ColumnMap,
    Line,
    Page,
    Word,
    build_columns,
    group_lines,
    merge_adjacent,
)
from app.extractors.base import Extracao, Span, envelope, normalizar, origem_de

RE_CODIGO = re.compile(r"^[0-9?]{2,6}$")
RE_COMPETENCIA = re.compile(r"(?<![0-9/?])([0-9?]{1,2})\s*/\s*([0-9?]{4}|[0-9?]{2})(?![0-9/?])")

MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

TERMOS_COMPETENCIA = ("competenc", "mes/ano", "mes ano", "referente", "folha", "periodo", "mes de")

# Rótulos multi-palavra de propósito: "INSS" e "IRRF" sozinhos são verbas
# legítimas da tabela; "Base INSS" não é.
INICIO_BASES = (
    "base inss", "base de calculo", "base calculo", "base fgts", "base irrf", "base ir",
    "base i.r", "total vencimento", "total de vencimento", "total desconto",
    "total de desconto", "total proventos", "total bruto", "valor liquido",
    "liquido a receber", "liquido recebido", "fgts do mes", "fgts mes", "deposito fgts",
    "salario contribuicao", "sal. contribuicao", "salario base inss", "total geral",
)

CABECALHO_TERMOS = (
    "cod", "codigo", "rubrica", "descri", "discrimina", "historico", "lancament",
    "evento", "verba", "refer", "qtde", "quant", "vencim", "provent", "credito",
    "descont", "debito", "valor",
)


@dataclass(slots=True)
class _Layout:
    """O que aprendemos sobre a tabela de verbas numa página."""

    colunas: ColumnMap
    y_cabecalho: float


class ExtratorHolerite:
    """Lê um PDF de holerite e devolve as verbas (fields) e bases de cálculo (bases)."""

    tipo = "holerite"

    def extrair(self, paginas: Sequence[Page]) -> Extracao:
        pages_out: list[dict] = []
        spans: list[Span] = []
        notas: list[str] = []
        layout: _Layout | None = None  # reaproveitado em páginas sem cabeçalho próprio

        for pagina in paginas:
            linhas = group_lines(pagina.words)
            indice_pagina = len(pages_out)

            detectado = _detectar_layout(linhas, pagina.width)
            if detectado is not None:
                layout = detectado
            elif layout is None:
                notas.append(
                    f"página {pagina.number}: cabeçalho da tabela não encontrado, usando heurística"
                )

            ano, mes = _detectar_competencia(linhas)
            y_corte = detectado.y_cabecalho if detectado else -1.0
            fields, y_fim = _extrair_fields(
                linhas, layout if detectado else None, pagina, spans, indice_pagina, y_corte
            )
            bases = _extrair_bases(linhas, y_fim, pagina, spans, indice_pagina)

            pages_out.append(
                {
                    "page": pagina.number,
                    "year": ano,
                    "month": mes,
                    "fields": fields,
                    "bases": bases,
                }
            )

            if pagina.origem == "ocr":
                notas.append(f"página {pagina.number}: lida por OCR")
            if not fields and not bases:
                notas.append(f"página {pagina.number}: nenhum dado extraído")

        return Extracao(value={"pages": pages_out}, spans=spans, notas=notas)


# --------------------------------------------------------------------------
# Competência
# --------------------------------------------------------------------------


def _detectar_competencia(linhas: list[Line]) -> tuple[str, str]:
    """Acha mês e ano da folha.

    Preferimos a ocorrência ancorada num rótulo ("Competência: 01/2020") à
    primeira `MM/AAAA` da página: datas de admissão e de emissão também moram
    no topo do documento e enganariam a busca solta.
    """
    candidatos: list[tuple[int, str, str]] = []  # (prioridade, ano, mes) — menor prioridade vence

    for linha in linhas:
        texto = linha.text
        alvo = normalizar(texto)
        ancorada = any(t in alvo for t in TERMOS_COMPETENCIA)  # linha tem rótulo tipo "Competência"
        conf = linha.min_conf
        origem = "ocr" if any(w.src == "ocr" for w in linha.words) else "text"

        for m in RE_COMPETENCIA.finditer(texto):
            mes, ano = _normalizar_mes_ano(m.group(1), m.group(2), conf, origem)
            if mes:
                candidatos.append((0 if ancorada else 2, ano, mes))

        nome = _competencia_por_nome(alvo, texto, conf, origem)
        if nome:
            candidatos.append((0 if ancorada else 1, nome[0], nome[1]))

    if not candidatos:
        return "", ""

    # Prioridade 0 (ancorada num rótulo) vence sempre; entre iguais, a ordem
    # de leitura do documento decide (sort é estável).
    candidatos.sort(key=lambda c: c[0])
    _, ano, mes = candidatos[0]
    return ano, mes


def _normalizar_mes_ano(mes: str, ano: str, conf: float, origem: str) -> tuple[str, str]:
    """Valida e formata "MM" e "AAAA" extraídos de um "MM/AAAA" achado no texto."""
    mes = restringir_charset(marcar_baixa_confianca(mes, conf, origem), "0123456789?")
    ano = restringir_charset(marcar_baixa_confianca(ano, conf, origem), "0123456789?")

    if DESCONHECIDO not in mes and mes.isdigit():
        if not 1 <= int(mes) <= 12:
            return "", ""  # mês fora de faixa: não é competência de verdade, descarta
        mes = mes.rjust(2, "0")
    else:
        mes = mes.rjust(2, DESCONHECIDO)

    if len(ano) == 2:
        # Janela de dois dígitos: "70" pra cima é 19xx, abaixo disso é 20xx.
        ano = ("20" + ano) if DESCONHECIDO in ano or int(ano) < 70 else ("19" + ano)
    if DESCONHECIDO not in ano and ano.isdigit() and not 1970 <= int(ano) <= 2100:
        return "", ""

    return mes, ano


def _competencia_por_nome(alvo: str, texto: str, conf: float, origem: str) -> tuple[str, str] | None:
    """Layouts que escrevem "JANEIRO/2020" ou "Janeiro de 2020"."""
    for nome, numero in MESES_PT.items():
        if len(nome) <= 3:
            continue  # abreviações dão falso positivo dentro de outras palavras
        if nome not in alvo:
            continue
        m = re.search(r"(?<![0-9?])([0-9?]{4})(?![0-9?])", texto)
        if not m:
            continue
        ano = restringir_charset(marcar_baixa_confianca(m.group(1), conf, origem), "0123456789?")
        return ano, f"{numero:02d}"
    return None


# --------------------------------------------------------------------------
# Tabela de verbas
# --------------------------------------------------------------------------


def _classificar_cabecalho(texto: str) -> str | None:
    """Decide o "papel" de uma célula de cabeçalho: código, descrição, valor, etc."""
    alvo = normalizar(texto).strip(".: ")
    if not alvo:
        return None
    if alvo.startswith(("cod", "cód")) or "rubrica" in alvo:
        return "codigo"
    if any(t in alvo for t in ("descri", "discrimina", "historico", "lancament", "evento", "verba")):
        return "descricao"
    if any(t in alvo for t in ("refer", "qtde", "quant")):
        return "referencia"
    if any(t in alvo for t in ("vencim", "provent", "credito")):
        return "vencimentos"
    if any(t in alvo for t in ("descont", "debito")):
        return "descontos"
    if alvo.startswith("valor"):
        return "vencimentos"
    return None


def _detectar_layout(linhas: list[Line], largura: float) -> _Layout | None:
    melhor: tuple[int, Line, list[tuple[str, Word]]] | None = None

    for linha in linhas:
        celulas = merge_adjacent(linha.words, max_gap_ratio=1.0)
        marcados = [
            (papel, celula)
            for celula in celulas
            if (papel := _classificar_cabecalho(celula.text)) is not None
        ]
        papeis = {p for p, _ in marcados}
        # Sem descrição e sem uma coluna de valor não é o cabeçalho da tabela.
        if "descricao" not in papeis or not (papeis & {"vencimentos", "descontos"}):
            continue
        if len(marcados) < 2:
            continue

        pontuacao = len(marcados)
        if melhor is None or pontuacao > melhor[0]:
            melhor = (pontuacao, linha, marcados)

    if melhor is None:
        return None

    _, linha, marcados = melhor
    chaves = [(papel, palavra) for papel, palavra in marcados]
    # build_columns exige chaves únicas; sufixamos duplicatas mantendo a ordem.
    vistos: dict[str, int] = {}
    unicas: list[tuple[str, Word]] = []
    for papel, palavra in chaves:
        vistos[papel] = vistos.get(papel, 0) + 1
        sufixo = "" if vistos[papel] == 1 else f"#{vistos[papel]}"
        unicas.append((papel + sufixo, palavra))

    return _Layout(colunas=build_columns(linha, unicas, largura), y_cabecalho=linha.y1)


def _eh_inicio_bases(linha: Line) -> bool:
    """True se esta linha marca o fim da tabela de verbas (começo das bases/totais)."""
    alvo = normalizar(linha.text)
    if any(t in alvo for t in INICIO_BASES):
        return True
    # "Total ..." com dinheiro na linha também fecha a tabela.
    return alvo.startswith("total") and bool(RE_MOEDA.search(linha.text))


def _extrair_fields(
    linhas: list[Line],
    layout: _Layout | None,
    pagina: Page,
    spans: list[Span],
    indice_pagina: int,
    y_corte: float,
) -> tuple[list[dict], float]:
    """Devolve as verbas e o `y` onde a tabela terminou (início das bases)."""
    fields: list[dict] = []
    y_min = layout.y_cabecalho if layout else y_corte
    y_fim = float("inf")

    for linha in linhas:
        if linha.y0 < y_min:
            continue  # ainda está na área do cabeçalho
        if _eh_inicio_bases(linha):
            y_fim = linha.y0  # marca onde a tabela de verbas termina
            break
        if not RE_MOEDA.search(linha.text):
            continue  # linha sem nenhum valor monetário não é uma verba

        item = _ler_verba(linha, layout)
        if item is None:
            continue

        campo, palavras = item
        indice = len(fields)
        fields.append(campo)
        spans.append(
            Span(
                path=f"pages.{indice_pagina}.fields.{indice}",
                page=pagina.number,
                bbox=envelope(palavras),
                origem=origem_de(palavras),
            )
        )

    return fields, y_fim


def _ler_verba(linha: Line, layout: _Layout | None) -> tuple[dict, list[Word]] | None:
    """Monta uma verba (code/label/reference/value) a partir de uma linha da tabela."""
    if layout is not None:
        baldes = layout.colunas.split(linha)
        codigo_palavras = baldes.get("codigo", [])
        descricao_palavras = baldes.get("descricao", [])
        referencia_palavras = baldes.get("referencia", [])
        valor_palavras = [
            w
            for chave, ws in baldes.items()
            if chave.startswith(("vencimentos", "descontos"))
            for w in ws
        ]
    else:
        codigo_palavras, descricao_palavras, referencia_palavras, valor_palavras = _partir_sem_cabecalho(
            linha
        )

    valores = [w for w in valor_palavras if RE_MOEDA.fullmatch(w.text.strip())]
    if not valores:
        return None  # sem valor monetário na linha, não é uma verba válida
    # Com colunas de vencimento e desconto lado a lado, só uma tem valor na
    # linha; se as duas tiverem, a mais à direita é a que o layout preencheu.
    palavra_valor = valores[-1]

    codigo = ""
    codigo_usadas: list[Word] = []
    for w in codigo_palavras:
        if RE_CODIGO.fullmatch(w.text.strip()):
            codigo = restringir_charset(
                marcar_baixa_confianca(w.text.strip(), w.conf, w.src), "0123456789?"
            )
            codigo_usadas = [w]
            break

    rotulo_palavras = [w for w in descricao_palavras if w not in codigo_usadas]
    label = _limpar_label(" ".join(w.text for w in rotulo_palavras))
    if not label:
        return None  # sem descrição, não dá pra saber que verba é essa

    referencia = ""
    for w in referencia_palavras:
        texto = w.text.strip()
        if re.fullmatch(r"[0-9?]+(?:[,.][0-9?]+)?%?", texto):
            referencia = sanear_referencia(texto, w.conf, w.src)
            break

    campo = {
        "code": codigo,
        "label": label,
        "reference": referencia,
        "value": sanear_moeda(palavra_valor.text, palavra_valor.conf, palavra_valor.src),
    }
    usadas = codigo_usadas + rotulo_palavras + [palavra_valor]
    return campo, usadas


def _partir_sem_cabecalho(linha: Line) -> tuple[list[Word], list[Word], list[Word], list[Word]]:
    """Fallback quando a tabela não tem cabeçalho reconhecível.

    Estrutura assumida: [código] descrição [referência] ... valor no fim. É a
    forma dominante nos holerites brasileiros, mas continua sendo palpite —
    por isso só entra quando o cabeçalho falhou.
    """
    palavras = linha.words
    codigo: list[Word] = []
    inicio = 0
    if palavras and RE_CODIGO.fullmatch(palavras[0].text.strip()):
        codigo = [palavras[0]]
        inicio = 1

    monetarias = [w for w in palavras if RE_MOEDA.fullmatch(w.text.strip())]
    if not monetarias:
        return codigo, palavras[inicio:], [], []

    valor = monetarias[-1]
    corpo = [w for w in palavras[inicio:] if w.x1 <= valor.x0]

    descricao = [w for w in corpo if not re.fullmatch(r"[0-9?.,%]+", w.text.strip())]
    referencia = [w for w in corpo if re.fullmatch(r"[0-9?]+(?:[,.][0-9?]+)?%?", w.text.strip())]
    return codigo, descricao, referencia, [valor]


def _limpar_label(texto: str) -> str:
    """Colapsa espaços repetidos e tira pontuação solta nas bordas do rótulo."""
    limpo = re.sub(r"\s+", " ", texto or "").strip(" .:-\t")
    return limpo


# --------------------------------------------------------------------------
# Bases e totais
# --------------------------------------------------------------------------


def _extrair_bases(
    linhas: list[Line],
    y_inicio: float,
    pagina: Page,
    spans: list[Span],
    indice_pagina: int,
) -> list[dict]:
    if y_inicio == float("inf"):
        # A tabela não terminou com um rótulo conhecido: varremos a página
        # inteira atrás de pares rótulo/valor de base, sem faixa de corte.
        candidatas = [l for l in linhas if _eh_inicio_bases(l) or _so_valores(l)]
        y_inicio = min((l.y0 for l in candidatas), default=float("inf"))
        if y_inicio == float("inf"):
            return []  # não achou nenhuma base na página

    regiao = [l for l in linhas if l.y0 >= y_inicio - 1]
    bases: list[dict] = []

    i = 0
    while i < len(regiao):
        linha = regiao[i]
        pares = _pares_na_linha(linha)

        if not pares and i + 1 < len(regiao) and _so_valores(regiao[i + 1]):
            # Layout de duas faixas: rótulos numa linha, valores na de baixo,
            # alinhados por coluna.
            pares = _parear_por_coluna(linha, regiao[i + 1])
            if pares:
                i += 1

        for rotulo, palavra_valor, palavras in pares:
            indice = len(bases)
            bases.append(
                {
                    "label": rotulo,
                    "value": sanear_moeda(
                        palavra_valor.text, palavra_valor.conf, palavra_valor.src
                    ),
                }
            )
            spans.append(
                Span(
                    path=f"pages.{indice_pagina}.bases.{indice}",
                    page=pagina.number,
                    bbox=envelope(palavras),
                    origem=origem_de(palavras),
                )
            )
        i += 1

    return bases


def _so_valores(linha: Line) -> bool:
    """True se a linha tem só valores monetários (nenhum texto/rótulo junto)."""
    palavras = [w for w in linha.words if w.text.strip()]
    if not palavras:
        return False
    return all(RE_MOEDA.fullmatch(w.text.strip()) for w in palavras)


def _pares_na_linha(linha: Line) -> list[tuple[str, Word, list[Word]]]:
    """Extrai pares rótulo/valor de uma linha, aceitando vários por linha.

    Cada valor monetário fecha o rótulo formado pelas palavras à sua esquerda
    desde o valor anterior — é assim que "Total Vencimentos 2.545,68  Total
    Descontos 262,87" sai como duas bases e não como uma.
    """
    pares: list[tuple[str, Word, list[Word]]] = []
    buffer: list[Word] = []

    for palavra in linha.words:
        if RE_MOEDA.fullmatch(palavra.text.strip()):
            rotulo = _limpar_label(" ".join(w.text for w in buffer))
            if rotulo and not rotulo.replace(" ", "").isdigit():
                pares.append((rotulo, palavra, buffer + [palavra]))
            buffer = []
        else:
            buffer.append(palavra)

    return pares


def _parear_por_coluna(
    linha_rotulos: Line, linha_valores: Line
) -> list[tuple[str, Word, list[Word]]]:
    """Casa rótulos e valores de faixas empilhadas pela proximidade horizontal."""
    celulas = merge_adjacent(linha_rotulos.words, max_gap_ratio=1.0)
    celulas = [c for c in celulas if not RE_MOEDA.fullmatch(c.text.strip())]
    if not celulas:
        return []

    pares: list[tuple[str, Word, list[Word]]] = []
    for valor in linha_valores.words:
        if not RE_MOEDA.fullmatch(valor.text.strip()):
            continue
        celula = min(celulas, key=lambda c: abs(c.cx - valor.cx))
        # Um valor longe de qualquer rótulo não é par de ninguém.
        if abs(celula.cx - valor.cx) > max(celula.width, valor.width) * 1.5:
            continue
        rotulo = _limpar_label(celula.text)
        if rotulo:
            pares.append((rotulo, valor, [celula, valor]))

    return pares


__all__ = ["ExtratorHolerite"]
