"""O CLI inteiro, com transporte e relógio falsos.

Existe porque idempotência e retomada testadas só no armazém provam metade: o
que se quer garantir é que **rodar o comando de novo** não gasta requisição
repetida nem muda número. Isso só o caminho completo mostra.
"""

import json
import tempfile
import unittest
from pathlib import Path

from fiscal.armazem import abrir
from fiscal.cli import principal
from fiscal.siconfi import FALHA_DE_REDE, Resposta

FIX = Path(__file__).parent / "fixtures"


def fixture(nome: str) -> dict:
    return json.loads((FIX / nome).read_text(encoding="utf-8"))["resposta"]


class TransporteFalso:
    """Responde `/entes` com a amostra e `/rgf` com Salvador, contando as URLs."""

    def __init__(self, rgf: dict | None = None, quebrar_em: int | None = None):
        self.pedidas: list[str] = []
        self.rgf = rgf if rgf is not None else fixture("rgf_salvador.json")
        self.quebrar_em = quebrar_em

    def __call__(self, url: str) -> Resposta:
        self.pedidas.append(url)
        if self.quebrar_em and len(self.pedidas) > self.quebrar_em:
            return Resposta(FALHA_DE_REDE, "TimeoutError: timed out")
        corpo = fixture("entes_amostra.json") if "/entes" in url else self.rgf
        return Resposta(200, json.dumps(corpo))

    @property
    def pedidos_rgf(self) -> int:
        return sum(1 for u in self.pedidas if "/rgf" in u)


class PontaAPonta(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.banco = str(Path(self.dir.name) / "teste.db")

    def tearDown(self):
        self.dir.cleanup()

    def rodar(self, *argv, transporte):
        return principal(["--banco", self.banco, *argv],
                         transporte=transporte, dormir=lambda _: None)

    def test_ingere_entes_e_depois_o_rgf(self):
        t = TransporteFalso()
        self.assertEqual(self.rodar("ingerir-entes", transporte=t), 0)
        self.assertEqual(self.rodar("ingerir", "--limite", "5", transporte=t), 0)
        with abrir(self.banco) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM pessoal").fetchone()[0], 5)
        self.assertEqual(t.pedidos_rgf, 5)

    def test_rodar_de_novo_nao_gasta_requisicao(self):
        """O critério que justifica a tabela de progresso: uma varredura do NE
        leva ~57 minutos, e reler o que já veio é o desperdício que importa.

        Sem `--limite`: com limite, a segunda execução pega os N **seguintes**
        da fila, que é o comportamento certo e não prova nada sobre repetição.
        """
        t = TransporteFalso()
        self.rodar("ingerir-entes", transporte=t)
        self.rodar("ingerir", transporte=t)          # varre tudo
        antes = t.pedidos_rgf
        self.assertGreater(antes, 0)
        self.rodar("ingerir", transporte=t)          # nada pendente
        self.assertEqual(t.pedidos_rgf - antes, 0, "repetiu requisição já feita")

    def test_rodar_de_novo_nao_duplica_nem_muda_numero(self):
        t = TransporteFalso()
        self.rodar("ingerir-entes", transporte=t)
        self.rodar("ingerir", transporte=t)
        with abrir(self.banco) as con:
            antes = con.execute(
                "SELECT codigo_ibge, percentual FROM pessoal ORDER BY 1").fetchall()
        self.rodar("ingerir", transporte=t)
        self.rodar("ingerir-entes", transporte=t)
        with abrir(self.banco) as con:
            depois = con.execute(
                "SELECT codigo_ibge, percentual FROM pessoal ORDER BY 1").fetchall()
        self.assertEqual([tuple(r) for r in antes], [tuple(r) for r in depois])

    def test_interrupcao_preserva_o_que_ja_foi_lido(self):
        """A garantia tem de estar no código, não no README. O radar aprendeu
        isso perdendo 2.500 registros a uma exceção que ninguém capturava."""
        t = TransporteFalso(quebrar_em=4)   # 1 de /entes + 3 de /rgf, depois cai
        self.rodar("ingerir-entes", transporte=t)
        codigo = self.rodar("ingerir", "--limite", "10", transporte=t)
        self.assertEqual(codigo, 1, "execução interrompida deveria sair com 1")
        with abrir(self.banco) as con:
            gravados = con.execute("SELECT COUNT(*) FROM pessoal").fetchone()[0]
            coleta = con.execute("SELECT * FROM coleta ORDER BY rowid DESC").fetchone()
        self.assertGreater(gravados, 0, "não preservou o que já tinha lido")
        self.assertIsNotNone(coleta["falhou_com"])

    def test_e_a_retomada_continua_de_onde_parou(self):
        quebrado = TransporteFalso(quebrar_em=4)
        self.rodar("ingerir-entes", transporte=quebrado)
        self.rodar("ingerir", "--limite", "10", transporte=quebrado)
        with abrir(self.banco) as con:
            parcial = con.execute("SELECT COUNT(*) FROM pessoal").fetchone()[0]
        bom = TransporteFalso()
        self.rodar("ingerir", "--limite", "10", transporte=bom)
        with abrir(self.banco) as con:
            total = con.execute("SELECT COUNT(*) FROM pessoal").fetchone()[0]
        self.assertGreater(total, parcial)
        self.assertEqual(bom.pedidos_rgf, total - parcial,
                         "a retomada pediu de novo o que já estava gravado")

    def test_quem_nao_publicou_e_gravado_e_nao_e_perguntado_de_novo(self):
        t = TransporteFalso(rgf=fixture("rgf_vazio.json"))
        self.rodar("ingerir-entes", transporte=t)
        self.rodar("ingerir", transporte=t)
        with abrir(self.banco) as con:
            linhas = con.execute("SELECT publicou FROM pessoal").fetchall()
        self.assertTrue(linhas)
        self.assertTrue(all(r["publicou"] == 0 for r in linhas))
        antes = t.pedidos_rgf
        self.rodar("ingerir", transporte=t)
        self.assertEqual(t.pedidos_rgf, antes,
                         "perguntou de novo a quem já se sabia que não publicou")

    def test_resumo_e_listar_nao_estouram_com_banco_vazio(self):
        t = TransporteFalso()
        self.assertEqual(self.rodar("resumo", transporte=t), 0)
        self.assertEqual(self.rodar("listar", transporte=t), 0)


if __name__ == "__main__":
    unittest.main()
