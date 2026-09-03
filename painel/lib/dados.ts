/**
 * Tipos e helpers do snapshot. **Módulo puro** — nada de `node:fs` aqui.
 *
 * A leitura de disco mora em `lib/servidor.ts`. A separação não é estética: no
 * projeto irmão, misturar as duas coisas quebrou o build com
 * `UnhandledSchemeError: node:path`, porque este módulo é importado também pelo
 * componente de cliente, e `node:fs` não pode ir para o navegador.
 *
 * As chaves declaradas aqui são cobradas por um teste do lado Python
 * (`tests/test_snapshot.py`): se o export e estes tipos se afastarem, o teste
 * falha antes de alguém ver uma página em branco.
 */

/** Uma linha do snapshot, no formato compacto de array. */
export type LinhaMunicipio = [
  codigo: number,
  nome: string,
  uf: string,
  populacao: number | null,
  /** `null` = ainda não consultado; `false` = consultado e não entregou. */
  publicou: boolean | null,
  percentual: number | null,
  limitePrudencial: number | null,
  despesa: number | null,
  rclAjustada: number | null,
];

export interface Snapshot {
  geradoEm: string;
  coletadoEm: string | null;
  fonte: string;
  exercicio: number;
  periodo: number;
  limites: { prudencial: number; legal: number };
  cobertura: {
    universo: number;
    consultados: number;
    publicaram: number;
    municipiosIbgeNoNordeste: number;
  };
  colunas: string[];
  municipios: LinhaMunicipio[];
  /** Ordem dos campos em cada ponto de `serie`. */
  colunasSerie: string[];
  /**
   * A série histórica por código IBGE: `{ "2927408": [[2024,2,true,33.22], ...] }`.
   *
   * Fica separada da tupla de `municipios` de propósito. Um número sozinho não
   * diz se o município está melhorando ou piorando — e é essa a pergunta que a
   * foto esconde. Entre 2024/2 e 2024/3, Salvador caiu de 33,22% para 32,37% e
   * Imperatriz subiu de 57,63% para 60,64%: mesma "situação" no cartão, dois
   * movimentos opostos.
   *
   * Só municípios que publicaram aparecem aqui; ausência já é dita pelo campo
   * `publicou` do período em destaque.
   */
  serie: Record<string, PontoSerie[]>;
  /** Todos os períodos coletados, em ordem: `[[2024,1],[2024,2],[2024,3]]`. */
  periodos: [exercicio: number, periodo: number][];
  /**
   * A despesa liquidada por função orçamentária (RREO Anexo 02).
   *
   * `null` enquanto a varredura de funções não tiver rodado. A chave existe
   * sempre — é o que o teste de contrato do lado Python compara.
   */
  funcoes: Funcoes | null;
}

/**
 * O que o município gasta por função: educação, saúde, urbanismo, os 28 nomes
 * da Portaria MOG 42/1999.
 *
 * O percentual com pessoal responde "cabe no limite?". Esta é a outra pergunta,
 * a que ninguém consegue responder olhando um percentual: **para onde vai o
 * dinheiro?**
 *
 * ## Por que esparso
 *
 * Cada município declara cerca de 14 das 28 funções. Emitir as 28 com `null`
 * nas outras dobraria o arquivo para não dizer nada. Os rótulos saem uma vez
 * só, e cada valor carrega o **índice** nesse array — ordenado pela soma no
 * Nordeste, de modo que Educação é sempre `0`.
 *
 * ## A armadilha intra-orçamentária
 *
 * No relatório de origem cada função aparece **duas vezes**: uma no total e
 * outra em "Intra-Orçamentárias" (transferências entre órgãos do próprio
 * município). Somar as duas conta o mesmo gasto duplicado. O motor Python já
 * filtra na leitura — Salvador em saúde é R$ 2,86 bi, e não os R$ 137 mi que a
 * leitura ingênua devolve.
 */
export interface Funcoes {
  exercicio: number;
  /** **Bimestre** (1..6), não quadrimestre — o RREO não usa a escala do RGF. */
  periodo: number;
  fonte: string;
  coletadoEm: string | null;
  cobertura: { consultados: number; publicaram: number; naoFecham: number };
  /** Os nomes das funções, ordenados pela soma no Nordeste. */
  rotulos: string[];
  /** Ordem dos campos de cada entrada de `porMunicipio`. */
  colunasMunicipio: string[];
  /**
   * Por código IBGE: `{ "2927408": [totalDeclarado, [[indice, valor], ...]] }`.
   * Valores em reais inteiros — centavos num orçamento municipal são ruído, e
   * cada casa decimal custa bytes em 19.500 valores.
   */
  porMunicipio: Record<string, EntradaFuncoes>;
  /**
   * O **mesmo bimestre do ano anterior**, para comparação. `null` quando não
   * foi coletado.
   *
   * Nunca o período anterior. O RREO é acumulado no ano, então o 6º bimestre
   * **contém** o 4º — a mediana da razão b4/b6, medida em 03/09/2026 sobre
   * 1.414 municípios, deu **0,629**: 63% do valor do 6º *é* o do 4º. A fatia
   * de cada função mal se mexe entre eles (deslocamento mediano de 0,96 ponto
   * percentual), e uma frase de tendência construída ali seria ruído.
   *
   * Entre o mesmo bimestre de dois anos as acumulações são disjuntas: o
   * deslocamento mediano sobe para **1,67 pp**, com 42% das comparações
   * movendo 2 pontos ou mais.
   *
   * Compartilha o array `rotulos` do bloco pai — índices que significassem
   * funções diferentes em cada ano trocariam educação por saúde na comparação.
   */
  anterior: FuncoesAnterior | null;
}

export interface FuncoesAnterior {
  exercicio: number;
  periodo: number;
  coletadoEm: string | null;
  cobertura: { consultados: number; publicaram: number; naoFecham: number };
  porMunicipio: Record<string, EntradaFuncoes>;
}

export type EntradaFuncoes = [
  total: number | null,
  valores: [indice: number, valor: number][],
];

/** Um ponto da série: exercício, quadrimestre, publicou, percentual. */
export type PontoSerie = [
  exercicio: number,
  periodo: number,
  publicou: boolean,
  percentual: number,
];

export interface Municipio {
  codigo: number;
  nome: string;
  uf: string;
  populacao: number | null;
  publicou: boolean | null;
  percentual: number | null;
  limitePrudencial: number | null;
  despesa: number | null;
  rclAjustada: number | null;
}

/** Arrays compactos viram objetos. O snapshot é compacto porque o visitante o
 *  baixa; o código é legível porque alguém o mantém. */
export function expandir(s: Snapshot): Municipio[] {
  return s.municipios.map(
    ([codigo, nome, uf, populacao, publicou, percentual,
      limitePrudencial, despesa, rclAjustada]) => ({
      codigo, nome, uf, populacao, publicou, percentual,
      limitePrudencial, despesa, rclAjustada,
    }),
  );
}

/** Número no formato brasileiro. `null` vira travessão, **nunca zero** — zero
 *  é um número, e ausência não é. */
export function br(v: number | null | undefined, casas = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("pt-BR", {
    minimumFractionDigits: casas,
    maximumFractionDigits: casas,
  });
}

export type Faixa =
  | "implausivel"
  | "acima-legal"
  | "acima-prudencial"
  | "abaixo"
  | "sem-dado";

/**
 * A faixa do plausivel: **entre 0 e 100%**.
 *
 * Acima de 100% o municipio declara gastar mais com pessoal do que TODA a sua
 * receita; abaixo de zero, declara gasto negativo. Nenhum dos dois descreve uma
 * prefeitura -- descrevem um formulario preenchido errado.
 *
 * Os dois extremos apareceram no dado real de 2024, e por motivos diferentes:
 *
 * - Guaratinga/BA declarou **371,02%** (R$ 110 mi sobre R$ 29,6 mi).
 * - Paripueira/AL declarou **despesa negativa** e portanto **-19,35%**.
 *
 * O caso negativo e o mais perigoso, e por uma razao que custou perceber: ele e
 * **internamente coerente**. `despesa / RCL` da exatamente -19,35%, entao a
 * conferencia NAO o acusa -- coerencia nao e plausibilidade. E num ranking por
 * percentual ele iria para o **fim da lista**, parecendo o municipio mais
 * economico do Nordeste.
 *
 * Exibidos como declarados e marcados. Corrigir seria inventar numero; esconder
 * seria escolher quais declaracoes o leitor pode ver.
 */
export const LIMITE_PLAUSIVEL = 100;
export const MINIMO_PLAUSIVEL = 0;

/** Onde o município cai em relação aos dois limites da Lei de
 *  Responsabilidade Fiscal. Sem percentual, a resposta é "não sei" — e "não
 *  sei" nunca pode virar "está abaixo". */
export function faixa(m: Municipio, limites: Snapshot["limites"]): Faixa {
  if (m.percentual === null) return "sem-dado";
  if (m.percentual > LIMITE_PLAUSIVEL) return "implausivel";
  if (m.percentual < MINIMO_PLAUSIVEL) return "implausivel";
  if (m.percentual > limites.legal) return "acima-legal";
  const prudencial = m.limitePrudencial ?? limites.prudencial;
  if (m.percentual > prudencial) return "acima-prudencial";
  return "abaixo";
}

export const ROTULO_FAIXA: Record<Faixa, string> = {
  implausivel: "Valor implausível",
  "acima-legal": "Acima do limite legal",
  "acima-prudencial": "Acima do limite prudencial",
  abaixo: "Dentro do limite",
  "sem-dado": "Sem relatório entregue",
};
