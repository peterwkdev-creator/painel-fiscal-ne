import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Painel Fiscal do Nordeste — gasto com pessoal contra o limite legal",
  description:
    "Quanto cada município do Nordeste gasta com pessoal contra o limite da " +
    "Lei de Responsabilidade Fiscal, direto do SICONFI/Tesouro Nacional, com " +
    "a fonte e a data de coleta ao lado de cada número.",
  metadataBase: new URL("https://painel-fiscal-ne.vercel.app"),
  openGraph: {
    title: "Painel Fiscal do Nordeste",
    description:
      "Gasto com pessoal dos municípios do Nordeste contra o limite legal, " +
      "com procedência.",
    locale: "pt_BR",
    type: "website",
  },
  // `app/opengraph-image.png` vira o og:image sozinho, pela convenção de
  // arquivo do App Router. O `card` precisa ser explícito: o padrão do Next é
  // `summary`, que renderiza um quadrado pequeno e joga fora a imagem larga.
  twitter: {
    card: "summary_large_image",
    title: "Painel Fiscal do Nordeste",
    description:
      "Gasto com pessoal dos municípios do Nordeste contra o limite legal.",
  },
  robots: { index: true, follow: true },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR">
      <body>
        {/* Primeiro elemento focável da página: quem navega por teclado não
            deveria passar por toda a navegação para chegar ao conteúdo. */}
        <a className="pular-para-conteudo" href="#conteudo">
          Pular para o conteúdo
        </a>
        {children}
      </body>
    </html>
  );
}
