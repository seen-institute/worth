import type { TableFile } from "../types";

const NUMERIC = /^-?[\d.]+$/;

export function DataTable({ file }: { file: TableFile }) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            {file.columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {file.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className={NUMERIC.test(cell) ? "num" : undefined}>
                  {cell || "·"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
