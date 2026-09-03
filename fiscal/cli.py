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
    entes, pessoal, transporte_http,
)

BANCO_PADRAO = os.environ.get("PAINEL_FISCAL_BANCO", "painel.db")

# 1.793 municípios com RGF municipal no Nordeste. Não é 1.794: Fernando de
# Noronha é distrito estadual de PE, não município. Ver a regra do sistema.
ESPERADOS_NO_NE = 1793


def _br(v: float | None, casas: int = 2, sufixo: str = "") -> str:
    """Número no formato brasileiro. `None` vira travessão, nunca zero."""
    if v is None:
        return "—"
    s = f"{v:,.{casas}f}".translate(str.maketrans(",.", ".,"))
    return f"{s}{sufixo}"


def cmd_ingerir_entes(args, transporte: Transporte, dormir: Dormir) -> int:
    achados = entes(transporte, dormir=dormir, uf=NORDESTE, esfera="M")
    with armazem.abrir(args.banco) as con:
        n = armazem.gravar_entes(con, achados)
    print(f"{n} municípios do Nordeste gravados.")
    if n != ESPERADOS_NO_NE:
        print(f"  ATENÇÃO: esperados {ESPERADOS_NO_NE}, vieram {n}. "
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
    if universo != ESPERADOS_NO_NE:
        print(f"  ATENÇÃO: o universo deveria ser {ESPERADOS_NO_NE}.")
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
            # Fernando de Noronha é distrito estadual de PE, não município: não
            # entrega RGF municipal. A diferença para os 1.794 do IBGE é
            # exibida, não escondida. Ver `especs/painel-fiscal.md`.
            "municipiosIbgeNoNordeste": 1794,
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
    }
    destino = Path(args.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(snapshot, ensure_ascii=False,
                                  separators=(",", ":")) + "\n", encoding="utf-8")
    kb = destino.stat().st_size // 1024
    print(f"{len(linhas)} municípios em {destino} ({kb} KB). "
          f"{publicaram} publicaram, {consultados - publicaram} não publicaram, "
          f"{universo - consultados} ainda não consultados.")
    return 0


def montar() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fiscal", description="Painel Fiscal do Nordeste")
    p.add_argument("--banco", default=BANCO_PADRAO)
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("ingerir-entes", help="a lista de municípios do NE (1 requisição)")

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
          "conferir": cmd_conferir, "exportar": cmd_exportar}[args.comando]
    try:
        return fn(args, t, dormir)
    except ErroSiconfi as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
