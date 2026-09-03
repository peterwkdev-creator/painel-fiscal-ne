import { readFile } from "node:fs/promises";
import path from "node:path";

import type { Snapshot } from "./dados";

/**
 * Leitura do snapshot gerado pelo motor Python. **Só no servidor.**
 *
 * Roda no build, num Server Component: o arquivo é lido do disco, não buscado
 * por rede. O visitante recebe HTML pronto, e o painel não tem
 * responsabilidade nenhuma de coletar dado — é a costura entre as duas
 * linguagens, desenhada em `especs/painel-fiscal.md`.
 *
 * Fica separado de `lib/dados.ts` porque aquele é importado também pelo
 * componente de cliente, e `node:fs` não pode ir para o navegador.
 */

let cache: Snapshot | null = null;

export async function lerSnapshot(): Promise<Snapshot> {
  if (cache) return cache;
  const arquivo = path.join(process.cwd(), "dados", "snapshot.json");
  cache = JSON.parse(await readFile(arquivo, "utf-8")) as Snapshot;
  return cache;
}
