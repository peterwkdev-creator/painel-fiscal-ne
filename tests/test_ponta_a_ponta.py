"""O CLI inteiro, com transporte e relógio falsos.

Existe porque idempotência e retomada testadas só no armazém provam metade: o
que se quer garantir é que **rodar o comando de novo** não gasta requisição
repetida nem muda número. Isso só o caminho completo mostra.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from fiscal.armazem import abrir, gravar_entes
from fiscal.cli import RECORTES, principal
from fiscal.siconfi import FALHA_DE_REDE, Ente, Resposta

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
        """Roda o comando e **engole a saída**, devolvendo o código de retorno.

        O CLI imprime progresso de propósito — uma varredura de 57 minutos sem
        sinal de vida é indistinguível de uma travada. Mas numa suíte isso
        despejava 40 linhas no terminal, e o custo não é estético: no histórico
        de um terminal, um `ATENÇÃO: banco incompleto` de verdade fica
        indistinguível do mesmo aviso vindo de uma fixture de 20 municípios.
        As outras três suítes do workspace não imprimem nada; esta era a única.

        A saída fica disponível em `self.saida` para quem quiser afirmar sobre
        ela — engolir não é o mesmo que descartar.

        **O `stderr` também**, e ele estava de fora: o relato de interrupção
        (`>>> N municípios gravados`) sai por lá, e escapava da suíte inteira.
        Uma linha só, e por isso ninguém via — mas a promessa era silêncio.
        """
        buffer, erros = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(erros):
            codigo = principal(["--banco", self.banco, *argv],
                               transporte=transporte, dormir=lambda _: None)
        self.saida = buffer.getvalue()
        self.erros = erros.getvalue()
        return codigo

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


class TestUniversoConferido(unittest.TestCase):
    """O aviso de banco incompleto, que ninguém exercitava.

    A versão anterior decidia o universo esperado a partir da própria contagem
    (`5570 if universo > 1793 else 1793`), com os dois números soltos no código
    em vez de saírem de `RECORTES`.

    **E ela não estava errada em nenhuma entrada alcançável hoje** — o canário
    mostrou isso: com só dois recortes, as duas versões concordam em tudo. O
    que ela quebra é o recorte SEGUINTE, e é o que
    `test_um_recorte_novo_nao_dispara_aviso_falso` prova.

    A lição de escrever isto: **contrato igual não prova mudança.** Os quatro
    primeiros testes aqui descrevem o comportamento certo e passam nas duas
    versões; só o quinto distingue. Um teste que não reprova o código antigo
    não defende a correção — documenta o que já funcionava.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.banco = str(Path(self.dir.name) / "teste.db")

    def tearDown(self):
        self.dir.cleanup()

    def conferir(self) -> str:
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            principal(["--banco", self.banco, "conferir"],
                      transporte=TransporteFalso(), dormir=lambda _: None)
        return saida.getvalue()

    def _gravar_entes(self, quantos: int) -> None:
        """Usa `gravar_entes`, o mesmo caminho da produção.

        Montar o INSERT à mão faria o teste conhecer o esquema por conta
        própria — e passar a quebrar a cada coluna nova por um motivo que não
        tem nada a ver com o que ele afirma.
        """
        entes = [
            Ente(codigo_ibge=900000 + i, nome=f"M{i}", uf="XX",
                 regiao="Teste", esfera="M", populacao=None, cnpj=None)
            for i in range(quantos)
        ]
        with abrir(self.banco) as con:
            gravar_entes(con, entes)
            con.commit()

    def test_avisa_quando_o_universo_nao_e_de_nenhum_recorte(self):
        self._gravar_entes(20)
        self.assertIn("ATENÇÃO", self.conferir())

    def test_nao_avisa_no_tamanho_do_NORDESTE(self):
        self._gravar_entes(RECORTES["NE"][1])
        self.assertNotIn("ATENÇÃO", self.conferir())

    def test_nao_avisa_no_tamanho_do_BRASIL(self):
        self._gravar_entes(RECORTES["BR"][1])
        self.assertNotIn("ATENÇÃO", self.conferir())

    def test_um_recorte_novo_nao_dispara_aviso_falso(self):
        """**O teste que justifica a mudança**, e o único que a versão anterior
        reprova.

        A versão anterior era `5570 if universo > 1793 else 1793`: ela decidia
        o esperado a partir da própria contagem, com os dois números soltos no
        código. Num recorte de um estado — 645 municípios em São Paulo — ela
        concluiria "esperado 1793" e avisaria **banco incompleto num banco
        completo**. Aviso falso é pior que aviso nenhum: ensina a ignorar.

        Escrito depois de o canário mostrar que os outros testes deste caso
        **passavam nas duas versões**. Contrato igual não prova mudança; era
        preciso o caso em que elas divergem.
        """
        recorte_novo = ("SP", (["SP"], 645))
        RECORTES[recorte_novo[0]] = recorte_novo[1]
        try:
            self._gravar_entes(645)
            self.assertNotIn("ATENÇÃO", self.conferir(),
                             "avisou banco incompleto num recorte completo")
        finally:
            del RECORTES[recorte_novo[0]]

    def test_contagem_intermediaria_sempre_avisa(self):
        """Varredura interrompida é o caso comum, e tem de gritar."""
        for parcial in (1792, 1794, 3000, 5569):
            with self.subTest(universo=parcial):
                self.dir.cleanup()
                self.dir = tempfile.TemporaryDirectory()
                self.banco = str(Path(self.dir.name) / "teste.db")
                self._gravar_entes(parcial)
                self.assertIn("ATENÇÃO", self.conferir(),
                              f"{parcial} municípios passaram sem aviso")
