"""Persistência em SQLite. Nada sai da máquina.

O desenho responde a dois critérios de aceite da especificação: **ingerir duas
vezes não muda nada** e **ausência é gravada como ausência, nunca como zero**.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .siconfi import Ente, Pessoal

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ente (
    codigo_ibge INTEGER PRIMARY KEY,
    nome        TEXT NOT NULL,
    uf          TEXT NOT NULL,
    regiao      TEXT NOT NULL,
    esfera      TEXT NOT NULL,
    populacao   INTEGER,
    cnpj        TEXT,
    visto_em    TEXT NOT NULL
);

-- Uma linha por ente/exercicio/periodo. `publicou` separa as duas ausencias
-- que nunca podem virar a mesma coisa:
--   publicou = 0  -> o ente NAO entregou o relatorio (items: [] com HTTP 200)
--   publicou = 1 com percentual NULL -> entregou, mas sem aquele campo
-- Zero em `percentual` continua significando zero de verdade.
CREATE TABLE IF NOT EXISTS pessoal (
    codigo_ibge       INTEGER NOT NULL,
    exercicio         INTEGER NOT NULL,
    periodo           INTEGER NOT NULL,
    publicou          INTEGER NOT NULL,
    rcl               REAL,
    rcl_ajustada      REAL,
    despesa           REAL,
    percentual        REAL,
    limite_prudencial REAL,
    fonte             TEXT NOT NULL,
    coletado_em       TEXT NOT NULL,
    PRIMARY KEY (codigo_ibge, exercicio, periodo)
);

-- Marca de progresso: e o que torna a varredura retomavel sem reler o que ja
-- veio. Uma hora de rede e tempo de sobra para algo dar errado.
CREATE TABLE IF NOT EXISTS coleta (
    iniciada_em  TEXT NOT NULL,
    terminada_em TEXT,
    exercicio    INTEGER NOT NULL,
    periodo      INTEGER NOT NULL,
    lidos        INTEGER NOT NULL DEFAULT 0,
    publicaram   INTEGER NOT NULL DEFAULT 0,
    falhou_com   TEXT
);
"""

FONTE = "SICONFI/Tesouro Nacional — RGF Anexo 01"


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def abrir(caminho: str | Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(ESQUEMA)
        yield con
        con.commit()
    finally:
        con.close()


def gravar_entes(con: sqlite3.Connection, entes: list[Ente]) -> int:
    """Idempotente: o mesmo ente gravado duas vezes continua sendo uma linha."""
    con.executemany(
        "INSERT INTO ente (codigo_ibge, nome, uf, regiao, esfera, populacao, cnpj, visto_em)"
        " VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(codigo_ibge) DO UPDATE SET"
        "   nome=excluded.nome, uf=excluded.uf, regiao=excluded.regiao,"
        "   esfera=excluded.esfera, populacao=excluded.populacao,"
        "   cnpj=excluded.cnpj, visto_em=excluded.visto_em",
        [(e.codigo_ibge, e.nome, e.uf, e.regiao, e.esfera, e.populacao, e.cnpj, agora())
         for e in entes],
    )
    return len(entes)


def gravar_pessoal(
    con: sqlite3.Connection,
    codigo_ibge: int,
    exercicio: int,
    periodo: int,
    p: Pessoal | None,
) -> None:
    """Grava o resultado -- inclusive quando o resultado é "não publicou".

    `p is None` não é motivo para não gravar: gravar a ausência é o que impede a
    varredura de tentar o mesmo ente de novo a cada retomada, e é o que permite
    distinguir "não entregou" de "ainda não perguntei".
    """
    con.execute(
        "INSERT INTO pessoal (codigo_ibge, exercicio, periodo, publicou, rcl,"
        " rcl_ajustada, despesa, percentual, limite_prudencial, fonte, coletado_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(codigo_ibge, exercicio, periodo) DO UPDATE SET"
        "   publicou=excluded.publicou, rcl=excluded.rcl,"
        "   rcl_ajustada=excluded.rcl_ajustada, despesa=excluded.despesa,"
        "   percentual=excluded.percentual, limite_prudencial=excluded.limite_prudencial,"
        "   fonte=excluded.fonte, coletado_em=excluded.coletado_em",
        (codigo_ibge, exercicio, periodo, 1 if p else 0,
         p.rcl if p else None, p.rcl_ajustada if p else None,
         p.despesa if p else None,
         p.percentual if p else None, p.limite_prudencial if p else None,
         FONTE, agora()),
    )


def ja_coletados(con: sqlite3.Connection, exercicio: int, periodo: int) -> set[int]:
    """Quem já foi perguntado neste período -- a base da retomada."""
    return {r[0] for r in con.execute(
        "SELECT codigo_ibge FROM pessoal WHERE exercicio=? AND periodo=?",
        (exercicio, periodo))}


def abrir_coleta(con: sqlite3.Connection, exercicio: int, periodo: int) -> int:
    cur = con.execute(
        "INSERT INTO coleta (iniciada_em, exercicio, periodo) VALUES (?,?,?)",
        (agora(), exercicio, periodo))
    return cur.lastrowid


def fechar_coleta(con: sqlite3.Connection, rowid: int, lidos: int,
                  publicaram: int, falhou_com: str | None = None) -> None:
    con.execute(
        "UPDATE coleta SET terminada_em=?, lidos=?, publicaram=?, falhou_com=?"
        " WHERE rowid=?",
        (agora(), lidos, publicaram, falhou_com, rowid))
