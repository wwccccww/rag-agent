import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RAG Agent",
  description: "Local RAG + streaming chat",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
