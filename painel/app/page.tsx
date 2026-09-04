import { br, expandir, faixa } from "../lib/dados";
import { lerSnapshot } from "../lib/servidor";
import Municipios from "./municipios";
import estilos from "./page.module.css";

/**
 * Server Component: lê o snapshot do disco **no build** e entrega HTML pronto.
 * Nada de busca por rede no navegador, nada de estado de carregamento.
 */
export default async function Pagina() {
  const s = await lerSnapshot();
  const municipios = expandir(s);

  const comDado = municipios.filter((m) => m.percentual !== null);
  const acimaLegal = comDado.filter((m) => faixa(m, s.limites) === "acima-legal");
  const acimaPrudencial = comDado.filter(
    (m) => faixa(m, s.limites) === "acima-prudencial",
  );
  const naoEntregaram = municipios.filter((m) => m.publicou === false);
  const naoConsultados = municipios.filter((m) => m.publicou === null);
  const implausiveis = comDado.filter((m) => faixa(m, s.limites) === "implausivel");
  // A média EXCLUI os implausíveis. Seis declarações acima de 100% -- uma delas
  // de 371% -- puxariam a média de 1.414 municípios quase um ponto inteiro, e
  // uma média contaminada por erro de preenchimento é erro factual na página,
  // não detalhe. Os seis continuam listados, marcados; só não votam na média.
  const paraMedia = comDado.filter((m) => faixa(m, s.limites) !== "implausivel");
  const media =
    paraMedia.length > 0
      ? paraMedia.reduce((soma, m) => soma + (m.percentual ?? 0), 0) / paraMedia.length
      : null;

  const quadrimestre = `${s.periodo}º quadrimestre de ${s.exercicio}`;

  return (
    <main className={estilos.pagina} id="conteudo">
      <header className={estilos.cabecalho}>
        <h1 className={estilos.titulo}>Painel Fiscal do Nordeste</h1>
        <p className={estilos.subtitulo}>
          Quanto cada município gasta com pessoal, contra o limite que a{" "}
          <strong>Lei de Responsabilidade Fiscal</strong> impõe a ele.
          Passar do limite prudencial de {br(s.limites.prudencial)}% já proíbe
          novas contratações; o teto legal é {br(s.limites.legal)}%.
        </p>
        <p className={estilos.periodo}>
          {quadrimestre} · {s.fonte}
          {s.coletadoEm ? ` · coleta em ${s.coletadoEm.slice(0, 10)}` : ""}
        </p>
      </header>

      <section className={estilos.cartoes} aria-label="Resumo">
        <div className={estilos.cartao}>
          <p className={estilos.cartaoRotulo}>Acima do teto legal</p>
          <p className={`${estilos.cartaoValor} ${estilos.alerta} tabular`}>
            {acimaLegal.length}
          </p>
          <p className={estilos.cartaoFonte}>
            mais de {br(s.limites.legal)}% da receita corrente líquida ajustada
          </p>
        </div>
        <div className={estilos.cartao}>
          <p className={estilos.cartaoRotulo}>Acima do prudencial</p>
          <p className={`${estilos.cartaoValor} ${estilos.atencao} tabular`}>
            {acimaPrudencial.length}
          </p>
          <p className={estilos.cartaoFonte}>já proibidos de contratar</p>
        </div>
        <div className={estilos.cartao}>
          <p className={estilos.cartaoRotulo}>Não entregaram o relatório</p>
          <p className={`${estilos.cartaoValor} ${estilos.ausente} tabular`}>
            {naoEntregaram.length}
          </p>
          <p className={estilos.cartaoFonte}>
            de {s.cobertura.consultados} municípios consultados
          </p>
        </div>
        <div className={estilos.cartao}>
          <p className={estilos.cartaoRotulo}>Média dos que entregaram</p>
          <p className={`${estilos.cartaoValor} tabular`}>
            {media === null ? "—" : `${br(media)}%`}
          </p>
          <p className={estilos.cartaoFonte}>
            {paraMedia.length} municípios com percentual publicado
            {implausiveis.length > 0 &&
              `, fora ${implausiveis.length} com valor implausível`}
          </p>
        </div>
      </section>

      {naoConsultados.length > 0 && (
        <p className={estilos.avisoParcial} role="status">
          <strong>Coleta parcial.</strong> {naoConsultados.length} dos{" "}
          {s.cobertura.universo} municípios ainda não foram consultados nesta
          rodada. Os números acima descrevem só o que já foi lido.
        </p>
      )}

      <Municipios
        municipios={municipios}
        limites={s.limites}
        quadrimestre={quadrimestre}
      />

      <footer className={estilos.rodape}>
        <p>
          <strong>O percentual não é recalculado aqui.</strong> Ele vem
          calculado e declarado pelo próprio município no Relatório de Gestão
          Fiscal, sobre a receita corrente líquida <em>ajustada</em>. Recalcular
          criaria uma segunda verdade que ninguém assinou.
        </p>
        <p>
          O universo é de <strong>{s.cobertura.universo}</strong> municípios com
          relatório municipal, e não os {s.cobertura.municipiosIbge} que o IBGE
          conta na mesma área. A diferença é{" "}
          <strong>Fernando de Noronha</strong>, distrito estadual de Pernambuco
          e não município: sem Executivo próprio, não entrega relatório. É
          exatamente <strong>um</strong> nos dois recortes — o do Nordeste e o
          do país. Os dois números estão certos, e a diferença fica exibida em
          vez de ajustada.
        </p>
        <p>
          Este painel não interpreta, não acusa e não declara ninguém em
          descumprimento — mostra o número publicado e o limite legal ao lado.
          Fonte: {s.fonte}. Gerado em {s.geradoEm.slice(0, 10)}.
        </p>
      </footer>
    </main>
  );
}
