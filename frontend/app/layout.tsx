import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "UniCore",
  description: "University operations core",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
