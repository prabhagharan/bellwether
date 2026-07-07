import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConditionBuilder } from "./ConditionBuilder";

describe("ConditionBuilder", () => {
  it("emits only the set fields", async () => {
    const onChange = vi.fn();
    render(<ConditionBuilder onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("min_confidence"), "0.7");
    await userEvent.click(screen.getByLabelText("up"));
    const last = onChange.mock.calls.at(-1)![0];
    expect(last).toEqual({ min_confidence: 0.7, directions: ["up"] });
    expect(last).not.toHaveProperty("min_magnitude");
  });
});
