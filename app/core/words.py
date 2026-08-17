"""Camada comum de palavras posicionadas.

Este é o ponto onde texto embutido e OCR deixam de ser coisas diferentes: os
dois produzem `Word`, e todo o resto do pipeline (linhas, colunas, extratores)
trabalha só com isso. É o que evita ter dois caminhos de código para PDF
nativo e PDF escaneado.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Literal

Origem = Literal["text", "ocr"]


@dataclass(slots=True)
class Word:
    """Uma palavra na página, com sua posição (x0,y0)-(x1,y1) em pontos de PDF."""

    text: str
    x0: float  # borda esquerda
    y0: float  # borda superior
    x1: float  # borda direita
    y1: float  # borda inferior
    #: 0-100. Texto embutido é 100 por definição; OCR traz a confiança do motor.
    conf: float = 100.0
    src: Origem = "text"

    @property
    def cx(self) -> float:
        """Centro horizontal — útil para comparar posição entre palavras."""
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        """Centro vertical."""
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 0.0)

    @property
    def width(self) -> float:
        return max(self.x1 - self.x0, 0.0)

    def bbox(self) -> tuple[float, float, float, float]:
        """Caixa delimitadora no formato (x0, y0, x1, y1)."""
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(slots=True)
class Line:
    """Uma linha visual da página: um conjunto de palavras na mesma altura."""

    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return min((w.x0 for w in self.words), default=0.0)

    @property
    def x1(self) -> float:
        return max((w.x1 for w in self.words), default=0.0)

    @property
    def y0(self) -> float:
        return min((w.y0 for w in self.words), default=0.0)

    @property
    def y1(self) -> float:
        return max((w.y1 for w in self.words), default=0.0)

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def min_conf(self) -> float:
        return min((w.conf for w in self.words), default=100.0)

    def words_between(self, x0: float, x1: float, min_overlap: float = 0.5) -> list[Word]:
        """Palavras cuja largura cai majoritariamente dentro de [x0, x1]."""
        out = []
        for w in self.words:
            inter = min(w.x1, x1) - max(w.x0, x0)  # tamanho da sobreposição horizontal
            if w.width <= 0:
                if x0 <= w.x0 <= x1:
                    out.append(w)
                continue
            if inter / w.width >= min_overlap:  # proporção da palavra que está dentro da faixa
                out.append(w)
        return out


@dataclass(slots=True)
class Page:
    """Uma página do PDF já processada: dimensões e todas as palavras encontradas."""

    number: int  # número da página, começando em 1
    width: float
    height: float
    words: list[Word]
    origem: Origem  # "text" (camada nativa do PDF) ou "ocr" (imagem reconhecida)
    #: Preenchido só quando a página passou por OCR, para diagnóstico.
    ocr_mean_conf: float | None = None


def group_lines(words: Sequence[Word], tolerance_ratio: float = 0.55) -> list[Line]:
    """Agrupa palavras em linhas visuais.

    A tolerância é derivada da altura mediana das palavras da própria página,
    não de um valor fixo em pontos: documentos escaneados em resoluções
    diferentes têm alturas diferentes, e um limiar cravado quebra em um deles.
    """
    usable = [w for w in words if w.text.strip()]  # ignora palavra vazia/só espaço
    if not usable:
        return []

    # A tolerância de altura é relativa (mediana da própria página), não um
    # número fixo — assim funciona em documentos com fonte grande ou pequena.
    heights = [w.height for w in usable if w.height > 0]
    tol = (median(heights) if heights else 8.0) * tolerance_ratio

    lines: list[Line] = []
    current: list[Word] = []  # palavras acumuladas da linha "atual"
    current_cy = 0.0

    # Varre as palavras de cima para baixo, da esquerda para a direita.
    for w in sorted(usable, key=lambda w: (w.cy, w.x0)):
        if current and abs(w.cy - current_cy) > tol:
            # Essa palavra já está "alta" demais para pertencer à linha atual:
            # fecha a linha corrente e começa uma nova.
            lines.append(Line(sorted(current, key=lambda x: x.x0)))
            current = []
        current.append(w)
        current_cy = sum(x.cy for x in current) / len(current)  # média móvel da altura da linha

    if current:  # não esquece a última linha, que não passa pelo "if" acima
        lines.append(Line(sorted(current, key=lambda x: x.x0)))

    return lines


# --------------------------------------------------------------------------
# Colunas a partir do cabeçalho
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Column:
    """Uma banda vertical da página, correspondendo a uma coluna da tabela."""

    key: str  # identificador interno (ex.: "entrada", "saida")
    label: str  # texto do cabeçalho, como impresso
    x0: float
    x1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(slots=True)
class ColumnMap:
    """Bandas horizontais das colunas, deduzidas da linha de cabeçalho.

    Localizar coluna pelo cabeçalho — e não por posição fixa — é o que permite
    o mesmo código ler layouts diferentes, inclusive páginas com larguras
    distintas dentro do mesmo PDF.
    """

    columns: list[Column]
    header_line: Line | None = None

    def get(self, key: str) -> Column | None:
        """Busca a coluna pelo identificador interno; None se não existir."""
        for c in self.columns:
            if c.key == key:
                return c
        return None

    def has(self, *keys: str) -> bool:
        """True se todas as chaves passadas existem entre as colunas encontradas."""
        present = {c.key for c in self.columns}
        return all(k in present for k in keys)

    def assign(self, word: Word) -> Column | None:
        """A qual coluna esta palavra pertence.

        Primeiro por sobreposição horizontal (funciona com colunas largas), e
        como desempate a menor distância entre centros (funciona com colunas
        numéricas alinhadas à direita, cuja palavra é mais estreita que a banda).
        """
        if not self.columns:
            return None

        # 1ª tentativa: a coluna com maior sobreposição horizontal com a palavra.
        best: Column | None = None
        best_overlap = 0.0
        for c in self.columns:
            inter = min(word.x1, c.x1) - max(word.x0, c.x0)
            if inter > best_overlap:
                best_overlap = inter
                best = c
        if best is not None and best_overlap > 0:
            return best

        # Não sobrepõe nenhuma banda (ex.: número estreito, coluna larga):
        # cai para a coluna cujo centro está mais perto do centro da palavra.
        return min(self.columns, key=lambda c: abs(c.cx - word.cx))

    def split(self, line: Line) -> dict[str, list[Word]]:
        """Distribui as palavras de uma linha entre as colunas."""
        buckets: dict[str, list[Word]] = {c.key: [] for c in self.columns}  # um balde por coluna
        for w in line.words:
            col = self.assign(w)
            if col is not None:
                buckets[col.key].append(w)
        for words_ in buckets.values():
            words_.sort(key=lambda w: w.x0)  # mantém a ordem da esquerda para a direita
        return buckets


def build_columns(
    header: Line,
    matches: list[tuple[str, Word]],
    page_width: float,
    left_pad: float = 4.0,
) -> ColumnMap:
    """Monta as bandas a partir das palavras de cabeçalho já identificadas.

    Cada banda vai do início da própria palavra de cabeçalho até o início da
    seguinte, com uma folga à esquerda — números alinhados à direita costumam
    estourar um pouco para trás do rótulo.
    """
    ordered = sorted(matches, key=lambda m: m[1].x0)  # da esquerda para a direita
    columns: list[Column] = []
    for i, (key, word) in enumerate(ordered):
        x0 = word.x0 - left_pad
        if i + 1 < len(ordered):
            # O fim desta coluna é o meio do caminho até o cabeçalho seguinte.
            nxt = ordered[i + 1][1]
            x1 = (word.x1 + nxt.x0) / 2 if nxt.x0 > word.x1 else nxt.x0
        else:
            x1 = page_width  # última coluna vai até a borda da página
        if i == 0:
            x0 = min(x0, 0.0) if word.x0 < 20 else x0  # primeira coluna encosta na margem
        columns.append(Column(key=key, label=word.text, x0=x0, x1=x1))

    # Garante bandas contíguas: o fim de uma é o começo da próxima.
    for i in range(len(columns) - 1):
        columns[i].x1 = max(columns[i].x1, columns[i + 1].x0)

    return ColumnMap(columns=columns, header_line=header)


def merge_adjacent(words: Iterable[Word], max_gap_ratio: float = 0.35) -> list[Word]:
    """Junta palavras coladas que o motor separou (ex.: "2.389" + ",77").

    O limiar é proporcional à altura do texto, de novo para não depender da
    resolução do documento.
    """
    ws = sorted(words, key=lambda w: w.x0)  # da esquerda para a direita
    if not ws:
        return []

    # Começa com a primeira palavra como "candidata a fundir com a próxima".
    out: list[Word] = [
        Word(ws[0].text, ws[0].x0, ws[0].y0, ws[0].x1, ws[0].y1, ws[0].conf, ws[0].src)
    ]
    for w in ws[1:]:
        prev = out[-1]
        gap = w.x0 - prev.x1  # distância horizontal entre a palavra anterior e esta
        ref = max(prev.height, w.height, 1.0)
        if gap <= ref * max_gap_ratio:
            # Espaço pequeno demais para ser separação real: junta na anterior.
            prev.text += w.text
            prev.x1 = max(prev.x1, w.x1)
            prev.y0 = min(prev.y0, w.y0)
            prev.y1 = max(prev.y1, w.y1)
            prev.conf = min(prev.conf, w.conf)  # a confiança da junção é a pior das duas
        else:
            out.append(Word(w.text, w.x0, w.y0, w.x1, w.y1, w.conf, w.src))
    return out
