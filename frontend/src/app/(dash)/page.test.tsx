import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SWRConfig } from "swr";

const GET = vi.fn();
vi.mock("@/api/client", () => ({ client: { GET: (...a: any[]) => GET(...a) } }));
vi.mock("@/hooks/useAlertStream", () => ({ useAlertStream: () => ({ alerts: [], connected: false }) }));

import FeedPage from "./page";

function makeSignals(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: i + 1, direction: "up", magnitude: "small", confidence: 0.5, entities: [],
    text: `sig ${i + 1}`, url: null, source_type: "news", figure_name: "F",
    published_at: "2026-07-11T00:00:00Z", evidence_quote: "q",
  }));
}

function renderFeed() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <FeedPage />
    </SWRConfig>,
  );
}

function lastQuery() {
  return GET.mock.calls[GET.mock.calls.length - 1][1].params.query;
}

beforeEach(() => { GET.mockReset(); });

describe("FeedPage pagination", () => {
  it("requests limit=25 offset=0 initially and disables Prev", async () => {
    GET.mockResolvedValue({ data: makeSignals(25) });
    renderFeed();
    await waitFor(() => expect(GET).toHaveBeenCalled());
    expect(lastQuery().limit).toBe(25);
    expect(lastQuery().offset).toBe(0);
    expect(screen.getByRole("button", { name: /prev/i })).toBeDisabled();
  });

  it("Next advances the offset by 25", async () => {
    GET.mockResolvedValue({ data: makeSignals(25) });
    renderFeed();
    await waitFor(() => expect(screen.getByText("sig 1")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(lastQuery().offset).toBe(25));
  });

  it("disables Next when a page returns fewer than 25", async () => {
    GET.mockResolvedValue({ data: makeSignals(10) });
    renderFeed();
    await waitFor(() => expect(screen.getByText("sig 1")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });

  it("changing direction resets to page 1 (offset 0)", async () => {
    GET.mockResolvedValue({ data: makeSignals(25) });
    renderFeed();
    await waitFor(() => expect(screen.getByText("sig 1")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /next/i }));   // -> offset 25
    await userEvent.selectOptions(screen.getByRole("combobox"), "down");    // reset
    await waitFor(() => {
      expect(lastQuery().offset).toBe(0);
      expect(lastQuery().direction).toBe("down");
    });
  });
});
