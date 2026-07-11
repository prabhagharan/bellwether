import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SignalItem } from "./SignalItem";

const signal = {
  id: 1, direction: "down", magnitude: "moderate", confidence: 0.72, entities: ["TSLA"],
  text: "Trump signals 25% EV tariffs\n\nfull body", url: "https://news.example/x",
  source_type: "news", figure_name: "Donald Trump",
  published_at: "2026-07-11T00:00:00Z", evidence_quote: "tariffs will crush margins",
};

describe("SignalItem", () => {
  it("shows a compact summary and hides details until expanded", () => {
    render(<SignalItem signal={signal} />);
    expect(screen.getByText(/Trump signals 25% EV tariffs/)).toBeTruthy();
    expect(screen.getByText("down/moderate")).toBeTruthy();
    expect(screen.queryByText(/tariffs will crush margins/)).toBeNull();
  });

  it("expands on click to reveal source line, link, and evidence quote", () => {
    render(<SignalItem signal={signal} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/tariffs will crush margins/)).toBeTruthy();
    expect(screen.getByText(/news · Donald Trump/)).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe("https://news.example/x");
  });

  it("renders no link when url is null", () => {
    render(<SignalItem signal={{ ...signal, url: null }} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByRole("link")).toBeNull();
  });
});
