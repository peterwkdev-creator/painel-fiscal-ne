"""Cliente do SIOPS (aplicação em saúde) pelo TabNet do DATASUS.

Fonte diferente do SICONFI em tudo -- protocolo, codificação e escala -- e por
isso mora num módulo próprio. **Todo fato aqui foi medido contra o serviço vivo
em 07 e 08/09/2026**, antes de o leitor existir; o registro está em
`.claude/rules/painel-fiscal.md` e em `progress/painel-fiscal.md`.

A escala é o motivo de existir: **uma requisição por UF devolve todos os
municípios dela nos 26 exercícios**, 2000 a 2025. São 27 requisições para o país
inteiro, contra 5.570 por exercício no SICONFI.

Como no `siconfi.py`, a rede fica atrás de `Transporte` para que o módulo seja
testável sem tocar a rede.
"""

from __future__ import annotations

import html as _html
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

# **`http://`, não `https://`.** O host não serve TLS, e ferramenta que promove
# para HTTPS devolve `ECONNREFUSED` na 443 -- que parece fonte morta e não é.
# Foi assim que esta base de 26 anos quase foi descartada, em 07/09/2026.
BASE = "http://siops-asp.datasus.gov.br/CGI"
CONSULTA = BASE + "/tabcgi.exe?SIOPS/serhist/municipio/indic{uf}.def"
FORMULARIO = BASE + "/deftohtm.exe?SIOPS/serhist/municipio/indic{uf}.def"

FALHA_DE_REDE = 599
REPETIVEIS = frozenset({429, 500, 502, 503, 504, FALHA_DE_REDE})
PAUSA_PADRAO = 1.0
TENTATIVAS_PADRAO = 4

# Os 26 arquivos anuais, 2000 a 2025. Verificado em 08/09/2026 lendo o
# <SELECT NAME=Arquivos> do formulário -- não suposto.
PRIMEIRO_ANO = 2000
ULTIMO_ANO = 2025
ARQUIVOS = tuple(
    "indmun{:02d}.dbf".format(a % 100) for a in range(PRIMEIRO_ANO, ULTIMO_ANO + 1)
)

# **O valor tem um % literal**, e é copiado do formulário exatamente como está.
# Escrevê-lo à mão mistura % cru com byte já codificado, e a consulta volta 200
# com tabela vazia -- que parece "não há dado" e é erro de chamada.
INDICADOR_EC29 = "3.2_%R.Próprios_em_Saúde-EC_29"

# Reticências no TabNet são ausência; nunca zero. Medido: 3 células em 4.784 no
# Ceará, então a base é praticamente completa -- mas 3 gravadas como 0 seriam
# três municípios acusados de não aplicar nada em saúde.
AUSENTE = "..."

# **26 UFs, não 27.** O Distrito Federal não tem série MUNICIPAL no SIOPS:
# `serhist/municipio/indicDF.def` responde **HTTP 502**, e o DF aparece em
# `serhist/estado/indicDF.def`, com um `SELECT SDistrito_Federal` no lugar do
# `SMunic`. Não é falha da coleta: o DF acumula as competências de estado e de
# município, e por isso não presta contas como município. Mesmo formato do fato
# já registrado sobre Fernando de Noronha no SICONFI -- as duas fontes estão
# certas, e o painel exibe a diferença em vez de ajustá-la para bater.
UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC",
    "SE", "SP", "TO",
)
SEM_SERIE_MUNICIPAL = ("DF",)

# **O piso legal NÃO é 15% em todo ano da série, e isso muda o que se pode
# afirmar.** A EC 29/2000 (ADCT, art. 77) fixou 7% para 2000 e mandou cada ente
# fechar a própria diferença "à razão de, pelo menos, um quinto por ano" até
# 15% em 2004. Entre 2001 e 2003 o piso é INDIVIDUAL -- depende de onde aquele
# município partiu --, então não existe régua nacional comparável nesses anos.
# Da LC 141/2012 (art. 7º) em diante, 15% fixo; a LC 227, de 13/01/2026,
# reescreveu o artigo para incluir o IBS na base e **manteve os 15%**.
#
# Sem isto, "abaixo de 15%" em 2000 acusaria 3.428 municípios de descumprir uma
# regra que ainda não valia para eles.
PISO_2004_EM_DIANTE = 15.0
PISO_POR_ANO = {2000: 7.0}


def piso_legal(ano: int):
    """O piso comparável daquele ano, ou `None` quando não há um.

    `None` em 2001-2003 é resposta, não lacuna: naqueles anos a régua era a
    trajetória de cada município, e uma média nacional não a substitui.
    """
    if ano in PISO_POR_ANO:
        return PISO_POR_ANO[ano]
    if ano >= 2004:
        return PISO_2004_EM_DIANTE
    return None


class ErroSiops(Exception):
    """Falha que não adianta repetir: resposta ilegível ou status definitivo."""


@dataclass(frozen=True)
class Resposta:
    status: int
    corpo: str


Transporte = Callable[[str, "bytes | None"], Resposta]
Dormir = Callable[[float], None]


@dataclass(frozen=True)
class Serie:
    """A série de um município: ano -> valor, com os anos ausentes de fora.

    `codigo_siops` tem **seis** dígitos -- ver `resolver_ibge`. Guardar o código
    da fonte como ela o entrega, e resolver a chave num passo explícito, é o que
    torna visível o município que não casa, em vez de sumir com ele.
    """

    codigo_siops: int
    nome: str
    valores: dict

    @property
    def anos(self) -> list:
        return sorted(self.valores)


def transporte_http(timeout: float = 180.0) -> Transporte:
    """Transporte real. **Nunca levanta por falha de rede**: devolve status.

    Mesmo desenho e mesmo motivo do `siconfi.transporte_http` -- inclusive o
    `except OSError`, pela fresta do `TimeoutError` de socket, que não é
    `URLError`.
    """

    def buscar(url, corpo=None):
        cabecalhos = {"User-Agent": "painel-fiscal/1.0 (uso pessoal)"}
        if corpo:
            cabecalhos["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=corpo, headers=cabecalhos)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # **A resposta é latin-1**, como a página inteira do TabNet.
                return Resposta(r.status, r.read().decode("latin-1"))
        except urllib.error.HTTPError as e:
            return Resposta(e.code, e.read().decode("latin-1", "replace"))
        except urllib.error.URLError as e:
            return Resposta(FALHA_DE_REDE, "{}: {}".format(type(e).__name__, e.reason))
        except OSError as e:
            return Resposta(FALHA_DE_REDE, "{}: {}".format(type(e).__name__, e))

    return buscar


def corpo_consulta(indicador: str = INDICADOR_EC29, arquivos=ARQUIVOS) -> bytes:
    """O corpo do POST, **codificado em latin-1**.

    A página inteira do TabNet é latin-1, e "Municípios" com acento precisa casar
    byte a byte com o que o servidor espera. Enviar em UTF-8 devolve 200 com
    tabela vazia -- de novo a falha que parece ausência de dado.
    """
    campos = [("Linha", "Municípios"), ("Coluna", "Ano"), ("Incremento", indicador)]
    campos += [("Arquivos", a) for a in arquivos]
    campos += [("formato", "table"), ("mostre", "Mostra")]
    return urllib.parse.urlencode(campos, encoding="latin-1").encode()


def buscar(
    url,
    corpo,
    o_que,
    transporte,
    *,
    dormir=time.sleep,
    tentativas=TENTATIVAS_PADRAO,
    pausa=PAUSA_PADRAO,
) -> str:
    """Busca com espera dobrando e devolve o corpo. Repete só o que vale."""
    espera = pausa
    ultimo = None
    for tentativa in range(1, tentativas + 1):
        ultimo = transporte(url, corpo)
        if ultimo.status == 200:
            return ultimo.corpo
        if ultimo.status not in REPETIVEIS:
            raise ErroSiops("HTTP {} ao buscar {}: {!r}".format(
                ultimo.status, o_que, ultimo.corpo[:200]))
        if tentativa < tentativas:
            dormir(espera)
            espera *= 2
    assert ultimo is not None
    raise ErroSiops("desisti de {} depois de {} tentativas; último status {}".format(
        o_que, tentativas, ultimo.status))


# **As tags não são fechadas** -- nem TD, nem TH, nem OPTION. Casar TR com TR de
# fechamento devolve DUAS linhas numa tabela de 184 municípios, e um leitor
# construído sobre isso parece dizer "a fonte não tem dado". O que funciona é
# trocar toda tag por quebra e ler a sequência de células que sobra.
def _celulas(bruto: str) -> list:
    texto = re.sub(r"<[^>]+>", "\n", bruto).replace("&nbsp;", " ")
    return [c for c in (_html.unescape(x).strip() for x in texto.split("\n")) if c]


_CABECALHO_LINHA = re.compile(r"^(\d{6})\s+(.+)$")
_NUMERO = re.compile(r"^-?[\d.]*,\d+$")


def _numero(v: str):
    """`None` para ausência. As reticências do TabNet **não são zero**."""
    if not _NUMERO.match(v):
        return None
    return float(v.replace(".", "").replace(",", "."))


def _anos_do_cabecalho(celulas: list) -> list:
    """Os anos como o SERVIDOR os devolveu, nunca como foram pedidos.

    A ordem das colunas é decisão dele. Assumir a ordem do pedido casaria cada
    município com o ano do vizinho -- e a tabela continuaria bem formada, que é
    exatamente a classe de defeito que não dói onde nasce.
    """
    try:
        i = celulas.index("Municípios")
    except ValueError as e:
        raise ErroSiops(
            "não achei o cabeçalho 'Municípios' na resposta; a consulta "
            "provavelmente voltou vazia (corpo em UTF-8? indicador errado?)"
        ) from e
    anos = []
    j = i + 1
    while j < len(celulas) and re.fullmatch(r"\d{4}", celulas[j]):
        anos.append(int(celulas[j]))
        j += 1
    if not anos:
        raise ErroSiops(
            "cabeçalho sem ano nenhum depois de 'Municípios': {!r}".format(
                celulas[i + 1:i + 4]))
    return anos


def ler_tabela(bruto: str):
    """Lê a tabela do TabNet: os anos do cabeçalho e uma série por município."""
    celulas = _celulas(bruto)
    anos = _anos_do_cabecalho(celulas)
    saida = []
    i = 0
    while i < len(celulas):
        m = _CABECALHO_LINHA.match(celulas[i])
        if not m:
            i += 1
            continue
        valores = []
        j = i + 1
        while j < len(celulas) and not _CABECALHO_LINHA.match(celulas[j]):
            valores.append(celulas[j])
            j += 1
        # **Cortar no número de anos.** A última linha da tabela engole o rodapé
        # da página -- legenda, "Copia como .CSV", "Voltar" -- e veio com 45
        # células em vez de 27. Sem o corte, o rodapé viraria dado.
        valores = valores[:len(anos)]
        saida.append(Serie(
            codigo_siops=int(m.group(1)),
            nome=m.group(2).strip(),
            valores={a: v for a, v in zip(anos, (_numero(x) for x in valores))
                     if v is not None},
        ))
        i = j
    return anos, saida


def serie_da_uf(uf, transporte, *, dormir=time.sleep,
                indicador=INDICADOR_EC29, arquivos=ARQUIVOS):
    """Todos os municípios de uma UF, em todos os exercícios pedidos."""
    bruto = buscar(
        CONSULTA.format(uf=uf.upper()),
        corpo_consulta(indicador, arquivos),
        "a série do SIOPS de {}".format(uf.upper()),
        transporte,
        dormir=dormir,
    )
    return ler_tabela(bruto)


def indicadores(uf, transporte, *, dormir=time.sleep) -> list:
    """Os indicadores que a UF oferece, lidos do formulário.

    Existe para que o nome do indicador seja **lido, nunca chutado** -- as
    OPTION não são fechadas, então o regex tem de parar no sinal de menor.
    """
    bruto = buscar(FORMULARIO.format(uf=uf.upper()), None,
                   "o formulário do SIOPS de {}".format(uf.upper()),
                   transporte, dormir=dormir)
    m = re.search(r'<SELECT[^>]*NAME=["\']?Incremento["\']?[^>]*>(.*?)</SELECT>',
                  bruto, re.S | re.I)
    if not m:
        raise ErroSiops("formulário de {} sem SELECT Incremento".format(uf.upper()))
    return [v for v, _ in re.findall(
        r'<OPTION[^>]*VALUE=["\']([^"\']+)["\'][^>]*>([^<]*)', m.group(1), re.I)]


# ------------------------------------------------------------------ a junção

def resolver_ibge(codigos_ibge) -> dict:
    """Mapa: código de 6 dígitos -> código do IBGE de 7.

    **O SIOPS entrega o código SEM o dígito verificador**: `230010 Abaiara`
    contra `2300101` no IBGE e no SICONFI, que é a chave de todo este projeto.
    Os seis primeiros dígitos identificam o município sozinhos -- o sétimo é
    verificador --, então dividir por dez é a ponte, e ela foi medida: no Ceará,
    **184 de 184 casaram, sem sobra dos dois lados**.

    Casar por NOME seria a alternativa óbvia e estaria errada: `Itapagé` no
    SIOPS é `Itapajé` no IBGE, grafia mudada em 2010, e há mais casos assim.
    """
    return {c // 10: c for c in codigos_ibge}


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower())
                   if not unicodedata.combining(c))


def conferir_nomes(series, nomes_ibge) -> list:
    """Onde o código casa mas o nome diverge -- para OLHAR, não para corrigir.

    Divergência é notícia sobre a fonte (grafia mudada, acento perdido), não
    defeito a consertar em silêncio. Devolve (código, nome no SIOPS, nome IBGE).
    """
    fora = []
    for s in series:
        ibge = nomes_ibge.get(s.codigo_siops)
        if ibge is not None and _sem_acento(s.nome) != _sem_acento(ibge):
            fora.append((s.codigo_siops, s.nome, ibge))
    return fora
