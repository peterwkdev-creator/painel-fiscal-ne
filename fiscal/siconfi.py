"""Cliente da API de dados abertos do SICONFI (Tesouro Nacional).

Todo fato aqui foi **verificado contra a API real em 03/09/2026**, antes de uma
linha de código existir. O registro completo está em
`.claude/rules/painel-fiscal.md`.

O acesso à rede fica atrás de `Transporte`, e a espera atrás de `dormir`, para
que o sistema inteiro seja testável sem tocar a rede e sem esperar de verdade --
mesmo desenho do `sys-radar-licitacoes`, e pelo mesmo motivo.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

BASE = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt"

# Pseudo-status para falha de rede. Não existe no HTTP: serve para que o
# transporte **nunca levante** e quem decide repetir possa receber, em vez de
# ser interrompido. Ver a seção correspondente na regra do radar.
FALHA_DE_REDE = 599
REPETIVEIS = frozenset({429, 500, 502, 503, 504, FALHA_DE_REDE})

# Medido em 03/09/2026: 8 requisições a 0,8 s, 8 em HTTP 200, ~1,1 s cada.
# A API é lenta, não hostil -- ao contrário do PNCP, não há orçamento diário.
PAUSA_PADRAO = 0.8
TENTATIVAS_PADRAO = 4

NORDESTE = frozenset({"MA", "PI", "CE", "RN", "PB", "PE", "AL", "SE", "BA"})

# As três linhas que interessam no RGF Anexo 01, dentro de 189-229 devolvidas.
# `DespesaComPessoalTotal` aparece DUAS vezes, distinguida só pela coluna: ler
# apenas pelo `cod_conta` pega a linha errada metade das vezes.
COLUNA_VALOR = "Valor"
COLUNA_PERCENTUAL = "% sobre a RCL Ajustada"
# A RCL **bruta** e a RCL **AJUSTADA** sao numeros diferentes, e o percentual
# publicado usa a ajustada -- o proprio rotulo da coluna diz "% sobre a RCL
# Ajustada". Salvador/BA em 2024/3: bruta 10.384.311.525,53, ajustada
# 10.250.806.767,53, e 3.318.008.507,92 / ajustada = 32,37%, o valor declarado.
# Usar a bruta faz a conferencia divergir SEMPRE no mesmo sentido -- foi assim
# que `fiscal conferir` denunciou o erro do proprio leitor, em 03/09/2026.
CONTA_RCL = "ReceitaCorrenteLiquidaLimite"
CONTA_RCL_AJUSTADA = "ReceitaCorrenteLiquidaAjustada"
CONTA_PESSOAL = "DespesaComPessoalTotal"
CONTA_PRUDENCIAL = "LimitePrudencialDespesaComPe"


class ErroSiconfi(Exception):
    """Falha que não adianta repetir: resposta ilegível ou status definitivo."""


@dataclass(frozen=True)
class Resposta:
    status: int
    corpo: str


Transporte = Callable[[str], Resposta]
Dormir = Callable[[float], None]


@dataclass(frozen=True)
class Ente:
    """Uma linha de `/entes`. `esfera` M = município, E = estado, U = União."""

    codigo_ibge: int
    nome: str
    uf: str
    regiao: str
    esfera: str
    populacao: int | None
    cnpj: str | None

    @classmethod
    def de_json(cls, d: dict) -> "Ente":
        return cls(
            codigo_ibge=int(d["cod_ibge"]),
            nome=(d.get("ente") or "").strip(),
            uf=(d.get("uf") or "").strip(),
            regiao=(d.get("regiao") or "").strip(),
            esfera=(d.get("esfera") or "").strip(),
            populacao=_inteiro(d.get("populacao")),
            cnpj=(d.get("cnpj") or None),
        )


@dataclass(frozen=True)
class Pessoal:
    """Despesa com pessoal de um ente num quadrimestre, como ELE a declarou.

    O percentual vem **calculado e homologado pelo próprio ente**. Recalcular a
    partir de `despesa / rcl` criaria uma segunda verdade que ninguém assinou --
    ver `especs/painel-fiscal.md`.
    """

    codigo_ibge: int
    exercicio: int
    periodo: int
    rcl: float | None
    rcl_ajustada: float | None
    despesa: float | None
    percentual: float | None
    limite_prudencial: float | None

    @property
    def acima_do_prudencial(self) -> bool | None:
        """`None` quando falta um dos dois lados -- ausência não é 'está abaixo'."""
        if self.percentual is None or self.limite_prudencial is None:
            return None
        return self.percentual > self.limite_prudencial


def _inteiro(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _numero(v) -> float | None:
    """Número da API, ou `None`. **Zero é um número e é preservado** -- a
    ausência é representada por `None`, nunca por 0."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def transporte_http(timeout: float = 60.0) -> Transporte:
    """Transporte real. **Nunca levanta por falha de rede**: devolve status.

    `TimeoutError` de socket é `OSError`, **não** `urllib.error.URLError` --
    foi por essa fresta que um erro de rede atravessou todo o backoff do radar e
    derrubou uma execução com 2.500 registros já lidos. Aqui ele é capturado.
    """

    def buscar(url: str) -> Resposta:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json",
                     "User-Agent": "painel-fiscal/1.0 (uso pessoal)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return Resposta(r.status, r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return Resposta(e.code, e.read().decode("utf-8", "replace"))
        except urllib.error.URLError as e:
            return Resposta(FALHA_DE_REDE, f"{type(e).__name__}: {e.reason}")
        except OSError as e:
            return Resposta(FALHA_DE_REDE, f"{type(e).__name__}: {e}")

    return buscar


def _envelope(resposta: Resposta, o_que: str) -> dict:
    """Decodifica o envelope `{items, hasMore, limit, offset, count, links}`."""
    try:
        d = json.loads(resposta.corpo)
    except json.JSONDecodeError as e:
        amostra = resposta.corpo[:200]
        raise ErroSiconfi(
            f"resposta ilegível ao buscar {o_que} "
            f"(HTTP {resposta.status}): {amostra!r}"
        ) from e
    if not isinstance(d, dict) or "items" not in d:
        raise ErroSiconfi(
            f"envelope inesperado ao buscar {o_que} (HTTP {resposta.status}): "
            f"chaves {sorted(d) if isinstance(d, dict) else type(d).__name__}"
        )
    return d


def buscar(
    url: str,
    o_que: str,
    transporte: Transporte,
    *,
    dormir: Dormir = time.sleep,
    tentativas: int = TENTATIVAS_PADRAO,
    pausa: float = PAUSA_PADRAO,
) -> dict:
    """Busca uma URL com espera dobrando, e devolve o envelope decodificado.

    Repete só o que vale a pena repetir (`REPETIVEIS`). Status definitivo -- um
    400 por parâmetro errado, por exemplo -- levanta na hora: repetir um erro de
    programação só gasta tempo e esconde a causa.
    """
    espera = pausa
    ultimo: Resposta | None = None
    for tentativa in range(1, tentativas + 1):
        ultimo = transporte(url)
        if ultimo.status == 200:
            return _envelope(ultimo, o_que)
        if ultimo.status not in REPETIVEIS:
            raise ErroSiconfi(
                f"HTTP {ultimo.status} ao buscar {o_que}: {ultimo.corpo[:200]!r}"
            )
        if tentativa < tentativas:
            dormir(espera)
            espera *= 2
    assert ultimo is not None
    raise ErroSiconfi(
        f"desisti de {o_que} depois de {tentativas} tentativas; "
        f"último status {ultimo.status}"
    )


def url_entes() -> str:
    return f"{BASE}/entes"


def entes(
    transporte: Transporte,
    *,
    dormir: Dormir = time.sleep,
    uf: frozenset[str] | None = None,
    esfera: str | None = "M",
) -> list[Ente]:
    """Todos os entes, ou só os das UFs pedidas.

    **Uma requisição basta**: verificado em 03/09/2026, `/entes` devolve os
    5.598 entes de uma vez (`limit` 6000, `hasMore` false). Não paginar sem
    necessidade.

    Filtrando pelas nove UFs do Nordeste com `esfera="M"` saem **1.793** --
    e não 1.794. A diferença é Fernando de Noronha, distrito estadual de PE e
    não município, que por isso não entrega RGF municipal. Ver a regra.
    """
    d = buscar(url_entes(), "a lista de entes", transporte, dormir=dormir)
    saida = [Ente.de_json(x) for x in d["items"]]
    if uf is not None:
        saida = [e for e in saida if e.uf in uf]
    if esfera is not None:
        saida = [e for e in saida if e.esfera == esfera]
    return saida


def url_rgf(exercicio: int, periodo: int, codigo_ibge: int) -> str:
    """RGF Anexo 01 (despesa com pessoal) do Executivo municipal.

    `id_ente` é obrigatório na prática: sem ele a API devolve `items: []` com
    HTTP **200** -- resposta vazia, não erro. E **repetir o parâmetro não busca
    dois entes**: volta um só, em silêncio. Daí uma requisição por município.
    """
    if not 1 <= periodo <= 3:
        raise ValueError(f"quadrimestre fora de 1..3: {periodo!r}")
    q = urllib.parse.urlencode({
        "an_exercicio": exercicio,
        "in_periodicidade": "Q",
        "nr_periodo": periodo,
        "co_tipo_demonstrativo": "RGF",
        "no_anexo": "RGF-Anexo 01",
        "co_esfera": "M",
        "co_poder": "E",
        "id_ente": codigo_ibge,
    })
    return f"{BASE}/rgf?{q}"


def _linha(itens: list[dict], prefixo_conta: str, coluna: str) -> float | None:
    for x in itens:
        conta = (x.get("cod_conta") or "").strip()
        if conta.startswith(prefixo_conta) and (x.get("coluna") or "").strip() == coluna:
            return _numero(x.get("valor"))
    return None


def pessoal(
    codigo_ibge: int,
    exercicio: int,
    periodo: int,
    transporte: Transporte,
    *,
    dormir: Dormir = time.sleep,
) -> Pessoal | None:
    """A despesa com pessoal de um ente, ou `None` se ele **não publicou**.

    `items: []` com HTTP 200 é a armadilha central desta API. Não é erro e não é
    zero: é ausência. Quem tratar como erro repete para sempre; quem tratar como
    dado grava zero onde não há informação -- e zero é um número.
    """
    d = buscar(
        url_rgf(exercicio, periodo, codigo_ibge),
        f"o RGF de {codigo_ibge} em {exercicio}/{periodo}",
        transporte,
        dormir=dormir,
    )
    itens = d["items"]
    if not itens:
        return None
    return Pessoal(
        codigo_ibge=codigo_ibge,
        exercicio=exercicio,
        periodo=periodo,
        rcl=_linha(itens, CONTA_RCL, COLUNA_VALOR),
        rcl_ajustada=_linha(itens, CONTA_RCL_AJUSTADA, COLUNA_VALOR),
        despesa=_linha(itens, CONTA_PESSOAL, COLUNA_VALOR),
        percentual=_linha(itens, CONTA_PESSOAL, COLUNA_PERCENTUAL),
        limite_prudencial=_linha(itens, CONTA_PRUDENCIAL, COLUNA_PERCENTUAL),
    )

# ---------------------------------------------------------------- despesa por função

# As 28 funções orçamentárias da Portaria MOG 42/1999. É lista **fechada e
# legal**, não inferência: o anexo não traz hierarquia utilizável (`cod_conta`
# tem dois valores para 1.464 linhas), então o que separa função de subfunção é
# o nome estar aqui. Subfunção fica de fora -- somá-la duplicaria tudo.
FUNCOES: tuple[str, ...] = (
    "Legislativa", "Judiciária", "Essencial à Justiça", "Administração",
    "Defesa Nacional", "Segurança Pública", "Relações Exteriores",
    "Assistência Social", "Previdência Social", "Saúde", "Trabalho",
    "Educação", "Cultura", "Direitos da Cidadania", "Urbanismo", "Habitação",
    "Saneamento", "Gestão Ambiental", "Ciência e Tecnologia", "Agricultura",
    "Organização Agrária", "Indústria", "Comércio e Serviços", "Comunicações",
    "Energia", "Transporte", "Desporto e Lazer", "Encargos Especiais",
)

# **Liquidada, não empenhada nem orçada.** Empenhada é dinheiro reservado;
# dotação é intenção. Liquidada é a despesa que o município reconhece como
# efetivamente realizada -- a única que responde "quanto gastou".
COLUNA_LIQUIDADA = "DESPESAS LIQUIDADAS ATÉ O BIMESTRE (d)"

# **Cada função aparece DUAS vezes**, sob dois rótulos: uma no total geral e
# outra em "Intra-Orçamentárias", que são transferências entre órgãos do mesmo
# governo. Ler sem filtrar o rótulo pega a segunda e publica número **vinte
# vezes menor**: Salvador/BA 2024 marcaria R$ 137 milhões em Saúde no lugar de
# R$ 2,86 bilhões. Descoberto em 03/09/2026, antes de ingerir.
ROTULO_EXCETO_INTRA = "Total das Despesas Exceto Intra-Orçamentárias"
CONTA_TOTAL_DESPESAS = "DESPESAS (EXCETO INTRA-ORÇAMENTÁRIAS)"


@dataclass(frozen=True)
class Funcoes:
    """A despesa liquidada por função, e o total que o próprio anexo declara.

    O total existe para **conferir**: a soma das funções tem de fechar com ele.
    É uma integridade que a fonte oferece de graça, e mais forte que a do RGF --
    ali só dá para comparar razão contra razão; aqui, parte contra todo.
    """

    codigo_ibge: int
    exercicio: int
    periodo: int
    total: float | None
    valores: dict[str, float]

    @property
    def soma(self) -> float:
        return sum(self.valores.values())

    @property
    def fecha(self) -> bool | None:
        """`None` sem total -- ausência de régua não é aprovação."""
        if self.total is None or not self.valores:
            return None
        return abs(self.soma - self.total) <= max(1.0, abs(self.total) * 1e-9)


def url_rreo(exercicio: int, periodo: int, codigo_ibge: int) -> str:
    """RREO Anexo 02 (despesa por função) de um ente.

    O RREO é **bimestral** -- `nr_periodo` de 1 a 6 --, enquanto o RGF é
    quadrimestral de 1 a 3. Confundir os dois devolve vazio sem dizer por quê.
    """
    if not 1 <= periodo <= 6:
        raise ValueError(f"bimestre fora de 1..6: {periodo!r}")
    q = urllib.parse.urlencode({
        "an_exercicio": exercicio,
        "nr_periodo": periodo,
        "co_tipo_demonstrativo": "RREO",
        "no_anexo": "RREO-Anexo 02",
        "co_esfera": "M",
        "id_ente": codigo_ibge,
    })
    return f"{BASE}/rreo?{q}"


def _valor_de(itens: list[dict], conta: str) -> float | None:
    for x in itens:
        if (x.get("coluna") == COLUNA_LIQUIDADA
                and x.get("rotulo") == ROTULO_EXCETO_INTRA
                and (x.get("conta") or "").strip() == conta):
            return _numero(x.get("valor"))
    return None


def funcoes(
    codigo_ibge: int,
    exercicio: int,
    periodo: int,
    transporte: Transporte,
    *,
    dormir: Dormir = time.sleep,
) -> Funcoes | None:
    """A despesa por função de um ente, ou `None` se ele não publicou."""
    d = buscar(
        url_rreo(exercicio, periodo, codigo_ibge),
        f"o RREO de {codigo_ibge} em {exercicio}/{periodo}",
        transporte,
        dormir=dormir,
    )
    itens = d["items"]
    if not itens:
        return None
    valores = {}
    for f in FUNCOES:
        v = _valor_de(itens, f)
        if v is not None:
            valores[f] = v
    total = next(
        (_numero(x.get("valor")) for x in itens
         if x.get("coluna") == COLUNA_LIQUIDADA
         and x.get("rotulo") == ROTULO_EXCETO_INTRA
         and CONTA_TOTAL_DESPESAS in (x.get("conta") or "")),
        None,
    )
    return Funcoes(codigo_ibge, exercicio, periodo, total, valores)
