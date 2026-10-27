import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Checks from "../components/review/Checks";
import FieldsTable from "../components/review/FieldsTable";
import type { Check, ReceiptField } from "../lib/types";

const f = (over: Partial<ReceiptField>): ReceiptField => ({
  name: "total", label: "Total", required: true, money: true, value: "18.23", extracted_value: "18.23",
  source_text: "$18.23", status: "uncertain", reason: "Printed total does not reconcile.", ...over,
});

describe("FieldsTable", () => {
  it("shows status, reason and printed text; never labels unreviewed values as verified", () => {
    render(<FieldsTable currency="USD" editable busyField={null} onSave={vi.fn()} onConfirm={vi.fn()}
      fields={[f({}), f({ name: "merchant", label: "Merchant", money: false, value: "Blue Oak", extracted_value: "Blue Oak", source_text: null, status: "extracted", reason: null })]} />);
    const total = screen.getByRole("row", { name: /total/i });
    expect(within(total).getByText("Doubtful")).toBeInTheDocument();
    expect(within(total).getByText("Printed total does not reconcile.")).toBeInTheDocument();
    expect(within(total).getByText("printed: $18.23")).toBeInTheDocument();
    expect(within(screen.getByRole("row", { name: /merchant/i })).getByText("Unreviewed")).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
  });

  it("edits a value and sends the correction", async () => {
    const onSave = vi.fn().mockResolvedValue(true);
    render(<FieldsTable currency="USD" editable busyField={null} onSave={onSave} onConfirm={vi.fn()} fields={[f({})]} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit Total" }));
    const input = screen.getByLabelText("Total");
    await userEvent.clear(input);
    await userEvent.type(input, "13.23{Enter}");
    expect(onSave).toHaveBeenCalledWith("total", "13.23");
  });

  it("confirms with one tap and hides controls when not editable", async () => {
    const onConfirm = vi.fn();
    const { rerender } = render(<FieldsTable currency="USD" editable busyField={null} onSave={vi.fn()} onConfirm={onConfirm} fields={[f({})]} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm Total" }));
    expect(onConfirm).toHaveBeenCalledWith("total");
    rerender(<FieldsTable currency="USD" editable={false} busyField={null} onSave={vi.fn()} onConfirm={onConfirm} fields={[f({})]} />);
    expect(screen.queryByRole("button", { name: "Edit Total" })).not.toBeInTheDocument();
  });

  it("shows the original model value when a reviewer corrected it", () => {
    render(<FieldsTable currency="USD" editable={false} busyField={null} onSave={vi.fn()} onConfirm={vi.fn()}
      fields={[f({ value: "13.23", extracted_value: "18.23", status: "verified", reason: "Corrected by reviewer." })]} />);
    expect(screen.getByText("13.23")).toBeInTheDocument();
    expect(screen.getByTitle("Value read by the model")).toHaveTextContent("18.23");
  });
});

describe("Checks", () => {
  const checks: Check[] = [
    { code: "total_reconciles", severity: "error", passed: false, fields: ["total"], message: "Total off by 5.00", details: null },
    { code: "currency_code", severity: "info", passed: true, fields: [], message: "USD is valid", details: null },
  ];
  it("lists failures first and collapses passed checks", async () => {
    render(<Checks checks={checks} />);
    expect(screen.getByText("Total off by 5.00")).toBeInTheDocument();
    expect(screen.queryByText("USD is valid")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /show 1 passed check/i }));
    expect(screen.getByText("USD is valid")).toBeInTheDocument();
  });
});
