"""A grade: representação tabular única, usada pela interface e pela planilha.

Os avisos são calculados aqui, a partir do próprio dado, toda vez que a grade é
montada — não existem como campo no JSON. É o que o enunciado pede: um holerite
com 12 competências em ordem e um 13 no meio não precisa de flag, precisa de
alguém que compare com as vizinhas.

Ter uma grade só é também o que faz a tabela da tela e o arquivo baixado nunca
divergirem: são a mesma função com dois renderizadores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise  # anda em pares: (item0,item1), (item1,item2)...
from math import ceil

from app.core.fields import ler_data
from app.core.uncertainty import DESCONHECIDO

# Cores usadas para pintar as células da planilha (Excel usa hexadecimal sem "#").
AMARELO = "FFF3CD"
VERMELHO = "F8D7DA"
BORDA_VERMELHA = "DC3545"
CABECALHO_FUNDO = "173772"

# Códigos dos quatro avisos do enunciado, mais o de incerteza de leitura.
# A chave é usada internamente; o valor é o texto mostrado ao usuário.
AVISOS = {
    "batidas_impares": "Número ímpar de batidas: falta uma entrada ou uma saída",
    "data_nao_sequencial": "A data quebra a sequência do documento",
    "pagina_vazia": "A página existe no PDF mas nenhum dado saiu dela",
    "mes_nao_sequencial": "A competência não é o mês seguinte à página anterior",
    "incerteza": "Há caractere que não foi possível ler (?)",
}

# Cada aviso tem uma cor de destaque. Vermelho é mais grave que amarelo.
SEVERIDADE = {
    "batidas_impares": "amarelo",
    "pagina_vazia": "amarelo",
    "incerteza": "amarelo",
    "data_nao_sequencial": "vermelho",
    "mes_nao_sequencial": "vermelho",
}


@dataclass(slots=True)  # slots=True: economiza memória, não permite atributo novo
class Linha:
    """Uma linha da grade (uma linha da tabela na tela e na planilha)."""

    celulas: list[str]
    avisos: list[str] = field(default_factory=list)
    #: De onde a linha veio no JSON — a interface usa para editar o campo certo.
    ref: dict = field(default_factory=dict)

    @property
    def severidade(self) -> str:
        """Vermelho ganha de amarelo quando os dois valem para a mesma linha."""
        if any(SEVERIDADE.get(a) == "vermelho" for a in self.avisos):
            return "vermelho"
        return "amarelo" if self.avisos else "nenhum"

    @property
    def mensagens(self) -> list[str]:
        # Troca cada código de aviso pelo texto legível correspondente.
        return [AVISOS[a] for a in self.avisos if a in AVISOS]


@dataclass(slots=True)
class Grade:
    """A tabela inteira: nomes das colunas + todas as linhas."""

    colunas: list[str]
    linhas: list[Linha]

    def to_dict(self) -> dict:
        """Formato simples (dict/list), pronto para virar JSON na resposta da API."""
        return {
            "colunas": self.colunas,
            "linhas": [
                {
                    "celulas": linha.celulas,
                    "avisos": linha.avisos,
                    "mensagens": linha.mensagens,
                    "severidade": linha.severidade,
                    "ref": linha.ref,
                }
                for linha in self.linhas
            ],
        }


def montar_grade(tipo: str, value: dict) -> Grade:
    """Ponto de entrada: escolhe o montador certo conforme o tipo de documento."""
    if tipo == "cartao-ponto":
        return _grade_cartao(value)
    if tipo == "holerite":
        return _grade_holerite(value)
    raise ValueError(f"tipo desconhecido: {tipo}")


def _tem_incerteza(celulas: list[str]) -> bool:
    """True se alguma célula contém o marcador de caractere não reconhecido."""
    return any(DESCONHECIDO in (c or "") for c in celulas)


# --------------------------------------------------------------------------
# Cartão de ponto
# --------------------------------------------------------------------------


def _grade_cartao(value: dict) -> Grade:
    """Monta a grade do cartão de ponto: uma linha por dia, colunas de entrada/saída."""
    # Achata "páginas -> dias" numa lista só, guardando os índices originais
    # (ip = índice da página, idia = índice do dia dentro da página) para
    # conseguir apontar de volta pro JSON depois (campo `ref`).
    dias: list[tuple[int, int, dict]] = [
        (ip, idia, dia)
        for ip, pagina in enumerate(value.get("pages", []))
        for idia, dia in enumerate(pagina.get("days", []))
    ]

    # Quantos pares de coluna "Entrada/Saída" a tabela precisa: o suficiente
    # para o dia com mais batidas (mínimo de 1 par, mesmo sem nenhum dado).
    max_batidas = max((len(d["punches"]) for _, _, d in dias), default=0)
    pares = max(ceil(max_batidas / 2), 1)

    colunas = ["Data"]
    for i in range(1, pares + 1):
        colunas += [f"Entrada {i}", f"Saída {i}"]

    quebras = _datas_nao_sequenciais([d.get("date_raw", "") for _, _, d in dias])

    linhas: list[Linha] = []
    for posicao, (ip, idia, dia) in enumerate(dias):
        batidas = dia.get("punches", [])
        celulas = [dia.get("date_raw", "")]
        # Preenche cada coluna de horário; se o dia tiver menos batidas que o
        # máximo, as colunas sobrando ficam em branco.
        for i in range(pares * 2):
            celulas.append(batidas[i]["time_hhmm"] if i < len(batidas) else "")

        avisos: list[str] = []
        if len(batidas) % 2 == 1:  # número ímpar de batidas = falta uma marcação
            avisos.append("batidas_impares")
        if posicao in quebras:
            avisos.append("data_nao_sequencial")
        if _tem_incerteza(celulas):
            avisos.append("incerteza")

        linhas.append(
            Linha(celulas=celulas, avisos=avisos, ref={"page_index": ip, "day_index": idia})
        )

    return Grade(colunas=colunas, linhas=linhas)


def _datas_nao_sequenciais(datas: list[str]) -> set[int]:
    """Quais posições quebram a sequência de datas do documento.

    A regra é literal ao enunciado: "uma linha por dia do período", então cada
    linha deve avançar exatamente um dia sobre a anterior — o mesmo número de
    dias de calendário que de linhas entre elas (datas ilegíveis são puladas
    na comparação, não contam como linha). Um cartão que lista só dias
    trabalhados também vai acender o aviso nas segundas-feiras: isso é
    informação real ("este documento pula dias"), não ruído — cabe ao revisor
    decidir se é esperado.
    """
    lidas = [ler_data(d) for d in datas]  # interpreta cada texto de data

    # Descobre o ano/mês "de contexto": o que mais se repete entre as datas
    # legíveis, usado para completar datas que vieram sem ano ou sem mês.
    anos = [lida.ano for lida in lidas if lida.ano]
    meses = [lida.mes for lida in lidas if lida.mes]
    ano_ctx = max(set(anos), key=anos.count) if anos else None
    mes_ctx = max(set(meses), key=meses.count) if meses else None

    resolvidas: list[tuple[int, date]] = []  # (posição na lista original, data completa)
    ano_corrente, mes_corrente = ano_ctx, mes_ctx
    ultimo_dia = 0

    for i, lida in enumerate(lidas):
        if lida.dia is None:
            continue  # sem dia legível, não dá pra comparar essa linha
        mes = lida.mes if lida.mes is not None else mes_corrente
        ano = lida.ano if lida.ano is not None else ano_corrente
        if mes is None or ano is None:
            continue
        # Dia que retrocede num documento sem mês impresso indica virada de mês.
        if lida.mes is None and lida.dia < ultimo_dia:
            mes += 1
            if mes > 12:
                mes, ano = 1, ano + 1
            mes_corrente, ano_corrente = mes, ano
        try:
            resolvidas.append((i, date(ano, mes, lida.dia)))
        except ValueError:
            continue  # data impossível (ex.: dia 31 em mês de 30) — ignora
        ultimo_dia = lida.dia

    if len(resolvidas) < 2:
        return set()  # não dá pra comparar sequência com menos de 2 datas

    quebras: set[int] = set()
    # Compara cada data com a anterior: a diferença em dias de calendário tem
    # que ser igual à diferença de posição na lista (uma linha = um dia).
    for (a_i, a_d), (b_i, b_d) in pairwise(resolvidas):
        delta_dias = (b_d - a_d).days
        delta_linhas = b_i - a_i
        if delta_dias != delta_linhas:
            quebras.add(b_i)

    return quebras


# --------------------------------------------------------------------------
# Holerite
# --------------------------------------------------------------------------


def _grade_holerite(value: dict) -> Grade:
    """Monta a grade do holerite: uma linha por página, uma coluna por verba."""
    paginas = value.get("pages", [])

    # Coleta todos os rótulos de verba (ex.: "Salário Base", "INSS") que
    # aparecem em qualquer página, na ordem em que aparecem, sem repetir.
    rotulos: list[str] = []
    for pagina in paginas:
        for campo in pagina.get("fields", []):
            rotulo = campo.get("label", "")
            if rotulo and rotulo not in rotulos:
                rotulos.append(rotulo)

    colunas = ["Pág.", "Mês", "Ano"] + rotulos
    quebras = _meses_nao_sequenciais(paginas)

    linhas: list[Linha] = []
    for ip, pagina in enumerate(paginas):
        # Agrupa os valores de cada página por rótulo de verba.
        por_rotulo: dict[str, list[str]] = {}
        for campo in pagina.get("fields", []):
            por_rotulo.setdefault(campo.get("label", ""), []).append(campo.get("value", ""))

        celulas = [str(pagina.get("page", ip + 1)), pagina.get("month", ""), pagina.get("year", "")]
        for rotulo in rotulos:
            # A mesma verba aparecendo duas vezes na página é dado real, e some
            # se a célula guardar só uma. Mostramos as duas em vez de escolher.
            celulas.append(" / ".join(por_rotulo.get(rotulo, [])))

        avisos: list[str] = []
        if not pagina.get("fields") and not pagina.get("bases"):
            avisos.append("pagina_vazia")
        if ip in quebras:
            avisos.append("mes_nao_sequencial")
        if _tem_incerteza(celulas):
            avisos.append("incerteza")

        linhas.append(Linha(celulas=celulas, avisos=avisos, ref={"page_index": ip}))

    return Grade(colunas=colunas, linhas=linhas)


def _meses_nao_sequenciais(paginas: list[dict]) -> set[int]:
    """Competências legíveis devem avançar exatamente um mês entre si.

    Páginas ilegíveis não quebram a cadeia: são puladas, e a comparação segue
    entre as próximas legíveis. Dezembro → janeiro é consecutivo.
    """
    legiveis: list[tuple[int, int]] = []  # (indice_pagina, ordinal_mes)
    for i, pagina in enumerate(paginas):
        mes, ano = pagina.get("month", ""), pagina.get("year", "")
        if not mes or not ano or DESCONHECIDO in mes or DESCONHECIDO in ano:
            continue
        if not mes.isdigit() or not ano.isdigit():
            continue
        # Ordinal do mês: ano*12 + mes cresce de 1 em 1 a cada mês que passa,
        # inclusive na virada de dezembro pra janeiro do ano seguinte.
        legiveis.append((i, int(ano) * 12 + int(mes)))

    quebras: set[int] = set()
    for (_, anterior), (indice, atual) in pairwise(legiveis):
        if atual - anterior != 1:
            quebras.add(indice)
    return quebras


__all__ = ["AMARELO", "BORDA_VERMELHA", "CABECALHO_FUNDO", "VERMELHO", "Grade", "Linha", "montar_grade"]
