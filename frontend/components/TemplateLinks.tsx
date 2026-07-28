"use client";

import { useEffect, useState } from "react";
import { api, downloadFile } from "@/lib/api";

type Template = {
  key: string;
  title: string;
  description: string;
  download_url: string;
};

/** Download strip for CSV upload templates. Pass `only` to show one. */
export default function TemplateLinks({ only }: { only?: string[] }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [error, setError] = useState("");

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
                <button
                  className="btn btn-secondary"
                  onClick={() =>
                    void downloadFile(t.download_url, `unicore_${t.key}_template.csv`).catch(
                      (err) => setError(String((err as Error).message)),
                    )
                  }
                >
                  Download CSV
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="card-meta">
        Each template carries the header row, one example row, and notes on required fields and
        formats. Comment lines starting with # are ignored on upload.
      </p>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
