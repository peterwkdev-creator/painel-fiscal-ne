"""Linha de comando do painel fiscal.

    python -m fiscal ingerir-entes
    python -m fiscal ingerir --exercicio 2024 --periodo 3
    python -m fiscal resumo
    python -m fiscal listar --acima-do-limite
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import armazem
from .siconfi import (
    NORDESTE, PAUSA_PADRAO, Dormir, ErroSiconfi, Transporte,
    entes, funcoes, pessoal, transporte_http,
)

BANCO_PADRAO = os.environ.get("PAINEL_FISCAL_BANCO", "painel.db")

# 1.793 municípios com RGF municipal no Nordeste. Não é 1.794: Fernando de
# Noronha é distrito estadual de PE, não município. Ver a regra do sistema.
#: O recorte é **bandeira, não pressuposto**, como no `sys-educacao-inep` e no
#: `sys-observatorio-ne`. Cravar o Nordeste no código transformaria a expansão
#: numa refatoração; sendo parâmetro, ela é um comando.
#:
#: Os números vêm da reconciliação de 03/09/2026 contra `/entes`: 1.793
#: municípios no Nordeste e 5.570 no país. O IBGE conta 1.794 e 5.571, e a
#: única diferença — nos dois recortes — é **Fernando de Noronha**, distrito
#: estadual de PE e não município. Nenhum ente existe só no SICONFI.
RECORTES = {
    "NE": (NORDESTE, 1793),
    "BR": (None, 5570),
}

#: Quantos municípios o IBGE conta e o SICONFI não. É **um**, e é Fernando de
#: Noronha — verificado nos dois recortes em 03/09/2026, comparando `/entes`
#: com a lista de localidades do IBGE: 1.793 × 1.794 no Nordeste e 5.570 ×
#: 5.571 no país, com zero entes existindo só no SICONFI.
MUNICIPIOS_SO_NO_IBGE = 1

def _br(v: float | None, casas: int = 2, sufixo: str = "") -> str:
    """Número no formato brasileiro. `None` vira travessão, nunca zero."""
    if v is None:
        return "—"
    s = f"{v:,.{casas}f}".translate(str.maketrans(",.", ".,"))
    return f"{s}{sufixo}"


def cmd_ingerir_entes(args, transporte: Transporte, dormir: Dormir) -> int:
    ufs, esperados = RECORTES[args.regiao]
    achados = entes(transporte, dormir=dormir, uf=ufs, esfera="M")
    with armazem.abrir(args.banco) as con:
        n = armazem.gravar_entes(con, achados)
    onde = "do Nordeste" if args.regiao == "NE" else "do Brasil"
    print(f"{n} municípios {onde} gravados.")
    if n != esperados:
        print(f"  ATENÇÃO: esperados {esperados}, vieram {n}. "
              f"Conferir antes de seguir — o universo mudou ou a API mudou.")
    return 0


def cmd_ingerir(args, transporte: Transporte, dormir: Dormir) -> int:
    """Varredura retomável: um município por requisição, ~57 min para o NE.

    Assume que **vai ser interrompida**. Grava por município e marca progresso,
    então rodar de novo continua em vez de gastar uma hora relendo.
    """
    with armazem.abrir(args.banco) as con:
        alvos = [r[0] for r in con.execute(
            "SELECT codigo_ibge FROM ente WHERE esfera='M' ORDER BY codigo_ibge")]
        feitos = set() if args.recomecar else armazem.ja_coletados(
            con, args.exercicio, args.periodo)
    if not alvos:
        print("Nenhum ente no banco. Rode `ingerir-entes` primeiro.", file=sys.stderr)
        return 2

    pendentes = [c for c in alvos if c not in feitos]
    print(f"{len(alvos)} municípios; {len(feitos)} já coletados; "
          f"{len(pendentes)} pendentes em {args.exercicio}/{args.periodo}.")
    if args.limite:
        pendentes = pendentes[:args.limite]
        print(f"  limitado a {len(pendentes)} nesta execução.")

    lidos = publicaram = 0
    falha: str | None = None
    with armazem.abrir(args.banco) as con:
        coleta = armazem.abrir_coleta(con, args.exercicio, args.periodo)
    try:
        for codigo in pendentes:
            p = pessoal(codigo, args.exercicio, args.periodo, transporte, dormir=dormir)
            with armazem.abrir(args.banco) as con:
                armazem.gravar_pessoal(con, codigo, args.exercicio, args.periodo, p)
            lidos += 1
            publicaram += 1 if p else 0
            if lidos % 50 == 0:
                print(f"  ... {lidos} lidos, {publicaram} publicaram")
            dormir(args.pausa)
    except (Exception, KeyboardInterrupt) as e:
        # Capturar só `ErroSiconfi` seria proteção de mentira: a garantia de
        # "preserva o que já foi lido" tem de estar no código, não no README.
        falha = f"{type(e).__name__}: {e}"
        print(f"\n>>> interrompida: {falha}", file=sys.stderr)
        print(f">>> {lidos} municípios gravados; rodar de novo continua daqui.",
              file=sys.stderr)
    finally:
        with armazem.abrir(args.banco) as con:
            armazem.fechar_coleta(con, coleta, lidos, publicaram, falha)
    print(f"{lidos} lidos, {publicaram} publicaram.")
    return 1 if falha else 0


def cmd_resumo(args, *_) -> int:
    with armazem.abrir(args.banco) as con:
        r = con.execute(
            "SELECT COUNT(*) t, SUM(publicou) p FROM pessoal"
            " WHERE exercicio=? AND periodo=?", (args.exercicio, args.periodo)).fetchone()
        if not r["t"]:
            print("Nada coletado nesse período ainda.")
            return 0
        nao = r["t"] - (r["p"] or 0)
        print(f"{args.exercicio}/{args.periodo}: {r['t']} consultados, "
              f"{r['p']} publicaram, {nao} não publicaram.\n")
        for linha in con.execute(
            "SELECT e.uf, COUNT(*) n, AVG(p.percentual) media,"
            "       SUM(CASE WHEN p.percentual > p.limite_prudencial THEN 1 ELSE 0 END) acima"
            "  FROM pessoal p JOIN ente e USING (codigo_ibge)"
            " WHERE p.exercicio=? AND p.periodo=? AND p.percentual IS NOT NULL"
            " GROUP BY e.uf ORDER BY media DESC", (args.exercicio, args.periodo)):
            print(f"  {linha['uf']}  {linha['n']:>4} munic.  "
                  f"média {_br(linha['media'], 2, '%'):>8}  "
                  f"acima do prudencial: {linha['acima']}")
    return 0


def cmd_listar(args, *_) -> int:
    sql = ("SELECT e.nome, e.uf, p.percentual, p.limite_prudencial, p.despesa,"
           "       p.coletado_em"
           "  FROM pessoal p JOIN ente e USING (codigo_ibge)"
           " WHERE p.exercicio=? AND p.periodo=? AND p.percentual IS NOT NULL")
    if args.acima_do_limite:
        sql += " AND p.percentual > p.limite_prudencial"
    sql += " ORDER BY p.percentual DESC LIMIT ?"
    with armazem.abrir(args.banco) as con:
        linhas = con.execute(
            sql, (args.exercicio, args.periodo, args.limite)).fetchall()
    if not linhas:
        print("Nada a listar.")
        return 0
    for l in linhas:
        acima = l["percentual"] > (l["limite_prudencial"] or float("inf"))
        print(f"{'!' if acima else ' '} {l['nome']}/{l['uf']:<3} "
              f"{_br(l['percentual'], 2, '%'):>8}"
              f"  (limite {_br(l['limite_prudencial'], 2, '%')})"
              f"  R$ {_br(l['despesa'])}")
    print(f"\nFonte: {armazem.FONTE}. Coleta em {linhas[0]['coletado_em'][:10]}.")
    return 0


def cmd_ingerir_funcoes(args, transporte: Transporte, dormir: Dormir) -> int:
    """Varre a despesa por função (RREO Anexo 02). Mesmo desenho do `ingerir`.

    O RREO é **bimestral**: `--periodo` vai de 1 a 6, não de 1 a 3 como o RGF.
    """
    with armazem.abrir(args.banco) as con:
        alvos = [r[0] for r in con.execute(
            "SELECT codigo_ibge FROM ente WHERE esfera='M' ORDER BY codigo_ibge")]
        feitos = set() if args.recomecar else armazem.ja_consultados_funcoes(
            con, args.exercicio, args.periodo)
    if not alvos:
        print("Nenhum ente no banco. Rode `ingerir-entes` primeiro.", file=sys.stderr)
        return 2

    pendentes = [c for c in alvos if c not in feitos]
    print(f"{len(alvos)} municípios; {len(feitos)} já consultados; "
          f"{len(pendentes)} pendentes em {args.exercicio}/{args.periodo} (bimestre).")
    if args.limite:
        pendentes = pendentes[:args.limite]
        print(f"  limitado a {len(pendentes)} nesta execução.")

    lidos = publicaram = nao_fecham = 0
    falha: str | None = None
    with armazem.abrir(args.banco) as con:
        coleta = armazem.abrir_coleta(con, args.exercicio, args.periodo)
    try:
        for codigo in pendentes:
            f = funcoes(codigo, args.exercicio, args.periodo, transporte, dormir=dormir)
            with armazem.abrir(args.banco) as con:
                armazem.gravar_funcoes(con, codigo, args.exercicio, args.periodo, f)
            lidos += 1
            if f:
                publicaram += 1
                if f.fecha is False:
                    nao_fecham += 1
            if lidos % 50 == 0:
                print(f"  ... {lidos} lidos, {publicaram} publicaram, "
                      f"{nao_fecham} não fecham")
            dormir(args.pausa)
    except (Exception, KeyboardInterrupt) as e:
        falha = f"{type(e).__name__}: {e}"
        print(f"\n>>> interrompida: {falha}", file=sys.stderr)
        print(f">>> {lidos} municípios gravados; rodar de novo continua daqui.",
              file=sys.stderr)
    finally:
        with armazem.abrir(args.banco) as con:
            armazem.fechar_coleta(con, coleta, lidos, publicaram, falha)
    print(f"{lidos} lidos, {publicaram} publicaram, {nao_fecham} com soma que "
          f"não fecha com o total declarado.")
    return 1 if falha else 0


def cmd_funcoes(args, *_) -> int:
    """O que os municípios gastam por função, e quantos relatórios não fecham."""
    with armazem.abrir(args.banco) as con:
        cob = con.execute(
            "SELECT COUNT(*) t, SUM(publicou) p, SUM(CASE WHEN fecha=0 THEN 1 ELSE 0 END) nf"
            "  FROM funcao_consulta WHERE exercicio=? AND periodo=?",
            (args.exercicio, args.periodo)).fetchone()
        if not cob["t"]:
            print("Nada coletado nesse período ainda.")
            return 0
        print(f"{args.exercicio}/{args.periodo}: {cob['t']} consultados, "
              f"{cob['p']} publicaram, {cob['nf']} com soma que não fecha.\n")
        for l in con.execute(
            "SELECT funcao, COUNT(*) n, SUM(valor) soma,"
            "       AVG(valor * 100.0 / NULLIF(total_declarado,0)) media_pct"
            "  FROM despesa_funcao WHERE exercicio=? AND periodo=? AND valor IS NOT NULL"
            " GROUP BY funcao ORDER BY soma DESC LIMIT ?",
                (args.exercicio, args.periodo, args.limite)):
            print(f"  {l['funcao']:24} R$ {_br(l['soma']):>20}   "
                  f"média {_br(l['media_pct'], 1, '%'):>7} do orçamento   "
                  f"({l['n']} municípios)")
    return 0


def cmd_conferir(args, *_) -> int:
    """Confere a coerência interna do que foi lido, e RELATA o que não bate.

    Não recalcula para corrigir — o percentual publicado continua sendo a
    verdade. O que se faz aqui é perguntar se os números lidos do mesmo
    relatório contam a mesma história: `despesa / RCL ajustada` tem de bater com
    o percentual declarado.

    **O denominador é a RCL AJUSTADA, não a bruta** — o rótulo da coluna diz
    "% sobre a RCL Ajustada". Na primeira versão esta conferência usava a bruta
    e divergia em TODOS os municípios, sempre no mesmo sentido. A divergência
    sistemática não era ruído: era esta função denunciando o erro de leitura de
    quem a escreveu. Divergência num sentido só nunca é acaso.
    """
    with armazem.abrir(args.banco) as con:
        linhas = con.execute(
            "SELECT p.codigo_ibge, e.nome, e.uf, p.rcl_ajustada, p.despesa,"
            "       p.percentual"
            "  FROM pessoal p JOIN ente e USING (codigo_ibge)"
            " WHERE p.exercicio=? AND p.periodo=? AND p.publicou=1"
            "   AND p.rcl_ajustada IS NOT NULL AND p.despesa IS NOT NULL"
            "   AND p.percentual IS NOT NULL AND p.rcl_ajustada <> 0",
            (args.exercicio, args.periodo)).fetchall()
        universo = con.execute(
            "SELECT COUNT(*) FROM ente WHERE esfera='M'").fetchone()[0]
        consultados = con.execute(
            "SELECT COUNT(*) FROM pessoal WHERE exercicio=? AND periodo=?",
            (args.exercicio, args.periodo)).fetchone()[0]

    print(f"Universo: {universo} municípios. Consultados: {consultados}. "
          f"Com os três números: {len(linhas)}.")
    # O universo é conferido contra os tamanhos que `RECORTES` declara, e não
    # contra um número inferido da própria contagem.
    #
    # A versão anterior fazia `5570 if universo > 1793 else 1793`, o que decide
    # o esperado a partir do que achou -- e tem um buraco: um banco NACIONAL
    # que tivesse perdido exatamente 3.777 municípios cairia em 1.793, bateria
    # com o "esperado" e passaria em silêncio. Também duplicava, em número
    # solto, o que `RECORTES` já diz.
    #
    # Assim, qualquer contagem que não seja a de um recorte conhecido avisa --
    # inclusive uma varredura interrompida no meio, que é o caso comum.
    tamanhos = {n for _, n in RECORTES.values()}
    if universo not in tamanhos:
        print(f"  ATENÇÃO: o universo é {universo}, e nenhum recorte conhecido "
              f"tem esse tamanho ({sorted(tamanhos)}). Banco incompleto?")
    if not linhas:
        return 0

    divergentes = []
    for l in linhas:
        calculado = l["despesa"] / l["rcl_ajustada"] * 100
        if abs(calculado - l["percentual"]) > args.tolerancia:
            divergentes.append((l, calculado))

    print(f"\nDivergência acima de {args.tolerancia} ponto percentual: "
          f"{len(divergentes)} de {len(linhas)}.")
    for l, calculado in divergentes[:args.limite]:
        print(f"  {l['nome']}/{l['uf']:<3} declarado {_br(l['percentual'], 2, '%')}"
              f"  vs  despesa/RCL ajustada {_br(calculado, 2, '%')}")
    if len(divergentes) > args.limite:
        print(f"  ... e mais {len(divergentes) - args.limite}.")
    print("\nDivergência é relatada, nunca corrigida: o percentual publicado "
          "pelo ente continua sendo o número do painel.")
    return 0


def _funcoes_de(con, ex: int, pe: int, indice: dict[str, int]) -> dict[str, list]:
    """As linhas de um período no formato esparso `[total, [[indice, valor]]]`.

    O `indice` vem de fora porque os dois períodos comparados **têm de
    compartilhar o mesmo array de rótulos**: índices que significassem funções
    diferentes em cada ano trocariam educação por saúde na comparação, sem
    nada estourar.
    """
    saida: dict[str, list] = {}
    for r in con.execute(
        "SELECT codigo_ibge, funcao, valor, total_declarado FROM despesa_funcao"
        " WHERE exercicio=? AND periodo=? AND valor IS NOT NULL"
        " ORDER BY codigo_ibge, valor DESC", (ex, pe)):
        # Centavos num orçamento municipal são ruído, e cada casa decimal
        # custa bytes em 19.500 valores. O total vem da mesma linha, arredondado
        # do mesmo jeito, para que a soma continue conferindo contra ele.
        entrada = saida.setdefault(
            str(r["codigo_ibge"]),
            [None if r["total_declarado"] is None else round(r["total_declarado"]), []])
        entrada[1].append([indice[r["funcao"]], round(r["valor"])])
    return saida


def _cobertura_funcoes(con, ex: int, pe: int) -> dict:
    r = con.execute(
        "SELECT COUNT(*) t, SUM(publicou) p,"
        "       SUM(CASE WHEN fecha=0 THEN 1 ELSE 0 END) nf"
        "  FROM funcao_consulta WHERE exercicio=? AND periodo=?", (ex, pe)).fetchone()
    return {"consultados": r["t"], "publicaram": r["p"] or 0, "naoFecham": r["nf"] or 0}


def _bloco_funcoes(con) -> dict | None:
    """A despesa por função, em SÉRIE — todos os exercícios do mesmo bimestre.

    **O período não vem de `--periodo`, e isso é deliberado.** O RREO é
    bimestral (1..6) e o RGF é quadrimestral (1..3): um `--periodo 3` significa
    coisas diferentes nos dois, e passar o do RGF aqui devolveria silenciosamente
    o terceiro bimestre em vez do que se pediu. O último coletado é a única
    resposta que não depende de quem digitou o comando.

    ## Por que a série é do MESMO bimestre em anos diferentes

    Medido em 03/09/2026, e o resultado decidiu o desenho. O RREO é **acumulado
    no ano**: o 6º bimestre já contém o 4º -- a mediana da razão b4/b6 deu
    **0,629**, ou seja 63% do valor do 6º **é** o do 4º. Não são duas
    observações independentes, são aninhadas, e a fatia de cada função mal se
    mexe entre elas: mediana de **0,96 ponto percentual** de deslocamento.

    Comparando o mesmo bimestre de dois anos, as acumulações são disjuntas e o
    deslocamento mediano sobe para **1,67 pp**, com 42% das comparações movendo
    2 pontos ou mais. É a mesma janela do ano, um ano depois.

    Por isso a série é `(ano, MESMO pe)` para cada ano coletado, e **nunca**
    mistura bimestres: se alguém varrer 2024/4, ele não entra na série do 6º.

    ## Uma lista, e não "atual" mais "anterior"

    A versão anterior emitia o período em destaque e um bloco `anterior`. Com
    três exercícios ou mais isso obrigaria a um terceiro formato, ou a repetir
    o mesmo ano em dois lugares do arquivo -- e dado repetido é dado que
    diverge. A lista vem do mais recente para o mais antigo, e quem quer só a
    foto usa o primeiro elemento.
    """
    ultimo = con.execute(
        "SELECT exercicio, periodo FROM funcao_consulta"
        " ORDER BY exercicio DESC, periodo DESC LIMIT 1").fetchone()
    if ultimo is None:
        return None
    pe = ultimo["periodo"]

    # Todo exercício com ESTE bimestre, do mais recente ao mais antigo --
    # **e só os COMPLETOS**.
    #
    # Um exercício varrido pela metade colocaria na série um ponto que separa
    # os municípios em dois grupos indistinguíveis: os que não entregaram
    # naquele ano, e os que ainda não perguntamos. A página diria "não tem
    # 2022" sobre quem tem, e a série desenharia um buraco que é nosso, não do
    # município.
    #
    # É a mesma distinção da faixa `nao-consultado`, agora dentro de uma série
    # -- e aqui ela é pior, porque um buraco no meio de uma linha temporal se
    # lê como interrupção do serviço, não como ausência de coleta.
    universo = con.execute("SELECT COUNT(*) FROM ente").fetchone()[0]
    anos = [r["exercicio"] for r in con.execute(
        "SELECT exercicio FROM funcao_consulta WHERE periodo = ?"
        " GROUP BY exercicio HAVING COUNT(DISTINCT codigo_ibge) >= ?"
        " ORDER BY exercicio DESC", (pe, universo))]
    if not anos:
        return None

    # Os rótulos saem da UNIÃO de todos os exercícios, ordenados pela soma no
    # mais recente. Uma função que só existe num ano antigo precisa de índice;
    # sem ele, a linha dela seria descartada em silêncio.
    marcas = ",".join("?" for _ in anos)
    rotulos = [r[0] for r in con.execute(
        "SELECT funcao,"
        "       SUM(CASE WHEN exercicio=? THEN valor ELSE 0 END) peso"
        "  FROM despesa_funcao"
        f" WHERE exercicio IN ({marcas}) AND periodo = ? AND valor IS NOT NULL"
        " GROUP BY funcao ORDER BY peso DESC, funcao", [anos[0], *anos, pe])]
    indice = {nome: i for i, nome in enumerate(rotulos)}

    return {
        "periodo": pe,
        "fonte": armazem.FONTE_FUNCOES,
        "rotulos": rotulos,
        "colunasMunicipio": ["total", "valores"],
        "exercicios": [
            {
                "exercicio": ex,
                "coletadoEm": con.execute(
                    "SELECT MAX(coletado_em) FROM funcao_consulta"
                    " WHERE exercicio=? AND periodo=?", (ex, pe)).fetchone()[0],
                "cobertura": _cobertura_funcoes(con, ex, pe),
                "porMunicipio": _funcoes_de(con, ex, pe, indice),
            }
            for ex in anos
        ],
    }


def cmd_exportar(args, *_) -> int:
    """Gera o snapshot que o painel lê no build.

    Arrays compactos em vez de objetos por município: 1.793 registros com nomes
    de campo repetidos custam vários múltiplos do necessário, e o arquivo é
    baixado por quem visita.
    """
    import json
    from pathlib import Path

    with armazem.abrir(args.banco) as con:
        universo = con.execute(
            "SELECT COUNT(*) FROM ente WHERE esfera='M'").fetchone()[0]
        linhas = con.execute(
            "SELECT e.codigo_ibge, e.nome, e.uf, e.populacao,"
            "       p.publicou, p.percentual, p.limite_prudencial, p.despesa,"
            "       p.rcl, p.rcl_ajustada"
            "  FROM ente e LEFT JOIN pessoal p"
            "    ON p.codigo_ibge = e.codigo_ibge AND p.exercicio=? AND p.periodo=?"
            " WHERE e.esfera='M' ORDER BY e.uf, e.nome",
            (args.exercicio, args.periodo)).fetchall()
        coleta = con.execute(
            "SELECT MAX(coletado_em) FROM pessoal WHERE exercicio=? AND periodo=?",
            (args.exercicio, args.periodo)).fetchone()[0]
        periodos = [[r[0], r[1]] for r in con.execute(
            "SELECT DISTINCT exercicio, periodo FROM pessoal"
            " ORDER BY exercicio, periodo")]
        # Só quem publicou entra na série: "não entregou" já é dito pelo campo
        # `publicou` do período em destaque, e repetir a ausência em cada ponto
        # da série inflaria o arquivo sem acrescentar informação.
        serie: dict[str, list] = {}
        for r in con.execute(
            "SELECT codigo_ibge, exercicio, periodo, publicou, percentual"
            "  FROM pessoal WHERE percentual IS NOT NULL"
            " ORDER BY codigo_ibge, exercicio, periodo"):
            serie.setdefault(str(r["codigo_ibge"]), []).append(
                [r["exercicio"], r["periodo"], bool(r["publicou"]), r["percentual"]])
        bloco_funcoes = _bloco_funcoes(con)

    consultados = sum(1 for l in linhas if l["publicou"] is not None)
    publicaram = sum(1 for l in linhas if l["publicou"] == 1)
    snapshot = {
        "geradoEm": armazem.agora(),
        "coletadoEm": coleta,
        "fonte": armazem.FONTE,
        "exercicio": args.exercicio,
        "periodo": args.periodo,
        "limites": {"prudencial": 51.3, "legal": 54.0},
        "cobertura": {
            "universo": universo,
            "consultados": consultados,
            "publicaram": publicaram,
            # **Derivado do universo, não cravado.** A versão anterior tinha
            # `1794` literal, e a expansão nacional o teria publicado inalterado
            # ao lado de 5.570 municípios -- uma "diferença" de 3.776 que não
            # existe, exibida com a autoridade de um número conferido.
            #
            # A relação é estável e vale nos dois recortes: o IBGE conta
            # **exatamente um a mais** que o SICONFI, e esse um é Fernando de
            # Noronha, distrito estadual de PE e não município, que por isso
            # não entrega RGF municipal. Reconciliado em 03/09/2026 nos dois
            # universos: nenhum ente existe só no SICONFI.
            "municipiosIbge": universo + MUNICIPIOS_SO_NO_IBGE,
        },
        "colunas": ["codigo", "nome", "uf", "populacao", "publicou",
                    "percentual", "limitePrudencial", "despesa", "rclAjustada"],
        "municipios": [
            [l["codigo_ibge"], l["nome"], l["uf"], l["populacao"],
             None if l["publicou"] is None else bool(l["publicou"]),
             l["percentual"], l["limite_prudencial"], l["despesa"],
             l["rcl_ajustada"]]
            for l in linhas
        ],
        # A série histórica de TODOS os períodos já coletados, separada dos
        # campos do período em destaque. Um número sozinho não diz se o
        # município está melhorando ou piorando -- e essa é justamente a
        # pergunta que a foto esconde. Entre 2024/2 e 2024/3, 45 municípios
        # saíram de cima do teto legal; sem série, isso é invisível.
        #
        # Fica em objeto separado, e não como coluna nova, porque a tupla
        # posicional de `municipios` é contrato com o TypeScript: acrescentar
        # um array lá dentro complicaria o tipo sem ganhar nada.
        "colunasSerie": ["exercicio", "periodo", "publicou", "percentual"],
        "serie": serie,
        "periodos": periodos,
        # `null` enquanto nenhuma varredura de funções tiver rodado -- a chave
        # existe sempre, porque o TypeScript do outro lado declara o campo e o
        # teste de contrato compara os dois conjuntos de chaves.
        "funcoes": bloco_funcoes,
    }
    destino = Path(args.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(snapshot, ensure_ascii=False,
                                  separators=(",", ":")) + "\n", encoding="utf-8")
    kb = destino.stat().st_size // 1024
    print(f"{len(linhas)} municípios em {destino} ({kb} KB). "
          f"{publicaram} publicaram, {consultados - publicaram} não publicaram, "
          f"{universo - consultados} ainda não consultados.")
    if snapshot["funcoes"]:
        f = snapshot["funcoes"]
        print(f"  despesa por função, {len(f['rotulos'])} funções, "
              f"{len(f['exercicios'])} exercício(s) no {f['periodo']}º bimestre:")
        for e in f["exercicios"]:
            print(f"    {e['exercicio']}/{f['periodo']}: "
                  f"{len(e['porMunicipio'])} municípios.")
    else:
        print("  sem despesa por função: rode `ingerir-funcoes`.")
    return 0


def montar() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fiscal", description="Painel Fiscal do Nordeste")
    p.add_argument("--banco", default=BANCO_PADRAO)
    sub = p.add_subparsers(dest="comando", required=True)

    ie = sub.add_parser("ingerir-entes",
                        help="a lista de municípios (1 requisição)")
    ie.add_argument("--regiao", default="NE", choices=sorted(RECORTES),
                    help="NE (padrão) ou BR")

    # O RREO e bimestral (1..6); o RGF e quadrimestral (1..3). Sao subcomandos
    # separados de proposito: um `--periodo 6` no comando errado devolve vazio
    # sem dizer por que, e a mensagem que falta e "voce usou a escala errada".
    for nome, ajuda in (("ingerir-funcoes", "varre a despesa por função (bimestral)"),
                        ("funcoes", "o que se gasta por função")):
        s = sub.add_parser(nome, help=ajuda)
        s.add_argument("--exercicio", type=int, default=2024)
        s.add_argument("--periodo", type=int, default=6, choices=(1, 2, 3, 4, 5, 6))
        s.add_argument("--limite", type=int, default=0 if nome == "ingerir-funcoes" else 12)
        if nome == "ingerir-funcoes":
            s.add_argument("--pausa", type=float, default=PAUSA_PADRAO)
            s.add_argument("--recomecar", action="store_true")

    for nome, ajuda in (("ingerir", "varre o RGF, retomável"),
                        ("resumo", "contagem e média por UF"),
                        ("listar", "os maiores percentuais"),
                        ("conferir", "coerência interna do que foi lido"),
                        ("exportar", "gera o snapshot que o painel lê")):
        s = sub.add_parser(nome, help=ajuda)
        s.add_argument("--exercicio", type=int, default=2024)
        s.add_argument("--periodo", type=int, default=3, choices=(1, 2, 3))
        if nome == "conferir":
            s.add_argument("--tolerancia", type=float, default=0.05,
                           help="divergencia aceita, em pontos percentuais")
            s.add_argument("--limite", type=int, default=15)
        if nome == "exportar":
            s.add_argument("--saida", default="painel/dados/snapshot.json")
        if nome == "ingerir":
            s.add_argument("--pausa", type=float, default=PAUSA_PADRAO)
            s.add_argument("--recomecar", action="store_true")
            s.add_argument("--limite", type=int, default=0,
                           help="para so N municipios nesta execucao")
        if nome == "listar":
            s.add_argument("--acima-do-limite", action="store_true")
            s.add_argument("--limite", type=int, default=30)
    return p


def principal(argv=None, transporte: Transporte | None = None,
              dormir: Dormir = time.sleep) -> int:
    args = montar().parse_args(argv)
    t = transporte or transporte_http()
    fn = {"ingerir-entes": cmd_ingerir_entes, "ingerir": cmd_ingerir,
          "resumo": cmd_resumo, "listar": cmd_listar,
          "conferir": cmd_conferir, "exportar": cmd_exportar,
          "ingerir-funcoes": cmd_ingerir_funcoes, "funcoes": cmd_funcoes}[args.comando]
    try:
        return fn(args, t, dormir)
    except ErroSiconfi as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
