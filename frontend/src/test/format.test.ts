import { describe, expect, it } from "vitest";
import { currencyExponent, formatDecimal, formatMinor } from "../lib/format";
import { precheckFile } from "../lib/upload";

describe("money formatting", () => {
  it("formats minor units per currency exponent without floats", () => {
    expect(formatMinor(123456, "USD")).toBe("USD 1,234.56");
    expect(formatMinor(1250, "JPY")).toBe("JPY 1,250");
    expect(formatMinor(1234, "KWD")).toBe("KWD 1.234");
    expect(formatMinor(5, "EUR")).toBe("EUR 0.05");
    expect(formatMinor(-1999, "GBP")).toBe("GBP −19.99");
    expect(formatMinor(null, "USD")).toBe("—");
  });

  it("keeps large values exact", () => {
    expect(formatMinor(900719925474099, "INR")).toBe("INR 9,007,199,254,740.99");
  });

  it("formats decimal strings to currency precision but keeps extra precision", () => {
    expect(formatDecimal("12.5", "USD")).toBe("12.50");
    expect(formatDecimal("1.299", "USD")).toBe("1.299");
    expect(formatDecimal("1200", "JPY")).toBe("1,200");
    expect(formatDecimal("-4", "EUR")).toBe("−4.00");
    expect(formatDecimal(null, "EUR")).toBe("—");
    expect(formatDecimal("not a number", "EUR")).toBe("not a number");
  });

  it("knows currency exponents", () => {
    expect(currencyExponent("JPY")).toBe(0);
    expect(currencyExponent("BHD")).toBe(3);
    expect(currencyExponent(null)).toBe(2);
  });
});

describe("upload pre-check", () => {
  const file = (name: string, type: string, size = 10) => new File([new Uint8Array(size)], name, { type });

  it("accepts supported types", () => {
    expect(precheckFile(file("a.jpg", "image/jpeg"), 10)).toBeNull();
    expect(precheckFile(file("a.pdf", "application/pdf"), 10)).toBeNull();
    expect(precheckFile(file("a.webp", ""), 10)).toBeNull();
  });

  it("rejects unsupported, empty and oversized files with a reason", () => {
    expect(precheckFile(file("a.gif", "image/gif"), 10)).toMatch(/only JPEG/);
    expect(precheckFile(file("a.png", "image/png", 0), 10)).toMatch(/empty/);
    expect(precheckFile(file("a.png", "image/png", 2 * 1024 * 1024), 1)).toMatch(/1 MB/);
  });
});
