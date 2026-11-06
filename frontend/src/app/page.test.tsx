import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchSnapshot: vi.fn(),
  writeCaseParam: vi.fn(),
  useLate: vi.fn(() => false),
  // The case id the URL carries on load, read by the CaseParamListener stub.
  urlCase: null as string | null,
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  fetchSnapshot: mocks.fetchSnapshot,
}));
vi.mock("@/lib/tenant-context", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/tenant-context")>()),
  useTenant: () => ({ tenant: { id: "tenant-a" } }),
}));
vi.mock("@/lib/url-state", async (importOriginal) => {
  const { useEffect } = await import("react");
  return {
    ...(await importOriginal<typeof import("@/lib/url-state")>()),
    CaseParamListener: ({ onChange }: { onChange: (caseId: string | null) => void }) => {
      useEffect(() => onChange(mocks.urlCase), [onChange]);
      return null;
    },
    writeCaseParam: mocks.writeCaseParam,
    useLate: () => mocks.useLate(),
  };
});
// Each child has its own tests. Here each stub shows what the page handed it.
vi.mock("@/components/pipeline/Board", () => ({
  Board: ({ buckets, onOpen, selectedCaseId }: {
    buckets: { column: { state: string; label: string }; total: number; cases: { case_id: string }[] }[];
    onOpen: (caseId: string) => void;
    selectedCaseId: string | null;
  }) => (
    <div data-testid="board">
      <p>{`selected:${selectedCaseId ?? "none"}`}</p>
      <button type="button" onClick={() => onOpen("CASE-A")}>open CASE-A</button>
      <ul>
        {buckets.map((b) => (
          <li key={b.column.state}>{`${b.column.label}:${b.total}:${b.cases.map((c) => c.case_id).join(",")}`}</li>
        ))}
      </ul>
    </div>
  ),
}));
vi.mock("@/components/pipeline/BoardToolbar", () => ({
  BoardToolbar: ({ search, sort, priority, matched, totalFetched, onSearch, onSort, onPriority }: {
    search: string;
    sort: string;
    priority: string;
    matched: number;
    totalFetched: number;
    onSearch: (value: string) => void;
    onSort: (value: string) => void;
    onPriority: (value: string) => void;
  }) => (
    <div>
      <p data-testid="toolbar">{[search, sort, priority, matched, totalFetched].join("|")}</p>
      <button type="button" onClick={() => onSearch("case-b")}>search case-b</button>
      <button type="button" onClick={() => onPriority("CRITICAL")}>critical only</button>
      <button type="button" onClick={() => onSort("risk-high")}>sort risk</button>
    </div>
  ),
}));
vi.mock("@/components/pipeline/CaseTraceSheet", () => ({
  CaseTraceSheet: ({ caseId, onOpenChange }: {
    caseId: string | null;
    onOpenChange: (open: boolean) => void;
  }) => (
    <div>
      <p>{`trace:${caseId ?? "none"}`}</p>
      <button type="button" onClick={() => onOpenChange(false)}>close trace</button>
    </div>
  ),
}));
vi.mock("@/components/pipeline/PipelineKpis", () => ({
  PipelineKpis: ({ snapshot, loading }: { snapshot: { cases: unknown[] } | undefined; loading: boolean }) => (
    <p data-testid="kpis">{loading ? "loading" : `loaded:${snapshot?.cases.length ?? 0}`}</p>
  ),
}));
vi.mock("@/components/pipeline/StartHere", () => ({ StartHere: () => null }));
vi.mock("@/components/help/HelpDot", () => ({ HelpDot: () => null }));
vi.mock("@/components/layout/States", () => ({
  ErrorState: ({ error }: { error: unknown }) => (
    <p data-testid="error">{error instanceof Error ? error.message : "unknown"}</p>
  ),
}));

import PipelinePage from "./page";

const CASE_A = { case_id: "CASE-A", state: "INGESTED", risk_score: 95, created_at: "2026-01-01T00:00:00Z" };
const CASE_B = { case_id: "CASE-B", state: "AUTO_CLEARED", risk_score: 10, created_at: "2026-01-02T00:00:00Z" };
const SNAPSHOT = { cases: [CASE_A, CASE_B], counts: { INGESTED: 3, AUTO_CLEARED: 1 }, in_flight: 0 };

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PipelinePage />
    </QueryClientProvider>,
  );
}

describe("Pipeline page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useLate.mockReturnValue(false);
    mocks.urlCase = null;
    mocks.fetchSnapshot.mockResolvedValue(SNAPSHOT);
  });

  it("asks for the snapshot with drain on, because this screen is the one that advances the pipeline", async () => {
    renderPage();
    await screen.findByTestId("board");
    expect(mocks.fetchSnapshot).toHaveBeenCalledWith({ drain: true });
  });

  it("does not ask for the snapshot again by itself, so a tab left open does not spend the Firestore read quota", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderPage();
      await screen.findByTestId("board");
      await vi.advanceTimersByTimeAsync(30_000);
      expect(mocks.fetchSnapshot).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows no board while the first snapshot is still loading", () => {
    mocks.fetchSnapshot.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.queryByTestId("board")).toBeNull();
    expect(screen.getByTestId("kpis")).toHaveTextContent("loading");
  });

  it("explains the wait when the first load is slow", () => {
    mocks.useLate.mockReturnValue(true);
    mocks.fetchSnapshot.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("Waking the service");
  });

  it("does not show the wake-up hint once the board has loaded", async () => {
    renderPage();
    await screen.findByTestId("board");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders each column with its total and the cases it holds", async () => {
    renderPage();
    expect(await screen.findByText("Queued:3:CASE-A")).toBeInTheDocument();
    expect(screen.getByText("Cleared:1:CASE-B")).toBeInTheDocument();
    // A column no count mentions shows zero and no cases, rather than being dropped.
    expect(screen.getByText("Awaiting a person:0:")).toBeInTheDocument();
  });

  it("passes the loaded cases to the toolbar and the KPIs", async () => {
    renderPage();
    // The KPI stub renders "loading" on the first pass, so wait for the loaded text itself.
    await waitFor(() => expect(screen.getByTestId("kpis")).toHaveTextContent("loaded:2"));
    expect(screen.getByTestId("toolbar")).toHaveTextContent("|newest||2|2");
  });

  it("shows an empty board when the snapshot has no cases", async () => {
    mocks.fetchSnapshot.mockResolvedValue({ cases: [], counts: {}, in_flight: 0 });
    renderPage();
    expect(await screen.findByText("Queued:0:")).toBeInTheDocument();
    expect(screen.getByTestId("kpis")).toHaveTextContent("loaded:0");
    expect(screen.getByTestId("toolbar")).toHaveTextContent("|newest||0|0");
  });

  it("shows the error and no board when the snapshot request fails", async () => {
    mocks.fetchSnapshot.mockRejectedValue(new Error("upstream unreachable"));
    renderPage();
    expect(await screen.findByTestId("error")).toHaveTextContent("upstream unreachable");
    expect(screen.queryByTestId("board")).toBeNull();
  });

  it("opens a case when its card is clicked, and writes it to the URL", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "open CASE-A" }));
    expect(mocks.writeCaseParam).toHaveBeenCalledWith("CASE-A");
    expect(screen.getByText("selected:CASE-A")).toBeInTheDocument();
    expect(screen.getByText("trace:CASE-A")).toBeInTheDocument();
  });

  it("closes the case and clears it from the URL when the trace sheet is closed", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "open CASE-A" }));
    fireEvent.click(screen.getByRole("button", { name: "close trace" }));
    expect(mocks.writeCaseParam).toHaveBeenLastCalledWith(null);
    expect(screen.getByText("trace:none")).toBeInTheDocument();
  });

  it("opens the case named in the URL on load, without rewriting the URL", async () => {
    mocks.urlCase = "CASE-B";
    renderPage();
    expect(await screen.findByText("trace:CASE-B")).toBeInTheDocument();
    expect(mocks.writeCaseParam).not.toHaveBeenCalled();
  });

  it("narrows the board and the counts when a search is typed", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "search case-b" }));
    expect(screen.getByTestId("toolbar")).toHaveTextContent("case-b|newest||1|2");
    expect(screen.getByText("Queued:3:")).toBeInTheDocument();
    expect(screen.getByText("Cleared:1:CASE-B")).toBeInTheDocument();
  });

  it("keeps only critical cases when the priority filter is set to critical", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "critical only" }));
    expect(screen.getByTestId("toolbar")).toHaveTextContent("|newest|CRITICAL|1|2");
    expect(screen.getByText("Queued:3:CASE-A")).toBeInTheDocument();
    expect(screen.getByText("Cleared:1:")).toBeInTheDocument();
  });

  it("keeps the chosen sort order in the toolbar", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "sort risk" }));
    expect(screen.getByTestId("toolbar")).toHaveTextContent("|risk-high||2|2");
  });
});
