"""Contrato entre o motor Python e o painel TypeScript.

O snapshot é a única costura entre as duas linguagens, e é uma costura que
**quebra em silêncio**: renomear um campo no export não quebra o build do Next,
não quebra o TypeScript, e não estoura no navegador. Só produz uma página com
travessões no lugar dos números, e ninguém desconfia.

Este teste lê `painel/lib/dados.ts` como texto e compara o que o TypeScript
**declara** com o que o Python **exporta**. Se os dois se afastarem, falha aqui.
"""

import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fiscal.armazem import abrir, gravar_entes, gravar_funcoes, gravar_pessoal
from fiscal.siconfi import Ente, Funcoes, Pessoal

RAIZ = Path(__file__).resolve().parent.parent
DADOS_TS = RAIZ / "painel" / "lib" / "dados.ts"


def campos_da_interface(texto: str, nome: str) -> set[str]:
    """As chaves de primeiro nível declaradas numa `interface`."""
    corpo = re.search(rf"export interface {nome} \{{(.*?)\n\}}", texto, re.S)
    if not corpo:
        raise AssertionError(f"interface {nome} não encontrada em dados.ts")
    return set(re.findall(r"^\s{2}(\w+)\??:", corpo.group(1), re.M))


def rotulos_da_tupla(texto: str, nome: str) -> list[str]:
    """Os nomes dos elementos do tipo-tupla, na ordem declarada."""
    corpo = re.search(rf"export type {nome} = \[(.*?)\n\];", texto, re.S)
    if not corpo:
        raise AssertionError(f"type {nome} não encontrado em dados.ts")
    return re.findall(r"^\s{2}(\w+):", corpo.group(1), re.M)


class ContratoEntreLinguagens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ts = DADOS_TS.read_text(encoding="utf-8")
        cls.dir = tempfile.TemporaryDirectory()
        banco = str(Path(cls.dir.name) / "c.db")
        saida = Path(cls.dir.name) / "snapshot.json"
        with abrir(banco) as con:
            gravar_entes(con, [
                Ente(2927408, "Salvador", "BA", "NE", "M", 2610987, "1"),
                Ente(2507507, "João Pessoa", "PB", "NE", "M", 833932, "2"),
                Ente(2111300, "São Luís", "MA", "NE", "M", 1037775, "3"),
            ])
            gravar_pessoal(con, 2927408, 2024, 3, Pessoal(
                2927408, 2024, 3, 1.04e10, 1.02e10, 3.3e9, 32.37, 51.3))
            gravar_pessoal(con, 2507507, 2024, 3, Pessoal(
                2507507, 2024, 3, 1e9, 9.8e8, 5.2e8, 52.69, 51.3))
            gravar_pessoal(con, 2111300, 2024, 3, None)   # não publicou
        subprocess.run(
            [sys.executable, "-m", "fiscal", "--banco", banco, "exportar",
             "--exercicio", "2024", "--periodo", "3", "--saida", str(saida)],
            cwd=RAIZ, check=True, capture_output=True)
        cls.snapshot = json.loads(saida.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_as_chaves_do_topo_sao_exatamente_as_declaradas(self):
        self.assertEqual(set(self.snapshot), campos_da_interface(self.ts, "Snapshot"))

    def test_as_chaves_de_cobertura_sao_exatamente_as_declaradas(self):
        corpo = re.search(r"cobertura: \{(.*?)\n  \};", self.ts, re.S).group(1)
        # **Chave é o que ABRE a linha**, com indentação -- não qualquer
        # `palavra:` no meio do bloco. A versão anterior usava `(\w+):` solto e
        # em 03/09/2026 leu a palavra "literal:" de um comentário JSDoc como se
        # fosse campo do contrato, reprovando um snapshot correto. Teste que
        # falha por prosa é teste que ensina a ignorá-lo.
        declaradas = set(re.findall(r"^\s+(\w+)\??:", corpo, re.M))
        self.assertEqual(set(self.snapshot["cobertura"]), declaradas)

    def test_a_ordem_das_colunas_bate_com_a_tupla_do_typescript(self):
        """A parte mais frágil: o snapshot é posicional.

        Trocar duas colunas de lugar no Python não quebra nada em lugar nenhum
        — só passa a mostrar população onde deveria estar o percentual.
        """
        self.assertEqual(self.snapshot["colunas"],
                         rotulos_da_tupla(self.ts, "LinhaMunicipio"))

    def test_cada_linha_tem_o_tamanho_da_tupla(self):
        esperado = len(rotulos_da_tupla(self.ts, "LinhaMunicipio"))
        for linha in self.snapshot["municipios"]:
            self.assertEqual(len(linha), esperado)

    def test_o_municipio_expandido_tem_os_campos_da_interface(self):
        # `expandir()` no TS produz `Municipio` a partir da tupla; os dois
        # conjuntos de nomes têm de ser o mesmo.
        self.assertEqual(set(rotulos_da_tupla(self.ts, "LinhaMunicipio")),
                         campos_da_interface(self.ts, "Municipio"))


class OQueOSnapshotPromete(unittest.TestCase):
    """Garantias do conteúdo, não só do formato."""

    @classmethod
    def setUpClass(cls):
        cls.snapshot = ContratoEntreLinguagens.snapshot

    def test_quem_nao_publicou_vem_como_false_e_nao_como_zero(self):
        i = self.snapshot["colunas"].index("publicou")
        j = self.snapshot["colunas"].index("percentual")
        nao = [l for l in self.snapshot["municipios"] if l[i] is False]
        self.assertTrue(nao, "o corpus de teste precisa de um que não publicou")
        for l in nao:
            self.assertIsNone(l[j], "ausência virou número")

    def test_a_fonte_e_a_data_de_coleta_viajam_com_o_dado(self):
        self.assertIn("SICONFI", self.snapshot["fonte"])
        self.assertTrue(self.snapshot["coletadoEm"])

    def test_os_limites_da_lei_estao_no_snapshot(self):
        self.assertEqual(self.snapshot["limites"]["prudencial"], 51.3)
        self.assertEqual(self.snapshot["limites"]["legal"], 54.0)

    def test_a_diferenca_para_o_ibge_e_derivada_e_nao_cravada(self):
        """O IBGE conta exatamente **um a mais** que o SICONFI, nos dois
        recortes, e esse um é Fernando de Noronha.

        A versão anterior exigia o literal `1794`: passava no Nordeste, e teria
        deixado a expansão nacional publicar "1794" ao lado de 5.570 municípios
        — uma diferença de 3.776 que não existe, com a aparência de um número
        conferido.
        """
        c = self.snapshot["cobertura"]
        self.assertEqual(c["municipiosIbge"], c["universo"] + 1)


class DespesaPorFuncaoNoSnapshot(unittest.TestCase):
    """O bloco `funcoes`: formato esparso, escala do período, e o total como régua.

    Vive num banco próprio porque a fixture de cima **não** tem funções — e é
    ela que prova a outra metade do contrato: sem varredura, `funcoes` é `null`
    e a chave continua existindo.
    """

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        banco = str(Path(cls.dir.name) / "f.db")
        saida = Path(cls.dir.name) / "snapshot.json"
        with abrir(banco) as con:
            gravar_entes(con, [
                Ente(2927408, "Salvador", "BA", "NE", "M", 2610987, "1"),
                Ente(2507507, "João Pessoa", "PB", "NE", "M", 833932, "2"),
                Ente(2111300, "São Luís", "MA", "NE", "M", 1037775, "3"),
            ])
            # Bimestre 5 e bimestre 6 do mesmo exercício: o export tem de pegar
            # o 6, o mais recente, e não o que `--periodo` disser.
            gravar_funcoes(con, 2927408, 2024, 5, Funcoes(
                2927408, 2024, 5, 100.0, {"Saúde": 60.0, "Educação": 40.0}))
            gravar_funcoes(con, 2927408, 2024, 6, Funcoes(
                2927408, 2024, 6, 300.0, {"Educação": 200.0, "Saúde": 100.0}))
            gravar_funcoes(con, 2507507, 2024, 6, Funcoes(
                2507507, 2024, 6, 150.0, {"Saúde": 90.0, "Urbanismo": 60.0}))
            gravar_funcoes(con, 2111300, 2024, 6, None)   # consultado, não publicou
        subprocess.run(
            [sys.executable, "-m", "fiscal", "--banco", banco, "exportar",
             "--exercicio", "2024", "--periodo", "3", "--saida", str(saida)],
            cwd=RAIZ, check=True, capture_output=True)
        cls.f = json.loads(saida.read_text(encoding="utf-8"))["funcoes"]

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_o_periodo_vem_do_banco_e_nao_do_argumento(self):
        """`--periodo 3` era quadrimestre do RGF; aqui a escala é outra.

        Se o export lesse o argumento, este teste devolveria o 3º bimestre —
        que não existe no banco — em vez do 6º. As duas escalas coexistem no
        mesmo número e é exatamente aí que o erro passa despercebido.
        """
        self.assertEqual(
            (self.f["exercicios"][0]["exercicio"], self.f["periodo"]), (2024, 6))

    def test_os_rotulos_saem_ordenados_pela_soma_no_nordeste(self):
        # Educação 200, Saúde 190, Urbanismo 60 -- somando os dois municípios.
        self.assertEqual(self.f["rotulos"], ["Educação", "Saúde", "Urbanismo"])

    def test_quem_nao_publicou_fica_fora_do_mapa_mas_conta_na_cobertura(self):
        atual = self.f["exercicios"][0]
        self.assertNotIn("2111300", atual["porMunicipio"])
        self.assertEqual(atual["cobertura"],
                         {"consultados": 3, "publicaram": 2, "naoFecham": 0})

    def test_o_valor_e_endereçado_por_indice_no_array_de_rotulos(self):
        total, valores = self.f["exercicios"][0]["porMunicipio"]["2507507"]
        self.assertEqual(total, 150)
        nomes = {self.f["rotulos"][i]: v for i, v in valores}
        self.assertEqual(nomes, {"Saúde": 90, "Urbanismo": 60})

    def test_a_soma_das_partes_fecha_com_o_total_declarado(self):
        """A garantia que a fonte oferece de graça — e que o arredondamento
        para reais inteiros não pode estragar."""
        for e in self.f["exercicios"]:
            for codigo, (total, valores) in e["porMunicipio"].items():
                self.assertEqual(sum(v for _, v in valores), total,
                                 f"a soma não fecha em {codigo} ({e['exercicio']})")

    def test_o_bimestre_anterior_nao_vaza_para_dentro_do_atual(self):
        """Salvador declarou os dois bimestres. Misturá-los somaria o mesmo
        gasto duas vezes, e o total declarado deixaria de conferir."""
        total, valores = self.f["exercicios"][0]["porMunicipio"]["2927408"]
        self.assertEqual(total, 300)
        self.assertEqual(len(valores), 2)

    def test_sem_varredura_a_chave_existe_e_vale_null(self):
        self.assertIsNone(ContratoEntreLinguagens.snapshot["funcoes"])


class ComparacaoEntreAnos(unittest.TestCase):
    """A série é o MESMO bimestre em anos diferentes — nunca o período anterior.

    A distinção não é preciosismo: o RREO é acumulado no ano, então o 6º
    bimestre **contém** o 4º (mediana da razão b4/b6 medida em 0,629). Usar o
    período anterior como comparação produziria deslocamentos de fatia com
    mediana de 0,96 pp, contra 1,67 pp entre anos — ruído vestido de tendência.
    """

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        banco = str(Path(cls.dir.name) / "c.db")
        saida = Path(cls.dir.name) / "s.json"
        with abrir(banco) as con:
            gravar_entes(con, [Ente(2927408, "Salvador", "BA", "NE", "M", 1, "1")])
            # 2023/6 é a comparação certa. 2024/4 é o período anterior e está
            # aqui justamente para provar que ele NÃO é escolhido.
            gravar_funcoes(con, 2927408, 2023, 6, Funcoes(
                2927408, 2023, 6, 100.0, {"Educação": 50.0, "Cultura": 50.0}))
            gravar_funcoes(con, 2927408, 2024, 4, Funcoes(
                2927408, 2024, 4, 80.0, {"Educação": 80.0}))
            gravar_funcoes(con, 2927408, 2024, 6, Funcoes(
                2927408, 2024, 6, 200.0, {"Educação": 150.0, "Saúde": 50.0}))
        subprocess.run(
            [sys.executable, "-m", "fiscal", "--banco", banco, "exportar",
             "--exercicio", "2024", "--periodo", "3", "--saida", str(saida)],
            cwd=RAIZ, check=True, capture_output=True)
        cls.f = json.loads(saida.read_text(encoding="utf-8"))["funcoes"]

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_a_serie_e_do_mesmo_bimestre_em_anos_diferentes(self):
        self.assertEqual(self.f["periodo"], 6)
        self.assertEqual([e["exercicio"] for e in self.f["exercicios"]], [2024, 2023],
            "escolheu 2024/4, que é o período anterior mas está CONTIDO em 2024/6")

    def test_a_serie_vem_do_mais_recente_para_o_mais_antigo(self):
        """Quem quer só a foto usa `exercicios[0]`, e a ordem é o que torna
        isso verdade sem cada chamador reordenar por conta."""
        anos = [e["exercicio"] for e in self.f["exercicios"]]
        self.assertEqual(anos, sorted(anos, reverse=True))

    def test_o_periodo_intermediario_nao_entra_em_lugar_nenhum(self):
        """2024/4 foi coletado e não pode vazar nem para o destaque nem para a
        comparação — o total de 80 denunciaria."""
        totais = [e["porMunicipio"]["2927408"][0] for e in self.f["exercicios"]]
        self.assertEqual(totais, [200, 100])
        self.assertNotIn(80, totais, "2024/4 vazou para a série")

    def test_os_dois_periodos_compartilham_o_array_de_rotulos(self):
        """Índices que significassem funções diferentes em cada ano trocariam
        educação por saúde na comparação, sem nada estourar."""
        rot = self.f["rotulos"]
        leitura = [{rot[i]: v for i, v in e["porMunicipio"]["2927408"][1]}
                   for e in self.f["exercicios"]]
        self.assertEqual(leitura[0], {"Educação": 150, "Saúde": 50})
        self.assertEqual(leitura[1], {"Educação": 50, "Cultura": 50})

    def test_funcao_que_so_existe_no_ano_anterior_ganha_indice(self):
        """Cultura sumiu em 2024. Sem índice para ela, a linha de 2023 seria
        descartada em silêncio e a soma do ano anterior deixaria de fechar."""
        self.assertIn("Cultura", self.f["rotulos"])
        total, valores = self.f["exercicios"][1]["porMunicipio"]["2927408"]
        self.assertEqual(sum(v for _, v in valores), total)

    def test_tres_exercicios_viram_tres_pontos_de_serie(self):
        """O caso que a forma anterior não comportava.

        Com "atual" mais "anterior", um terceiro ano exigiria um terceiro
        formato ou repetiria o mesmo ano em dois lugares do arquivo -- e dado
        repetido é dado que diverge. Este teste é a razão de a lista existir.
        """
        with tempfile.TemporaryDirectory() as d:
            banco = str(Path(d) / "tres.db")
            saida = Path(d) / "s.json"
            with abrir(banco) as con:
                gravar_entes(con, [Ente(2927408, "Salvador", "BA", "NE", "M", 1, "1")])
                for ex, valor in ((2022, 60.0), (2023, 80.0), (2024, 100.0)):
                    gravar_funcoes(con, 2927408, ex, 6, Funcoes(
                        2927408, ex, 6, valor, {"Educação": valor}))
            subprocess.run(
                [sys.executable, "-m", "fiscal", "--banco", banco, "exportar",
                 "--exercicio", "2024", "--periodo", "3", "--saida", str(saida)],
                cwd=RAIZ, check=True, capture_output=True)
            f = json.loads(saida.read_text(encoding="utf-8"))["funcoes"]

        self.assertEqual([e["exercicio"] for e in f["exercicios"]],
                         [2024, 2023, 2022])
        self.assertEqual([e["porMunicipio"]["2927408"][0] for e in f["exercicios"]],
                         [100, 80, 60])
        # Cada exercício carrega a SUA cobertura: um ano varrido pela metade
        # não pode herdar a cobertura do ano completo.
        for e in f["exercicios"]:
            self.assertEqual(e["cobertura"]["consultados"], 1)

    def test_exercicio_varrido_pela_metade_fica_FORA_da_serie(self):
        """O canário desta frente.

        Um ano incompleto na série separa os municípios em dois grupos
        indistinguíveis: os que não entregaram naquele ano, e os que ainda não
        perguntamos. A página diria "não tem 2022" sobre quem tem, e o buraco
        na linha temporal se leria como interrupção do serviço.

        É a mesma distinção da faixa `nao-consultado`, e aqui ela é pior:
        ausência de coleta disfarçada de ausência de gasto.
        """
        with tempfile.TemporaryDirectory() as d:
            banco = str(Path(d) / "parcial.db")
            saida = Path(d) / "s.json"
            with abrir(banco) as con:
                gravar_entes(con, [
                    Ente(2927408, "Salvador", "BA", "NE", "M", 1, "1"),
                    Ente(2507507, "João Pessoa", "PB", "NE", "M", 1, "1"),
                ])
                # 2024 completo: os dois entes consultados.
                for cod in (2927408, 2507507):
                    gravar_funcoes(con, cod, 2024, 6, Funcoes(
                        cod, 2024, 6, 100.0, {"Educação": 100.0}))
                # 2023 pela METADE: só um dos dois.
                gravar_funcoes(con, 2927408, 2023, 6, Funcoes(
                    2927408, 2023, 6, 90.0, {"Educação": 90.0}))
            subprocess.run(
                [sys.executable, "-m", "fiscal", "--banco", banco, "exportar",
                 "--exercicio", "2024", "--periodo", "3", "--saida", str(saida)],
                cwd=RAIZ, check=True, capture_output=True)
            f = json.loads(saida.read_text(encoding="utf-8"))["funcoes"]

        self.assertEqual([e["exercicio"] for e in f["exercicios"]], [2024],
                         "2023 foi varrido pela metade e não pode entrar")

    def test_com_um_ano_so_a_serie_tem_um_elemento(self):
        """Fixture própria de propósito: depender do `setUpClass` de outra
        classe amarra a ordem de execução do unittest, que não é garantida."""
        with tempfile.TemporaryDirectory() as d:
            banco = str(Path(d) / "so2024.db")
            saida = Path(d) / "s.json"
            with abrir(banco) as con:
                gravar_entes(con, [Ente(2927408, "Salvador", "BA", "NE", "M", 1, "1")])
                gravar_funcoes(con, 2927408, 2024, 6, Funcoes(
                    2927408, 2024, 6, 10.0, {"Educação": 10.0}))
            subprocess.run(
                [sys.executable, "-m", "fiscal", "--banco", banco, "exportar",
                 "--exercicio", "2024", "--periodo", "3", "--saida", str(saida)],
                cwd=RAIZ, check=True, capture_output=True)
            f = json.loads(saida.read_text(encoding="utf-8"))["funcoes"]
        self.assertEqual(len(f["exercicios"]), 1, "não há 2023/6 para comparar")
        self.assertEqual(f["exercicios"][0]["exercicio"], 2024)


if __name__ == "__main__":
    unittest.main()
