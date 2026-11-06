import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgTable } from "./AgTable";

// AG Grid measures its container with ResizeObserver, which jsdom does not provide.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

type Row = { id: string; name: string };
const ROWS: Row[] = [{ id: "a", name: "Alpha" }, { id: "b", name: "Beta" }];

describe("AgTable", () => {
  it("renders the column headers and every row, under the label it was given", async () => {
    render(<AgTable label="Demo table" rows={ROWS} columns={[{ headerName: "Name", field: "name" }]} rowId={(r) => r.id} />);
    expect(await screen.findByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Demo table" })).toBeInTheDocument();
  });

  it("renders a component in a cell, so a row can carry a button", async () => {
    const onPick = vi.fn();
    render(
      <AgTable
        label="Demo table"
        rows={ROWS}
        columns={[
          { headerName: "Name", field: "name" },
          {
            headerName: "",
            sortable: false,
            cellRenderer: (params: { data?: Row }) => (
              <button type="button" onClick={() => params.data && onPick(params.data)}>
                Pick {params.data?.name}
              </button>
            ),
          },
        ]}
        rowId={(r) => r.id}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Pick Beta" }));
    expect(onPick).toHaveBeenCalledWith({ id: "b", name: "Beta" });
  });
});
