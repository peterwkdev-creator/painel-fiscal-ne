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
        declaradas = set(re.findall(
            r"cobertura: \{(.*?)\};", self.ts, re.S)[0].split("\n")[0:0] or [])
        corpo = re.search(r"cobertura: \{(.*?)\n  \};", self.ts, re.S).group(1)
        declaradas = set(re.findall(r"(\w+):", corpo))
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

    def test_a_diferenca_para_o_ibge_e_exibida_nao_escondida(self):
        self.assertEqual(
            self.snapshot["cobertura"]["municipiosIbgeNoNordeste"], 1794)


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
        self.assertEqual((self.f["exercicio"], self.f["periodo"]), (2024, 6))

    def test_os_rotulos_saem_ordenados_pela_soma_no_nordeste(self):
        # Educação 200, Saúde 190, Urbanismo 60 -- somando os dois municípios.
        self.assertEqual(self.f["rotulos"], ["Educação", "Saúde", "Urbanismo"])

    def test_quem_nao_publicou_fica_fora_do_mapa_mas_conta_na_cobertura(self):
        self.assertNotIn("2111300", self.f["porMunicipio"])
        self.assertEqual(self.f["cobertura"],
                         {"consultados": 3, "publicaram": 2, "naoFecham": 0})

    def test_o_valor_e_endereçado_por_indice_no_array_de_rotulos(self):
        total, valores = self.f["porMunicipio"]["2507507"]
        self.assertEqual(total, 150)
        nomes = {self.f["rotulos"][i]: v for i, v in valores}
        self.assertEqual(nomes, {"Saúde": 90, "Urbanismo": 60})

    def test_a_soma_das_partes_fecha_com_o_total_declarado(self):
        """A garantia que a fonte oferece de graça — e que o arredondamento
        para reais inteiros não pode estragar."""
        for codigo, (total, valores) in self.f["porMunicipio"].items():
            self.assertEqual(sum(v for _, v in valores), total,
                             f"a soma não fecha em {codigo}")

    def test_o_bimestre_anterior_nao_vaza_para_dentro_do_atual(self):
        """Salvador declarou os dois bimestres. Misturá-los somaria o mesmo
        gasto duas vezes, e o total declarado deixaria de conferir."""
        total, valores = self.f["porMunicipio"]["2927408"]
        self.assertEqual(total, 300)
        self.assertEqual(len(valores), 2)

    def test_sem_varredura_a_chave_existe_e_vale_null(self):
        self.assertIsNone(ContratoEntreLinguagens.snapshot["funcoes"])


if __name__ == "__main__":
    unittest.main()
