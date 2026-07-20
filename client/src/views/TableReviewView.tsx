import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api, tableCropUrl } from "../api/client";
import type {
  MaterialRowOut,
  MaterialTableDetailOut,
  TableKind,
} from "../api/types";

const FLAG_TEXT: Record<string, string> = {
  qty_missing: "qty missing",
  qty_not_positive: "qty ≤ 0",
  qty_not_integer: "qty not a whole number",
  qty_x_unit_length_mismatch: "qty × unit length ≠ total length",
  qty_x_unit_weight_mismatch: "qty × unit weight ≠ total weight",
  unit_length_mm_not_positive: "unit length ≤ 0",
  total_length_mm_not_positive: "total length ≤ 0",
  unit_weight_kg_not_positive: "unit weight ≤ 0",
  total_weight_kg_not_positive: "total weight ≤ 0",
  area_x_thk_weight_mismatch: "area × thickness × steel density ≠ weight",
  area_exceeds_qty_x_bounding_rect: "area larger than qty × W×H",
};

const STATUS_STYLE: Record<string, string> = {
  auto_approved: "bg-emerald-900/60 text-emerald-300",
  approved: "bg-emerald-900/60 text-emerald-300",
  edited: "bg-sky-900/60 text-sky-300",
  needs_review: "bg-amber-900/60 text-amber-300",
  rejected: "bg-zinc-800 text-zinc-500 line-through",
};

type EditDraft = {
  description: string;
  qty: string;
  unit_length_mm: string;
  total_length_mm: string;
  total_weight_kg: string;
};

function RowEditor({
  row,
  onSave,
  onCancel,
}: {
  row: MaterialRowOut;
  onSave: (fields: Record<string, number | string>) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<EditDraft>({
    description: row.description ?? "",
    qty: row.qty?.toString() ?? "",
    unit_length_mm: row.unit_length_mm?.toString() ?? "",
    total_length_mm: row.total_length_mm?.toString() ?? "",
    total_weight_kg: row.total_weight_kg?.toString() ?? "",
  });
  const num = (s: string) => (s.trim() === "" ? undefined : Number(s));
  const set = (k: keyof EditDraft) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setDraft((d) => ({ ...d, [k]: e.target.value }));
  const input = "w-24 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-xs";
  return (
    <tr className="bg-zinc-900/70">
      <td className="px-2 py-1.5 text-xs text-zinc-500">✎</td>
      <td className="px-2 py-1.5">
        <input
          className={`${input} w-48`}
          value={draft.description}
          onChange={set("description")}
          placeholder="description"
        />
      </td>
      <td className="px-2 py-1.5">
        <input className={input} value={draft.qty} onChange={set("qty")} />
      </td>
      <td className="px-2 py-1.5">
        <input className={input} value={draft.unit_length_mm} onChange={set("unit_length_mm")} />
      </td>
      <td className="px-2 py-1.5">
        <input className={input} value={draft.total_length_mm} onChange={set("total_length_mm")} />
      </td>
      <td className="px-2 py-1.5">
        <input className={input} value={draft.total_weight_kg} onChange={set("total_weight_kg")} />
      </td>
      <td className="px-2 py-1.5" colSpan={2}>
        <button
          onClick={() =>
            onSave({
              description: draft.description,
              ...(num(draft.qty) !== undefined && { qty: num(draft.qty)! }),
              ...(num(draft.unit_length_mm) !== undefined && {
                unit_length_mm: num(draft.unit_length_mm)!,
              }),
              ...(num(draft.total_length_mm) !== undefined && {
                total_length_mm: num(draft.total_length_mm)!,
              }),
              ...(num(draft.total_weight_kg) !== undefined && {
                total_weight_kg: num(draft.total_weight_kg)!,
              }),
            })
          }
          className="mr-1 rounded bg-emerald-700 px-2 py-0.5 text-xs font-medium hover:bg-emerald-600"
        >
          Save
        </button>
        <button
          onClick={onCancel}
          className="rounded bg-zinc-800 px-2 py-0.5 text-xs hover:bg-zinc-700"
        >
          Cancel
        </button>
      </td>
    </tr>
  );
}

export default function TableReviewView({
  tableId,
  onBack,
}: {
  tableId: number;
  onBack: () => void;
}) {
  const [table, setTable] = useState<MaterialTableDetailOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [showApproved, setShowApproved] = useState(false);

  const refresh = useCallback(
    () => api.getTable(tableId).then(setTable).catch((e) => setError(e.message)),
    [tableId],
  );
  useEffect(() => {
    refresh();
  }, [refresh]);

  async function rowAction(
    rowId: number,
    body: Parameters<typeof api.patchMaterialRow>[1],
  ) {
    setError(null);
    try {
      await api.patchMaterialRow(rowId, body);
      setEditing(null);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function tableAction(body: Parameters<typeof api.patchTable>[1]) {
    setError(null);
    try {
      await api.patchTable(tableId, body);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!table) {
    return <div className="p-8 text-sm text-zinc-500">{error ?? "Loading table…"}</div>;
  }

  const flagged = table.rows.filter((r) => r.status === "needs_review");
  const visibleRows = showApproved
    ? table.rows
    : table.rows.filter((r) => r.status !== "auto_approved");
  const fmt = (v: number | null) => (v == null ? "—" : v.toLocaleString());

  // A mixed BOM holds two row species: bars (cut lengths) and plates (W×H,
  // area, THK). Split them into separate tables so each gets honest headers.
  const isPlate = (r: MaterialRowOut) =>
    r.width_mm != null || r.area_m2 != null || r.thk_mm != null;
  const plateRows = visibleRows.filter(isPlate);
  const barRows = visibleRows.filter((r) => !isPlate(r));

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            {table.title || `Table #${table.id}`}
            <span className="ml-3 rounded bg-zinc-800 px-2 py-0.5 text-xs font-normal text-zinc-300">
              {table.kind}
            </span>
            <span
              className={`ml-2 rounded px-2 py-0.5 text-xs font-normal ${
                STATUS_STYLE[table.status] ?? "bg-zinc-800 text-zinc-300"
              }`}
            >
              {table.status.toUpperCase()}
            </span>
          </h1>
          <p className="mt-1 text-sm text-zinc-400">
            {table.n_rows} rows · {table.auto_approved_rows} approved ·{" "}
            {table.needs_review_rows} flagged
            {table.validation?.weight_total_matches === true && (
              <span className="ml-2 text-emerald-400">
                ✓ weight column reconciles with printed total (
                {table.declared_total_weight_kg} kg)
              </span>
            )}
            {table.validation?.weight_total_matches === false && (
              <span className="ml-2 text-amber-400">
                ⚠ weight sum {table.validation.summed_total_weight_kg} kg ≠ printed{" "}
                {table.declared_total_weight_kg} kg
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={table.kind}
            onChange={(e) =>
              tableAction({ action: "set_kind", kind: e.target.value as TableKind })
            }
            className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm"
          >
            <option value="materials">materials</option>
            <option value="coordinates">coordinates</option>
            <option value="other">other</option>
            <option value="unknown">unknown</option>
          </select>
          {table.status === "approved" ? (
            <button
              onClick={() => tableAction({ action: "reopen" })}
              className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
            >
              Reopen
            </button>
          ) : (
            <>
              <button
                onClick={() => tableAction({ action: "approve" })}
                disabled={flagged.length > 0}
                title={
                  flagged.length
                    ? `${flagged.length} rows still need review`
                    : "Add this table to the project summary"
                }
                className="rounded bg-emerald-700 px-3 py-1.5 text-sm font-medium hover:bg-emerald-600 disabled:opacity-40"
              >
                Approve table
              </button>
              <button
                onClick={() => tableAction({ action: "reject" })}
                className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-red-900"
              >
                Reject
              </button>
            </>
          )}
          <button
            onClick={onBack}
            className="rounded bg-zinc-800 px-3 py-1.5 text-sm hover:bg-zinc-700"
          >
            ← Back
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex min-h-0 flex-1 gap-4">
        <div className="w-1/2 overflow-auto rounded border border-zinc-800 bg-white/95 p-2">
          <img
            src={tableCropUrl(table.id)}
            alt="table crop"
            className="w-full"
            style={{ filter: "brightness(0.5) contrast(30)" }}
          />
        </div>

        <div className="flex w-1/2 flex-col overflow-auto rounded border border-zinc-800">
          <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
            <span className="text-sm text-zinc-400">
              {visibleRows.length} of {table.rows.length} rows shown
            </span>
            <label className="flex items-center gap-1.5 text-xs text-zinc-400">
              <input
                type="checkbox"
                checked={showApproved}
                onChange={(e) => setShowApproved(e.target.checked)}
              />
              show auto-approved
            </label>
          </div>
          {(() => {
            // cells shared by both species — the middle columns differ
            const startCells = (r: MaterialRowOut) => (
              <>
                <td className="px-2 py-1.5 text-xs text-zinc-500">
                  {r.row_index + 1}
                </td>
                <td className="px-2 py-1.5">
                  <div className="font-medium">{r.material_key ?? "—"}</div>
                  {r.description && (
                    <div className="text-xs text-zinc-500">{r.description}</div>
                  )}
                  {r.flags.length > 0 && (
                    <div className="mt-0.5 flex flex-wrap gap-1">
                      {r.flags.map((f) => (
                        <span
                          key={f}
                          className="rounded bg-amber-950 px-1.5 py-0.5 text-[10px] text-amber-300"
                        >
                          {FLAG_TEXT[f] ?? f}
                        </span>
                      ))}
                    </div>
                  )}
                </td>
                <td className="px-2 py-1.5 tabular-nums">{fmt(r.qty)}</td>
              </>
            );
            const endCells = (r: MaterialRowOut) => (
              <>
                <td className="px-2 py-1.5 tabular-nums">{fmt(r.total_weight_kg)}</td>
                <td className="px-2 py-1.5">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                      STATUS_STYLE[r.status]
                    }`}
                  >
                    {r.status.replace("_", " ")}
                  </span>
                </td>
                <td className="px-2 py-1.5 whitespace-nowrap">
                  {table.status !== "approved" && (
                    <>
                      {r.status === "needs_review" && (
                        <button
                          onClick={() => rowAction(r.id, { action: "approve" })}
                          className="mr-1 rounded bg-emerald-800 px-1.5 py-0.5 text-xs hover:bg-emerald-700"
                          title="Approve as read"
                        >
                          ✓
                        </button>
                      )}
                      <button
                        onClick={() => setEditing(r.id)}
                        className="mr-1 rounded bg-zinc-800 px-1.5 py-0.5 text-xs hover:bg-zinc-700"
                        title="Edit values"
                      >
                        ✎
                      </button>
                      {r.status !== "rejected" && (
                        <button
                          onClick={() => rowAction(r.id, { action: "reject" })}
                          className="rounded bg-zinc-800 px-1.5 py-0.5 text-xs hover:bg-red-900"
                          title="Reject row"
                        >
                          ✕
                        </button>
                      )}
                    </>
                  )}
                </td>
              </>
            );
            const renderRows = (rows: MaterialRowOut[], mid: (r: MaterialRowOut) => ReactNode) =>
              rows.map((r) =>
                editing === r.id ? (
                  <RowEditor
                    key={r.id}
                    row={r}
                    onSave={(fields) => rowAction(r.id, { action: "edit", fields })}
                    onCancel={() => setEditing(null)}
                  />
                ) : (
                  <tr key={r.id} className="border-t border-zinc-800/60">
                    {startCells(r)}
                    {mid(r)}
                    {endCells(r)}
                  </tr>
                ),
              );
            const th = "px-2 py-1.5 font-normal";
            return (
              <>
                {barRows.length > 0 && (
                  <div>
                    <div className="border-b border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-xs font-medium text-zinc-400">
                      Bars · {barRows.length}
                    </div>
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-zinc-950 text-left text-xs text-zinc-500">
                        <tr>
                          <th className={th}>#</th>
                          <th className={th}>Material</th>
                          <th className={th}>Qty</th>
                          <th className={th}>Unit len mm</th>
                          <th className={th}>Total len mm</th>
                          <th className={th}>Weight kg</th>
                          <th className={th}>Status</th>
                          <th className={th}></th>
                        </tr>
                      </thead>
                      <tbody>
                        {renderRows(barRows, (r) => (
                          <>
                            <td className="px-2 py-1.5 tabular-nums">
                              {fmt(r.unit_length_mm)}
                            </td>
                            <td className="px-2 py-1.5 tabular-nums">
                              {fmt(r.total_length_mm)}
                            </td>
                          </>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {plateRows.length > 0 && (
                  <div>
                    <div className="border-b border-t border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-xs font-medium text-zinc-400">
                      Plates · {plateRows.length}
                    </div>
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-zinc-950 text-left text-xs text-zinc-500">
                        <tr>
                          <th className={th}>#</th>
                          <th className={th}>Material</th>
                          <th className={th}>Qty</th>
                          <th className={th}>W×H mm</th>
                          <th className={th}>THK mm</th>
                          <th className={th}>Area m²</th>
                          <th className={th}>Weight kg</th>
                          <th className={th}>Status</th>
                          <th className={th}></th>
                        </tr>
                      </thead>
                      <tbody>
                        {renderRows(plateRows, (r) => (
                          <>
                            <td className="px-2 py-1.5 tabular-nums">
                              {r.width_mm != null && r.height_mm != null
                                ? `${r.width_mm}×${r.height_mm}`
                                : "—"}
                            </td>
                            <td className="px-2 py-1.5 tabular-nums">{fmt(r.thk_mm)}</td>
                            <td className="px-2 py-1.5 tabular-nums">
                              {r.area_m2 != null ? `${r.area_m2}` : "—"}
                            </td>
                          </>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            );
          })()}
        </div>
      </div>
    </div>
  );
}
