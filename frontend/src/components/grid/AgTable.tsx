"use client";

import { AllCommunityModule, colorSchemeDark, ModuleRegistry, themeQuartz } from "ag-grid-community";
import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";

ModuleRegistry.registerModules([AllCommunityModule]);

const theme = themeQuartz.withPart(colorSchemeDark).withParams({
  backgroundColor: "transparent",
  foregroundColor: "rgba(255, 255, 255, 0.88)",
  headerTextColor: "rgba(255, 255, 255, 0.6)",
  borderColor: "rgba(255, 255, 255, 0.08)",
  rowHoverColor: "rgba(255, 255, 255, 0.04)",
  fontSize: 12,
});

/**
 * A read-only table on AG Grid Community (MIT), styled to match the console.
 *
 * Every row is rendered and the height follows the rows, so the table reads and prints as a table
 * (and renders under jsdom). Columns sort and resize; long text wraps.
 */
export function AgTable<T extends object>({
  label,
  rows,
  columns,
  rowId,
}: {
  label: string;
  rows: T[];
  columns: ColDef<T>[];
  rowId: (row: T) => string;
}) {
  return (
    <section className="bento-card overflow-hidden p-2" aria-label={label}>
      <AgGridReact<T>
        theme={theme}
        rowData={rows}
        columnDefs={columns}
        getRowId={(params) => rowId(params.data)}
        domLayout="autoHeight"
        suppressRowVirtualisation
        suppressColumnVirtualisation
        suppressCellFocus
        defaultColDef={{ sortable: true, resizable: true, flex: 1, minWidth: 110, wrapText: true, autoHeight: true }}
      />
    </section>
  );
}
