"""O cliente do SICONFI, sem tocar a rede e sem esperar de verdade.

Cada classe aqui cobra um critério de aceite de `especs/painel-fiscal.md`.
As fixtures são **respostas reais capturadas** em 03/09/2026.
"""

import json
import unittest
from pathlib import Path

from fiscal.siconfi import (
    FALHA_DE_REDE, NORDESTE, ErroSiconfi, Ente, Resposta,
    buscar, entes, pessoal, url_rgf,
)

FIX = Path(__file__).parent / "fixtures"


def fixture(nome: str) -> dict:
    return json.loads((FIX / nome).read_text(encoding="utf-8"))["resposta"]


def transporte_fixo(*respostas: Resposta):
    """Devolve as respostas em ordem, e anota as URLs pedidas."""
    fila = list(respostas)
    pedidas: list[str] = []

    def t(url: str) -> Resposta:
        pedidas.append(url)
        return fila.pop(0) if len(fila) > 1 else fila[0]

    t.pedidas = pedidas  # type: ignore[attr-defined]
    return t


def ok(corpo: dict) -> Resposta:
    return Resposta(200, json.dumps(corpo))


class TestVazioNaoEErroNemZero(unittest.TestCase):
    """A armadilha central desta API, e a razão desta suíte existir.

    `/rgf` sem `id_ente` responde **HTTP 200 com `items: []`**. Quem tratar como
    erro repete para sempre; quem tratar como dado grava zero onde não há
    informação. É ausência, e ausência tem de chegar como `None`.
    """

    def test_items_vazio_devolve_none(self):
        t = transporte_fixo(ok(fixture("rgf_vazio.json")))
        self.assertIsNone(pessoal(2927408, 2024, 3, t, dormir=lambda _: None))

    def test_e_nao_levanta(self):
        t = transporte_fixo(ok(fixture("rgf_vazio.json")))
        try:
            pessoal(2927408, 2024, 3, t, dormir=lambda _: None)
        except Exception as e:  # pragma: no cover
            self.fail(f"resposta vazia levantou {type(e).__name__}: {e}")

    def test_e_nao_repete(self):
        # Repetir uma resposta legítima é gastar uma hora de varredura à toa.
        t = transporte_fixo(ok(fixture("rgf_vazio.json")))
        pessoal(2927408, 2024, 3, t, dormir=lambda _: None)
        self.assertEqual(len(t.pedidas), 1)


class TestLeituraDoPlanoDeContas(unittest.TestCase):
    """`DespesaComPessoalTotal` aparece DUAS vezes na mesma resposta, separada
    só pela coluna. Ler apenas pelo `cod_conta` pega a linha errada metade das
    vezes -- e o erro seria silencioso, porque as duas linhas têm número."""

    def setUp(self):
        t = transporte_fixo(ok(fixture("rgf_salvador.json")))
        self.p = pessoal(2927408, 2024, 3, t, dormir=lambda _: None)

    def test_percentual_e_o_percentual_nao_o_valor_em_reais(self):
        self.assertEqual(self.p.percentual, 32.37)

    def test_despesa_e_o_valor_em_reais_nao_o_percentual(self):
        self.assertEqual(self.p.despesa, 3318008507.92)

    def test_rcl(self):
        self.assertEqual(self.p.rcl, 10384311525.53)

    def test_limite_prudencial_do_proprio_ente(self):
        self.assertEqual(self.p.limite_prudencial, 51.3)

    def test_salvador_esta_abaixo_do_prudencial(self):
        self.assertIs(self.p.acima_do_prudencial, False)

    def test_le_a_rcl_ajustada_que_e_diferente_da_bruta(self):
        # Dois números parecidos e diferentes na mesma resposta. Confundi-los
        # não estoura nada: só faz toda conferência divergir um pouquinho.
        self.assertEqual(self.p.rcl, 10384311525.53)
        self.assertEqual(self.p.rcl_ajustada, 10250806767.53)
        self.assertNotEqual(self.p.rcl, self.p.rcl_ajustada)

    def test_o_percentual_declarado_bate_com_despesa_sobre_rcl_AJUSTADA(self):
        """A prova de que o denominador certo é a ajustada.

        `fiscal conferir` usava a RCL bruta e divergia em TODOS os municípios,
        sempre no mesmo sentido -- e foi assim que denunciou o erro de leitura
        de quem o escreveu. Divergência num sentido só nunca é acaso.
        """
        calculado = self.p.despesa / self.p.rcl_ajustada * 100
        self.assertAlmostEqual(calculado, self.p.percentual, places=2)

    def test_e_NAO_bate_com_a_rcl_bruta(self):
        bruto = self.p.despesa / self.p.rcl * 100
        self.assertGreater(abs(bruto - self.p.percentual), 0.3)


class TestAcimaDoPrudencial(unittest.TestCase):
    """João Pessoa/PB marcou 52,69% em 2024/3 contra um prudencial de 51,3% --
    número real, colhido da API em 03/09/2026. É o caso que o painel existe
    para mostrar."""

    def test_percentual_acima_do_limite(self):
        from fiscal.siconfi import Pessoal
        p = Pessoal(2507507, 2024, 3, rcl=None, rcl_ajustada=None,
                    despesa=None, percentual=52.69, limite_prudencial=51.3)
        self.assertIs(p.acima_do_prudencial, True)

    def test_sem_um_dos_lados_a_resposta_e_desconhecido(self):
        # "Não sei" não pode virar "está abaixo".
        from fiscal.siconfi import Pessoal
        p = Pessoal(1, 2024, 3, rcl=None, rcl_ajustada=None,
                    despesa=None, percentual=None, limite_prudencial=51.3)
        self.assertIsNone(p.acima_do_prudencial)


class TestUniversoDoNordeste(unittest.TestCase):
    """`/entes` cabe numa requisição só, e o filtro tem de descartar o resto."""

    def test_uma_requisicao_basta(self):
        t = transporte_fixo(ok(fixture("entes_amostra.json")))
        entes(t, dormir=lambda _: None)
        self.assertEqual(len(t.pedidas), 1)

    def test_filtra_por_uf_e_por_esfera(self):
        t = transporte_fixo(ok(fixture("entes_amostra.json")))
        achados = entes(t, dormir=lambda _: None, uf=NORDESTE, esfera="M")
        self.assertTrue(achados)
        self.assertTrue(all(e.uf in NORDESTE and e.esfera == "M" for e in achados))

    def test_o_ne_real_tem_1793_entes_com_rgf_municipal(self):
        # Não é 1.794: Fernando de Noronha é distrito estadual de PE, não
        # município, e por isso não entrega RGF municipal. Os dois números
        # estão certos, e a diferença é exibida em vez de ajustada.
        total = json.loads((FIX / "entes_amostra.json").read_text(encoding="utf-8"))
        self.assertEqual(total["_total_real_no_ne"], 1793)


class TestFalhaDeRedeViraStatus(unittest.TestCase):
    """A regra que o radar pagou caro: `TimeoutError` de socket é `OSError`,
    não `URLError`, e por essa fresta um erro atravessou todo o backoff."""

    def test_repete_e_depois_desiste_com_mensagem_util(self):
        esperas = []
        t = transporte_fixo(Resposta(FALHA_DE_REDE, "TimeoutError: timed out"))
        with self.assertRaises(ErroSiconfi) as c:
            buscar("http://x", "o teste", t, dormir=esperas.append, tentativas=4, pausa=1.0)
        self.assertIn("4 tentativas", str(c.exception))
        self.assertIn(str(FALHA_DE_REDE), str(c.exception))

    def test_a_espera_dobra(self):
        esperas = []
        t = transporte_fixo(Resposta(503, "indisponivel"))
        with self.assertRaises(ErroSiconfi):
            buscar("http://x", "o teste", t, dormir=esperas.append, tentativas=4, pausa=1.0)
        self.assertEqual(esperas, [1.0, 2.0, 4.0])

    def test_status_definitivo_nao_e_repetido(self):
        # Repetir um 400 por parâmetro errado só gasta tempo e esconde a causa.
        esperas = []
        t = transporte_fixo(Resposta(400, "parametro invalido"))
        with self.assertRaises(ErroSiconfi):
            buscar("http://x", "o teste", t, dormir=esperas.append)
        self.assertEqual(esperas, [])
        self.assertEqual(len(t.pedidas), 1)

    def test_recupera_depois_de_uma_falha(self):
        t = transporte_fixo(Resposta(FALHA_DE_REDE, "caiu"), ok({"items": []}))
        d = buscar("http://x", "o teste", t, dormir=lambda _: None)
        self.assertEqual(d["items"], [])


class TestRespostaIlegivelDizOQueEstavaSendoFeito(unittest.TestCase):
    """`except: pass` é proibido; erro engolido reaparece irreconhecível."""

    def test_json_quebrado_nomeia_a_tarefa_e_preserva_a_causa(self):
        t = transporte_fixo(Resposta(200, "<html>manutencao</html>"))
        with self.assertRaises(ErroSiconfi) as c:
            buscar("http://x", "o RGF de 2927408", t, dormir=lambda _: None)
        self.assertIn("o RGF de 2927408", str(c.exception))
        self.assertIsNotNone(c.exception.__cause__)


class TestUrl(unittest.TestCase):
    def test_monta_os_parametros_verificados(self):
        u = url_rgf(2024, 3, 2927408)
        for p in ("an_exercicio=2024", "in_periodicidade=Q", "nr_periodo=3",
                  "co_tipo_demonstrativo=RGF", "co_esfera=M", "co_poder=E",
                  "id_ente=2927408"):
            self.assertIn(p, u)

    def test_quadrimestre_invalido_falha_cedo(self):
        with self.assertRaises(ValueError):
            url_rgf(2024, 4, 2927408)


if __name__ == "__main__":
    unittest.main()
