"""Reconhecimento e saneamento dos campos que os dois documentos têm em comum:
datas, horários, dinheiro e referências.

Todo campo aqui segue o mesmo par do enunciado: guardamos o que estava impresso
(`_raw`) e, quando faz sentido, o que interpretamos (`_hhmm`). Quando os dois
divergem, dá para auditar.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

from app.core.uncertainty import (
    DESCONHECIDO,
    corrigir_faixa,
    marcar_baixa_confianca,
    restringir_charset,
)

_D = r"[0-9?]"  # um dígito, ou "?" quando o caractere já veio marcado como incerto

# Aceitamos `?` dentro dos padrões porque um token já marcado como incerto
# continua sendo uma data/hora — descartá-lo perderia a linha inteira.
RE_DATA = re.compile(  # ex.: "07/03/2024", "7-3-24", "0?/03"
    rf"(?<![0-9?])({_D}{{1,2}})([/.\-])({_D}{{1,2}})(?:\2({_D}{{2,4}}))?(?![0-9?])"
)
RE_HORA = re.compile(rf"(?<![0-9?:]){_D}{{1,2}}[:.]{_D}{{2}}(?![0-9?:])")  # ex.: "08:03"
RE_MOEDA = re.compile(rf"(?<![0-9?]){_D}{{1,3}}(?:\.{_D}{{3}})*,{_D}{{2}}(?![0-9?])")  # ex.: "2.389,77"
RE_REFERENCIA = re.compile(rf"(?<![0-9?]){_D}{{1,5}}(?:[,.]{_D}{{1,3}})?%?(?![0-9?])")  # ex.: "1,5%"
RE_SO_DIGITOS = re.compile(rf"^{_D}+$")

# Nomes de mês abreviados em português -> número do mês (não usado nas regras
# acima, mas fica disponível para quem precisar interpretar "07/MAR/2024").
MESES_PT = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

ANO_MIN, ANO_MAX = 1970, 2100  # faixa de ano considerada plausível para estes documentos


# --------------------------------------------------------------------------
# Datas
# --------------------------------------------------------------------------


def sanear_data(bruto: str, conf: float = 100.0, origem: str = "text") -> str:
    """Devolve a data como impressa, com os dígitos impossíveis marcados.

    Isto é o que cumpre "nunca produza uma data impossível" sem violar
    "date_raw é o que está impresso": não corrigimos o valor para um palpite
    plausível, marcamos o dígito que não pode estar certo. `38/07` vira
    `??/07` — os dois dígitos são explicação possível — e `45/07` vira `?5/07`,
    porque nenhum `4X` é dia válido.
    """
    texto = marcar_baixa_confianca(bruto.strip(), conf, origem)  # marca glifo ambíguo, se for o caso
    m = RE_DATA.search(texto)
    if not m:
        return texto  # não achou nada parecido com data: devolve como veio

    dia, sep, mes, ano = m.group(1), m.group(2), m.group(3), m.group(4)

    dia_s = corrigir_faixa(dia, 1, 31)   # dia tem que estar entre 1 e 31
    mes_s = corrigir_faixa(mes, 1, 12)   # mês tem que estar entre 1 e 12
    ano_s = ano
    if ano and ano.isdigit():
        # Ano com 2 dígitos (ex.: "24") não é validado contra a faixa — não dá
        # pra saber se é 1924 ou 2024 só olhando os dois dígitos.
        ano_s = ano if len(ano) == 2 else corrigir_faixa(ano, ANO_MIN, ANO_MAX)

    # Componentes na faixa, mas a data não existe no calendário (31/02).
    # Marcamos o dia: é o componente que varia linha a linha, enquanto mês e
    # ano se repetem no documento inteiro e se corroboram entre si.
    if DESCONHECIDO not in dia_s and DESCONHECIDO not in mes_s:
        ano_int = _ano_int(ano_s)
        if ano_int is not None:
            ultimo = calendar.monthrange(ano_int, int(mes_s))[1]
            if int(dia_s) > ultimo:
                dia_s = corrigir_faixa(dia_s, 1, ultimo)

    saneado = dia_s + sep + mes_s + (sep + ano_s if ano_s else "")
    return texto[: m.start()] + saneado + texto[m.end() :]


def _ano_int(ano: str | None) -> int | None:
    if not ano or DESCONHECIDO in ano or not ano.isdigit():
        return None
    valor = int(ano)
    if len(ano) == 2:
        # Janela de dois dígitos: documentos trabalhistas são deste século.
        valor += 2000
    return valor if ANO_MIN <= valor <= ANO_MAX else None


@dataclass(frozen=True, slots=True)
class DataLida:
    dia: int | None
    mes: int | None
    ano: int | None

    @property
    def completa(self) -> bool:
        return self.dia is not None and self.mes is not None

    def como_date(self, ano_padrao: int | None = None) -> date | None:
        ano = self.ano if self.ano is not None else ano_padrao
        if self.dia is None or self.mes is None or ano is None:
            return None
        try:
            return date(ano, self.mes, self.dia)
        except ValueError:
            return None


def ler_data(bruto: str) -> DataLida:
    """Interpreta a data. Componente com `?` volta como None — não adivinhamos."""
    m = RE_DATA.search(bruto or "")
    if not m:
        return DataLida(None, None, None)  # nada parecido com data no texto

    def _int(valor: str | None) -> int | None:
        # Converte pra int só se for um número "limpo" (sem "?" e sem vazio).
        if not valor or DESCONHECIDO in valor or not valor.isdigit():
            return None
        return int(valor)

    return DataLida(dia=_int(m.group(1)), mes=_int(m.group(3)), ano=_ano_int(m.group(4)))


# --------------------------------------------------------------------------
# Horários
# --------------------------------------------------------------------------


def sanear_hora(bruto: str, conf: float = 100.0, origem: str = "text") -> tuple[str, str]:
    """Devolve `(time_raw, time_hhmm)`.

    `time_raw` é o impresso, com dígitos impossíveis marcados; `time_hhmm` é o
    mesmo horário em 24h com zero à esquerda. Os `?` atravessam a normalização
    intactos — `0?:25` continua `0?:25`, como no exemplo do enunciado.
    """
    texto = marcar_baixa_confianca(bruto.strip(), conf, origem)
    texto = restringir_charset(texto, "0123456789:.?")  # só dígitos, ":", "." e "?" sobrevivem
    texto = texto.replace(".", ":")  # "08.03" e "08:03" são o mesmo horário

    if ":" not in texto:
        return texto, texto  # não deu pra separar hora de minuto

    hora, _, minuto = texto.partition(":")  # parte antes e depois do primeiro ":"
    hora_s = corrigir_faixa(hora, 0, 23)
    minuto_s = corrigir_faixa(minuto, 0, 59)

    raw = f"{hora_s}:{minuto_s}"
    hhmm = f"{hora_s.rjust(2, '0')}:{minuto_s.rjust(2, '0')}"  # sempre 2 dígitos (zero à esquerda)
    return raw, hhmm


def encontrar_horas(texto: str) -> list[str]:
    """Lista todos os horários (HH:MM) achados dentro de um texto qualquer."""
    return [m.group(0) for m in RE_HORA.finditer(texto or "")]


# --------------------------------------------------------------------------
# Dinheiro e referência
# --------------------------------------------------------------------------


def sanear_moeda(bruto: str, conf: float = 100.0, origem: str = "text") -> str:
    """Normaliza um valor monetário mantendo o formato brasileiro impresso.

    Nada de float: `"2.389,77"` entra string e sai string.
    """
    texto = marcar_baixa_confianca(bruto.strip(), conf, origem)
    # Marcadores de natureza (D/C, sinal) não fazem parte do valor e o contrato
    # não tem onde guardá-los.
    texto = re.sub(r"[\s]*[-+]?\s*[DCdc]?$", "", texto).strip()
    return restringir_charset(texto, "0123456789.,?")


def sanear_referencia(bruto: str, conf: float = 100.0, origem: str = "text") -> str:
    """Normaliza uma referência (ex.: "220,00 h" ou "8%") mantendo o texto impresso."""
    texto = marcar_baixa_confianca(bruto.strip(), conf, origem)
    return restringir_charset(texto, "0123456789.,%?")


def eh_moeda(texto: str) -> bool:
    """True se o texto inteiro (sem sobra) parece um valor monetário."""
    return bool(RE_MOEDA.fullmatch((texto or "").strip()))


def eh_referencia(texto: str) -> bool:
    """True se o texto inteiro (sem sobra) parece uma referência numérica."""
    return bool(RE_REFERENCIA.fullmatch((texto or "").strip()))
