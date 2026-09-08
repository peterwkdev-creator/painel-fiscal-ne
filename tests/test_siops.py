"""O cliente do SIOPS, sem tocar a rede.

A fixture é a **resposta real do TabNet capturada em 08/09/2026** para o Ceará,
gravada em latin-1 como veio do servidor -- 184 municípios, 26 exercícios.

Cada classe aqui cobra uma armadilha que foi PAGA sondando a fonte. Elas não são
hipóteses: sem qualquer uma delas a consulta responde HTTP 200 com tabela vazia,
que parece "a fonte não tem esse dado" e é erro de chamada.
"""

import unittest
from pathlib import Path

from fiscal.siops import (
    ARQUIVOS, INDICADOR_EC29, PRIMEIRO_ANO, SEM_SERIE_MUNICIPAL, ULTIMO_ANO,
    UFS, ErroSiops, Resposta, buscar, conferir_nomes, corpo_consulta,
    ler_tabela, piso_legal, resolver_ibge, serie_da_uf,
)

FIX = Path(__file__).parent / "fixtures"


def resposta_ce() -> str:
    """A fixture decodificada como o transporte real a decodifica."""
    return (FIX / "siops_ce.html").read_bytes().decode("latin-1")


def transporte_fixo(*respostas):
    """Devolve as respostas em ordem, anotando URL e corpo de cada pedido."""
    fila = list(respostas)
    pedidos = []

    def t(url, corpo=None):
        pedidos.append((url, corpo))
        return fila.pop(0) if len(fila) > 1 else fila[0]

    t.pedidos = pedidos
    return t


class TestCorpoDaConsulta(unittest.TestCase):
    """Duas armadilhas do POST, e as duas devolvem 200 com tabela vazia."""

    def test_corpo_vai_em_latin1(self):
        """A página inteira do TabNet é latin-1; o acento tem de casar byte a byte."""
        corpo = corpo_consulta()
        # í em latin-1 é 0xED. Em UTF-8 seriam dois bytes (%C3%AD) e o servidor
        # não reconheceria o campo -- respondendo 200 com nada dentro.
        self.assertIn(b"Munic%EDpios", corpo)
        self.assertNotIn(b"%C3%AD", corpo)

    def test_porcento_literal_do_indicador_vai_codificado(self):
        """O nome do indicador tem um % de verdade, que precisa virar %25.

        Escrevê-lo à mão mistura % cru com byte já codificado, e a consulta volta
        200 com tabela vazia. O valor sai do formulário e passa pelo urlencode.
        """
        self.assertIn("%", INDICADOR_EC29)
        corpo = corpo_consulta()
        self.assertIn(b"Incremento=3.2_%25R.Pr%F3prios_em_Sa%FAde-EC_29", corpo)

    def test_pede_os_26_exercicios_numa_requisicao_so(self):
        """A razão de a fonte valer a pena: 26 requisições cobrem o país."""
        self.assertEqual(len(ARQUIVOS), ULTIMO_ANO - PRIMEIRO_ANO + 1)
        self.assertEqual(corpo_consulta().count(b"Arquivos="), len(ARQUIVOS))

    def test_o_DF_nao_esta_na_varredura_municipal(self):
        """`serhist/municipio/indicDF.def` responde HTTP 502: o DF acumula as
        competências de estado e de município, e não presta contas como
        município. Pedi-lo seria uma falha por ano, para sempre."""
        self.assertEqual(len(UFS), 26)
        self.assertNotIn("DF", UFS)
        self.assertEqual(SEM_SERIE_MUNICIPAL, ("DF",))


class TestPisoLegal(unittest.TestCase):
    """Comparar a série inteira contra 15% acusaria milhares de municípios de
    descumprir uma regra que ainda não valia para eles."""

    def test_em_2000_o_piso_era_sete_por_cento(self):
        self.assertEqual(piso_legal(2000), 7.0)

    def test_entre_2001_e_2003_nao_ha_piso_nacional(self):
        """A EC 29 mandou cada ente fechar a PRÓPRIA diferença em 1/5 por ano.
        `None` é resposta -- não há régua comparável --, não lacuna."""
        for ano in (2001, 2002, 2003):
            self.assertIsNone(piso_legal(ano), ano)

    def test_de_2004_em_diante_sao_quinze_por_cento(self):
        for ano in (2004, 2012, 2025):
            self.assertEqual(piso_legal(ano), 15.0, ano)


class TestTagsNaoFechadas(unittest.TestCase):
    """As tags do TabNet não fecham: TD, TH e OPTION vêm todas abertas.

    Um leitor que case TR com o TR de fechamento acha **duas** linhas nesta
    resposta e conclui que a fonte não tem dado. A tabela tem 184 municípios.
    """

    def test_le_os_184_municipios_que_o_ler_por_TR_perde(self):
        import re
        bruto = resposta_ce()
        # A premissa, medida e não suposta: há 191 <TR> de abertura e **um só**
        # fechamento na resposta inteira. Casar par a par acha uma linha.
        self.assertGreater(len(re.findall(r"<TR", bruto)), 180)
        self.assertEqual(bruto.count("</TR>"), 1)
        ingenuo = re.findall(r"<TR[^>]*>(.*?)</TR>", bruto, re.S)
        self.assertLess(len(ingenuo), 5, "o leitor ingênuo enxerga quase nada")
        _, series = ler_tabela(bruto)
        self.assertEqual(len(series), 184)

    def test_anos_completos_e_em_ordem(self):
        anos, _ = ler_tabela(resposta_ce())
        self.assertEqual(anos, list(range(PRIMEIRO_ANO, ULTIMO_ANO + 1)))


class TestAusenciaNaoEZero(unittest.TestCase):
    """Reticências no TabNet são ausência. Gravá-las como 0 acusaria um
    município de não ter aplicado nada em saúde naquele ano."""

    def test_ano_sem_dado_fica_fora_da_serie(self):
        _, series = ler_tabela(resposta_ce())
        vazios = [s for s in series if len(s.valores) < 26]
        self.assertTrue(vazios, "a fixture precisa conter ao menos uma ausência")
        for s in vazios:
            self.assertNotIn(0.0, s.valores.values())

    def test_nenhum_valor_e_zero_por_engano(self):
        """Medido: 3 células ausentes em 4.784. Nenhuma delas vira zero."""
        _, series = ler_tabela(resposta_ce())
        total = sum(len(s.valores) for s in series)
        self.assertEqual(184 * 26 - total, 3)


class TestRodapeNaoViraDado(unittest.TestCase):
    """A última linha da tabela engole o rodapé da página -- legenda, links,
    "Voltar" -- e vinha com 45 células em vez de 27."""

    def test_ultima_linha_tem_o_mesmo_tamanho_das_outras(self):
        anos, series = ler_tabela(resposta_ce())
        self.assertLessEqual(len(series[-1].valores), len(anos))
        self.assertEqual(series[-1].nome, "Viçosa do Ceará")


class TestAnosVemDaResposta(unittest.TestCase):
    """A ordem das colunas é decisão do servidor, não do pedido.

    Assumir a ordem pedida casaria cada município com o ano do vizinho -- e a
    tabela continuaria bem formada, que é a classe de defeito que não dói onde
    nasce.
    """

    RESPOSTA = (
        "<TABLE><TR><TH>Municípios<TH>2024<TH>2023<TH>Total"
        "<TR><TD>230010 Abaiara<TD>11,00<TD>22,00<TD>16,50"
    )

    def test_mapeia_pelo_cabecalho_recebido_e_nao_pelo_pedido(self):
        anos, series = ler_tabela(self.RESPOSTA)
        self.assertEqual(anos, [2024, 2023])
        self.assertEqual(series[0].valores, {2024: 11.0, 2023: 22.0})

    def test_resposta_sem_cabecalho_levanta_dizendo_o_que_houve(self):
        with self.assertRaises(ErroSiops) as e:
            ler_tabela("<TABLE><TR><TD>nada aqui")
        self.assertIn("Municípios", str(e.exception))


class TestJuncaoComOIBGE(unittest.TestCase):
    """O SIOPS entrega o código SEM o dígito verificador: seis, não sete."""

    def test_seis_digitos_viram_os_sete_do_ibge(self):
        self.assertEqual(resolver_ibge([2300101, 2304400]),
                         {230010: 2300101, 230440: 2304400})

    def test_o_codigo_da_fixture_e_de_seis_digitos(self):
        _, series = ler_tabela(resposta_ce())
        for s in series:
            self.assertEqual(len(str(s.codigo_siops)), 6)

    def test_divergencia_de_nome_e_relatada_e_nao_corrigida(self):
        """`Itapagé` no SIOPS é `Itapajé` no IBGE -- grafia mudada em 2010.

        Casar por nome perderia esse município; corrigi-lo em silêncio esconderia
        um fato verdadeiro sobre a fonte. O leitor relata e segue.
        """
        _, series = ler_tabela(resposta_ce())
        fora = conferir_nomes(series, {230630: "Itapajé"})
        self.assertEqual(fora, [(230630, "Itapagé", "Itapajé")])
        achado = [s for s in series if s.codigo_siops == 230630][0]
        self.assertEqual(achado.nome, "Itapagé")


class TestRepeticao(unittest.TestCase):
    """Repete só o que vale a pena. Erro de programação levanta na hora."""

    def test_repete_status_repetivel_e_devolve_o_bom(self):
        t = transporte_fixo(Resposta(503, "fora"), Resposta(200, "ok"))
        esperas = []
        self.assertEqual(
            buscar("u", None, "o teste", t, dormir=esperas.append), "ok")
        self.assertEqual(esperas, [1.0])

    def test_status_definitivo_levanta_sem_repetir(self):
        t = transporte_fixo(Resposta(404, "sumiu"))
        with self.assertRaises(ErroSiops):
            buscar("u", None, "o teste", t, dormir=lambda _: None)
        self.assertEqual(len(t.pedidos), 1)


class TestSerieDaUF(unittest.TestCase):
    """A ponta a ponta do módulo, ainda sem rede."""

    def test_monta_url_e_corpo_e_devolve_a_tabela(self):
        t = transporte_fixo(Resposta(200, resposta_ce()))
        anos, series = serie_da_uf("ce", t, dormir=lambda _: None)
        url, corpo = t.pedidos[0]
        self.assertIn("indicCE.def", url)
        self.assertTrue(url.startswith("http://"), "o host não serve TLS")
        self.assertIn(b"Coluna=Ano", corpo)
        self.assertEqual(len(anos), 26)
        self.assertEqual(len(series), 184)

    def test_o_numero_reproduz_a_medicao_anterior(self):
        """Controle: a sonda de 07/09/2026 mediu a mediana do CE em 2024 como
        23,0%. Um leitor novo que discordasse disso estaria errado em algum
        lugar -- e o desacordo apareceria aqui, não em produção."""
        import statistics
        _, series = ler_tabela(resposta_ce())
        v = [s.valores[2024] for s in series if 2024 in s.valores]
        self.assertEqual(len(v), 184)
        self.assertAlmostEqual(statistics.median(v), 23.0, delta=0.1)


class TestGuardaDeEncolhimento(unittest.TestCase):
    """Uma UF que volte menor produz banco válido, coerente e MENOR.

    Nenhuma verificação de forma pega isso -- só uma de cobertura, contra o que
    havia antes. É a lição de 07/09/2026, quando 1.794 municípios entraram por
    cima de 5.571 e 3.777 páginas indexadas viraram 404.
    """

    TRES = ("<TABLE><TR><TH>Municípios<TH>2024<TH>Total"
            "<TR><TD>230010 Abaiara<TD>11,00<TD>11,00"
            "<TR><TD>230015 Acarape<TD>12,00<TD>12,00"
            "<TR><TD>230020 Acaraú<TD>13,00<TD>13,00")
    DOIS = ("<TABLE><TR><TH>Municípios<TH>2024<TH>Total"
            "<TR><TD>230010 Abaiara<TD>11,00<TD>11,00"
            "<TR><TD>230015 Acarape<TD>12,00<TD>12,00")

    def setUp(self):
        import tempfile
        from fiscal import armazem
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.banco = str(Path(self.dir.name) / "t.db")
        with armazem.abrir(self.banco) as con:
            con.executemany(
                "INSERT INTO ente (codigo_ibge, nome, uf, regiao, esfera,"
                " populacao, cnpj, visto_em) VALUES (?,?,?,?,?,?,?,?)",
                [(2300101, "Abaiara", "CE", "NE", "M", 1, None, "x"),
                 (2300150, "Acarape", "CE", "NE", "M", 1, None, "x"),
                 (2300200, "Acaraú", "CE", "NE", "M", 1, None, "x")])

    def _rodar(self, corpo, *extra):
        """A suíte não imprime: um aviso real tem de se distinguir do aviso
        que sai de uma fixture de três municípios."""
        import contextlib, io
        from fiscal.cli import principal
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            return principal(
                ["--banco", self.banco, "ingerir-saude", "--uf", "CE", *extra],
                transporte_siops=transporte_fixo(Resposta(200, corpo)),
                dormir=lambda _: None)

    def test_a_primeira_varredura_grava(self):
        self.assertEqual(self._rodar(self.TRES), 0)

    def test_a_segunda_menor_e_recusada(self):
        self.assertEqual(self._rodar(self.TRES), 0)
        self.assertEqual(self._rodar(self.DOIS), 1)

    def test_encolher_de_proposito_precisa_da_bandeira(self):
        self.assertEqual(self._rodar(self.TRES), 0)
        self.assertEqual(self._rodar(self.DOIS, "--permitir-encolher"), 0)

    def test_mesma_varredura_duas_vezes_nao_muda_nada(self):
        import sqlite3
        self.assertEqual(self._rodar(self.TRES), 0)
        con = sqlite3.connect(self.banco)
        antes = con.execute("SELECT count(*) FROM saude").fetchone()[0]
        self.assertEqual(self._rodar(self.TRES), 0)
        self.assertEqual(
            con.execute("SELECT count(*) FROM saude").fetchone()[0], antes)
        con.close()


if __name__ == "__main__":
    unittest.main()
