import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoginPage from "./page";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

describe("LoginPage", () => {
  beforeEach(() => { window.localStorage.clear(); replace.mockClear(); vi.restoreAllMocks(); });

  it("stores the token and redirects on success", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ access_token: "T" }), { status: 200 })));
    render(<LoginPage />);
    await userEvent.type(screen.getByLabelText("username"), "tester");
    await userEvent.type(screen.getByLabelText("password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(window.localStorage.getItem("bw_token")).toBe("T");
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("shows an error on bad creds", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 401 })));
    render(<LoginPage />);
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText(/invalid username or password/i)).toBeInTheDocument();
  });
});
