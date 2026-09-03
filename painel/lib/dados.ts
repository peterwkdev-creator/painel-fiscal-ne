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
}

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
