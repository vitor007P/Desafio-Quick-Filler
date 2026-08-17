"""Marcação de incerteza por caractere.

Duas regras do enunciado moram aqui:

1. Caractere que não deu para ler vira `?`. Nunca um chute com cara de acerto.
2. A incerteza é por caractere, não por linha.

Decisão explícita: **não** aplicamos mapa de confusão (O→0, S→5, l→1). Trocar
um glifo por "o dígito que ele provavelmente era" é exatamente produzir um
valor errado com aparência de certo — o pior resultado possível neste domínio.
Um `O` num campo numérico vira `?` e some na revisão.

Também não enchemos a saída de `?` por precaução: encher de incerteza é dizer
que não transcrevemos nada. Só marcamos o que tem motivo concreto — o
caractere viola a forma do campo, ou veio de OCR com confiança baixa **e** é um
glifo reconhecidamente ambíguo.
"""

from __future__ import annotations

from app.config import settings

DESCONHECIDO = "?"

#: Glifos que os motores de OCR trocam entre si com frequência. Só estes são
#: candidatos a `?` por confiança baixa; um "K" mal lido continua "K", porque
#: ninguém confunde K com dígito.
GLIFOS_AMBIGUOS = frozenset("0OoQD1lI|!5S6Gb8B2Z7T4A9gq")


def _conf_muito_baixa() -> float:
    """Abaixo disto a leitura da palavra é, na prática, um palpite do motor."""
    return max(0.0, float(settings.ocr_low_conf) - 25.0)


def restringir_charset(texto: str, permitidos: str) -> str:
    """Todo caractere fora do conjunto esperado do campo vira `?`.

    É o caminho mais comum de `?` na prática: o motor devolveu `O8:3O` onde só
    cabiam dígitos e `:`.
    """
    # Percorre caractere por caractere: mantém quem está na lista de
    # permitidos, troca por "?" quem não está.
    return "".join(c if c in permitidos else DESCONHECIDO for c in texto)


def marcar_baixa_confianca(texto: str, conf: float, origem: str = "ocr") -> str:
    """Marca glifos ambíguos quando a palavra veio de OCR pouco confiante.

    Texto embutido não passa por aqui: se o PDF diz `8`, é `8` — não há
    reconhecimento envolvido, e marcar seria inventar incerteza.
    """
    if origem != "ocr" or conf >= _conf_muito_baixa():
        return texto  # veio da camada de texto, ou a confiança está boa: não mexe
    return "".join(DESCONHECIDO if c in GLIFOS_AMBIGUOS else c for c in texto)


def tem_incerteza(*valores: str) -> bool:
    """True se algum dos textos passados contém o marcador de incerteza."""
    return any(DESCONHECIDO in (v or "") for v in valores)


def mascarar_posicoes(componente: str, posicoes: set[int]) -> str:
    """Troca por `?` só os índices marcados em `posicoes`, mantendo o resto."""
    return "".join(
        DESCONHECIDO if i in posicoes else c for i, c in enumerate(componente)
    )


def posicoes_suspeitas(componente: str, minimo: int, maximo: int) -> set[int]:
    """Quais dígitos podem ser os responsáveis por um valor fora de faixa.

    Testa, dígito a dígito, se existe alguma substituição que traria o número
    de volta para a faixa válida. Se só uma posição resolve, marcamos só ela —
    `45` para um dia vira `?5`, porque nenhum `4X` é dia válido. Se mais de uma
    posição resolve, marcamos todas: em `38` tanto `30/31` quanto `08/18/28`
    são explicações plausíveis, e apontar um culpado seria inventar.
    """
    if DESCONHECIDO in componente or not componente.isdigit():
        return set()  # já tem "?" ou não é número: nada a fazer aqui

    valor = int(componente)
    if minimo <= valor <= maximo:
        return set()  # já está na faixa válida, nenhum dígito é suspeito

    culpados: set[int] = set()
    for i in range(len(componente)):  # testa cada posição do número
        for d in "0123456789":  # tenta trocar por cada dígito possível
            if d == componente[i]:
                continue  # não faz sentido "trocar" pelo mesmo dígito
            candidato = componente[:i] + d + componente[i + 1 :]
            if minimo <= int(candidato) <= maximo:
                culpados.add(i)
                break  # achou uma troca que resolve; não precisa testar as outras

    # Nenhuma troca de um dígito só salva o número: tudo é suspeito.
    return culpados or set(range(len(componente)))


def corrigir_faixa(componente: str, minimo: int, maximo: int) -> str:
    """Devolve o componente com os dígitos impossíveis marcados como `?`."""
    return mascarar_posicoes(componente, posicoes_suspeitas(componente, minimo, maximo))
