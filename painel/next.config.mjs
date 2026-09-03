/** @type {import('next').NextConfig} */
const nextConfig = {
  // Export estático: `next build` gera HTML/CSS/JS em `out/`, sem servidor
  // nenhum. É a decisão do ESPEC.md — nada de rota de servidor, nada de função
  // serverless. Configuração conforme a documentação oficial:
  // https://nextjs.org/docs/app/guides/static-exports
  output: "export",

  // Emite `/rota/index.html` em vez de `/rota.html`. Evita depender de regra de
  // rewrite no host, que é justamente o tipo de configuração invisível que
  // quebra quando se troca de hospedagem.
  trailingSlash: true,

  // O otimizador padrão de imagem exige servidor; sem ele, `next/image` falha
  // no export. Este painel não usa imagem — a declaração fica explícita para
  // que, se alguém adicionar uma, o motivo do erro esteja escrito aqui.
  images: { unoptimized: true },

  reactStrictMode: true,
};

export default nextConfig;
