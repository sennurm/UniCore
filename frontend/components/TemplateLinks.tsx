"use client";

import { useEffect, useState } from "react";
import { api, downloadUrl } from "@/lib/api";

type Template = {
  key: string;
  title: string;
  description: string;
  download_url: string;
};

/** Download strip for CSV upload templates. Pass `only` to show one. */
export default function TemplateLinks({ only }: { only?: string[] }) {
  const [templates, setTemplates] = useState<Template[]>([]);

  useEffect(() => {
    api<Template[]>("/templates")
      .then((all) => setTemplates(only ? all.filter((t) => only.includes(t.key)) : all))
      .catch(() => undefined);
  }, [only]);

  if (templates.length === 0) return null;

  return (
    <div className="card">
      <div className="card-kicker">Upload templates</div>
      <table className="table">
        <tbody>
          {templates.map((t) => (
            <tr key={t.key}>
              <td style={{ width: 170 }}><strong>{t.title}</strong></td>
              <td className="card-meta" style={{ display: "table-cell" }}>{t.description}</td>
              <td style={{ width: 130, textAlign: "right" }}>
                <a className="btn btn-secondary" href={downloadUrl(t.download_url)}>
                  Download CSV
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="card-meta">
        Each template carries the header row, one example row, and notes on required fields and
        formats. Comment lines starting with # are ignored on upload.
      </p>
    </div>
  );
}
