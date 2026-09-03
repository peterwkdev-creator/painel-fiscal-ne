"use client";

import { useMemo, useState } from "react";

import {
  br, faixa, ROTULO_FAIXA,
  type Faixa, type Municipio, type Snapshot,
} from "../lib/dados";
import estilos from "./municipios.module.css";

/**
 * O único componente de cliente da página: busca e ordenação da tabela.
 *
 * Tudo o mais é HTML gerado no build. O JavaScript que chega ao navegador
 * existe só para o que precisa de interação — filtrar 1.793 linhas.
 */

const UFS = ["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"] as const;

type Ordem = "percentual" | "nome" | "populacao";

export default function Municipios({
  municipios,
  limites,
  quadrimestre,
}: {
  municipios: Municipio[];
  limites: Snapshot["limites"];
  quadrimestre: string;
}) {
  const [busca, setBusca] = useState("");
  const [uf, setUf] = useState<string>("");
  const [soAcima, setSoAcima] = useState(false);
  const [ordem, setOrdem] = useState<Ordem>("percentual");

  const visiveis = useMemo(() => {
    const alvo = busca.trim().toLowerCase();
    const filtrados = municipios.filter((m) => {
      if (uf && m.uf !== uf) return false;
      if (alvo && !m.nome.toLowerCase().includes(alvo)) return false;
      if (soAcima) {
        const f = faixa(m, limites);
        return f === "acima-legal" || f === "acima-prudencial";
      }
      return true;
    });
    return filtrados.sort((a, b) => {
      if (ordem === "nome") return a.nome.localeCompare(b.nome, "pt-BR");
      if (ordem === "populacao") return (b.populacao ?? -1) - (a.populacao ?? -1);
      // Sem percentual vai para o fim: ausência não compete por posição num
      // ranking de quem gasta mais.
      const pa = a.percentual ?? -1;
      const pb = b.percentual ?? -1;
      return pb - pa;
    });
  }, [municipios, busca, uf, soAcima, ordem, limites]);

  return (
    <section className={estilos.bloco} aria-label="Municípios">
      <div className={estilos.controles}>
        <label className={estilos.campo}>
          <span>Município</span>
          <input
            type="search"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
            placeholder="buscar pelo nome"
          />
        </label>

        <label className={estilos.campo}>
          <span>Estado</span>
          <select value={uf} onChange={(e) => setUf(e.target.value)}>
            <option value="">todos</option>
            {UFS.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
        </label>

        <label className={estilos.campo}>
          <span>Ordenar por</span>
          <select value={ordem} onChange={(e) => setOrdem(e.target.value as Ordem)}>
            <option value="percentual">maior percentual</option>
            <option value="populacao">população</option>
            <option value="nome">nome</option>
          </select>
        </label>

        <label className={estilos.caixa}>
          <input
            type="checkbox"
            checked={soAcima}
            onChange={(e) => setSoAcima(e.target.checked)}
          />
          <span>só acima do limite</span>
        </label>
      </div>

      <p className={estilos.contagem} role="status">
        {visiveis.length} de {municipios.length} municípios · {quadrimestre}
      </p>

      <div className={estilos.rolagem}>
        <table className={estilos.tabela}>
          <caption className={estilos.legenda}>
            Despesa com pessoal como percentual da receita corrente líquida
            ajustada, declarada pelo próprio município.
          </caption>
          <thead>
            <tr>
              <th scope="col">Município</th>
              <th scope="col">UF</th>
              <th scope="col" className={estilos.num}>População</th>
              <th scope="col" className={estilos.num}>Pessoal / RCL</th>
              <th scope="col" className={estilos.num}>Limite</th>
              <th scope="col">Situação</th>
            </tr>
          </thead>
          <tbody>
            {visiveis.map((m) => {
              const f = faixa(m, limites);
              return (
                <tr key={m.codigo}>
                  <th scope="row" className={estilos.nome}>{m.nome}</th>
                  <td>{m.uf}</td>
                  <td className={`${estilos.num} tabular`}>
                    {m.populacao === null ? "—" : br(m.populacao, 0)}
                  </td>
                  <td className={`${estilos.num} tabular ${estilos[f]}`}>
                    {m.percentual === null ? "—" : `${br(m.percentual)}%`}
                  </td>
                  <td className={`${estilos.num} tabular`}>
                    {m.limitePrudencial === null
                      ? "—"
                      : `${br(m.limitePrudencial)}%`}
                  </td>
                  {/* O rótulo em texto existe para a informação não depender
                      só de cor -- ver o comentário em globals.css. */}
                  <td>
                    <span className={`${estilos.selo} ${estilos[f]}`}>
                      {ROTULO_FAIXA[f]}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {visiveis.length === 0 && (
        <p className={estilos.vazio}>Nenhum município com esses filtros.</p>
      )}
    </section>
  );
}
