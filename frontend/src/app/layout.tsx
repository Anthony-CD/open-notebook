import type { Metadata } from "next";
import {
  Bricolage_Grotesque,
  Instrument_Sans,
  Spline_Sans_Mono,
} from "next/font/google";
import "./globals.css";
import "katex/dist/katex.min.css";
import { Toaster } from "@/components/ui/sonner";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { ThemeProvider } from "@/components/providers/ThemeProvider";
import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { ConnectionGuard } from "@/components/common/ConnectionGuard";
import { themeScript } from "@/lib/theme-script";
import { I18nProvider } from "@/components/providers/I18nProvider";

const instrumentSans = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-instrument-sans",
});

const bricolageGrotesque = Bricolage_Grotesque({
  subsets: ["latin"],
  weight: ["600", "700"],
  variable: "--font-bricolage",
});

const splineSansMono = Spline_Sans_Mono({
  subsets: ["latin"],
  variable: "--font-spline-mono",
});

export const metadata: Metadata = {
  title: "Open Notebook",
  description: "Privacy-focused research and knowledge management",
  // The app ships 14 real, human-quality translations via its own i18n
  // system (see lib/i18n.ts) - Chrome's built-in page translator has no
  // way to know that and can still offer/auto-apply itself on top, which
  // not only produces confusing double-translation but has previously
  // caused real React crashes here (facebook/react#11538: the translator
  // rewrites text nodes in place, detaching them from React's tree, which
  // throws on the next poll-driven re-render - see EpisodeCard/
  // AskAcrossSourcesDialog fixes). Opt the whole app out; the existing
  // in-app language switcher is the supported way to change language.
  other: {
    google: "notranslate",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body
        className={`${instrumentSans.variable} ${bricolageGrotesque.variable} ${splineSansMono.variable} font-sans`}
      >
        <ErrorBoundary>
          <ThemeProvider>
            <QueryProvider>
              <I18nProvider>
                <ConnectionGuard>
                  {children}
                  <Toaster />
                </ConnectionGuard>
              </I18nProvider>
            </QueryProvider>
          </ThemeProvider>
        </ErrorBoundary>
      </body>
    </html>
  );
}
