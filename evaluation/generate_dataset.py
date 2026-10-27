"""Generate the synthetic, labelled receipt evaluation set.

Every document is fictional (made-up merchants, amounts and references) and rendered from a spec whose
values are the ground truth, so the set is legally shareable and contains no private data. Rendering is
seeded and deterministic; the generated files are committed so evaluations are reproducible even where
fonts differ.

    python evaluation/generate_dataset.py          # writes evaluation/dataset/{files/, manifest.jsonl}
"""

from __future__ import annotations

import io
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "dataset"
FILES = OUT / "files"
AS_OF = "2026-10-27"  # validation "today" for reproducible date checks
EXP = {"JPY": 0, "KWD": 3}
SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥", "CAD": "$", "AUD": "$", "SGD": "S$",
          "CHF": "CHF", "AED": "AED", "MXN": "$", "KWD": "KD"}
FONT_DIRS = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu"), Path("/Library/Fonts")]


def font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    fallbacks = {"mono": ["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"], "monob": ["consolab.ttf", "courbd.ttf", "DejaVuSansMono-Bold.ttf"],
                 "sans": ["arial.ttf", "DejaVuSans.ttf"], "sansb": ["arialbd.ttf", "DejaVuSans-Bold.ttf"], "serif": ["times.ttf", "DejaVuSerif.ttf"]}
    for candidate in fallbacks[name]:
        for directory in FONT_DIRS:
            path = directory / candidate
            if path.exists():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def q(value: Decimal, currency: str) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-EXP.get(currency, 2)), rounding=ROUND_HALF_UP)


def money(value: Decimal | None, currency: str) -> str | None:
    return None if value is None else format(q(value, currency), "f")


@dataclass
class Item:
    desc: str
    qty: str
    unit: str
    total: str | None = None   # printed line total (computed if None)
    discount: str | None = None


@dataclass
class Spec:
    id: str
    tags: list[str]
    merchant: str | None
    address: str
    currency: str
    items: list[Item]
    date_iso: str | None
    date_printed: str | None
    time: str | None = "14:32"
    layout: str = "thermal"          # thermal | invoice | wide
    tax_rate: str | None = "0.08"
    tax_label: str = "Tax"
    tax_included: bool = False
    split_tax: bool = False          # CGST + SGST
    discount: str | None = None
    service_rate: str | None = None
    tip: str | None = None
    total_override: str | None = None  # planted wrong total
    show_subtotal: bool = True
    show_currency_code: bool = True
    payment: str | None = "VISA ****4417"
    reference: str | None = None
    gstin: str | None = None
    degrade: list[str] = field(default_factory=list)
    pages: int = 1
    fmt: str = "jpg"
    not_receipt: bool = False
    date_any_of: list[str] | None = None
    expect_review: bool | None = None
    expect_flags: list[str] = field(default_factory=list)
    hidden_fields: list[str] = field(default_factory=list)  # printed but cropped/obscured -> not visible
    duplicate_of: str | None = None
    duplicate_kind: str | None = None
    seed: int = 0

    # ---- computed ground truth -------------------------------------------------------------------
    def compact(self, it: Item) -> bool:
        """Single-quantity thermal lines are printed as 'description  amount' with no qty or unit price."""
        return Decimal(it.qty) == 1 and self.layout == "thermal" and not it.discount

    def lines(self) -> list[dict]:
        out = []
        for it in self.items:
            qty, unit = Decimal(it.qty), Decimal(it.unit)
            total = Decimal(it.total) if it.total is not None else q(qty * unit - Decimal(it.discount or 0), self.currency)
            printed = not self.compact(it)
            out.append({"description": it.desc, "quantity": it.qty if printed else None,
                        "unit_price": it.unit if printed else None,
                        "line_total": money(total, self.currency), "discount": it.discount})
        return out

    def amounts(self) -> dict[str, Decimal | None]:
        c = self.currency
        subtotal = sum((Decimal(l["line_total"]) for l in self.lines()), Decimal(0))
        discount = Decimal(self.discount) if self.discount else None
        base = subtotal - (discount or 0)
        service = q(base * Decimal(self.service_rate), c) if self.service_rate else None
        tax = None
        if self.tax_rate:
            rate = Decimal(self.tax_rate)
            tax = q(base * rate / (1 + rate), c) if self.tax_included else q((base + (service or 0)) * rate, c)
        tip = Decimal(self.tip) if self.tip else None
        total = base + (service or 0) + (tip or 0) + (0 if self.tax_included or tax is None else tax)
        if self.total_override:
            total = Decimal(self.total_override)
        return {"subtotal": subtotal, "tax": tax, "tip": tip, "discount": discount, "service_charge": service, "total": q(total, c)}

    def ground_truth(self) -> dict:
        a = self.amounts()
        gt = {"merchant": self.merchant, "date": self.date_iso, "currency": self.currency,
              **{k: money(v, self.currency) for k, v in a.items()},
              "transaction_reference": self.reference, "line_items": self.lines()}
        if not self.show_subtotal:
            gt["subtotal"] = None
        for name in self.hidden_fields:
            gt[name] = None
        if self.date_any_of:
            gt["date_any_of"] = self.date_any_of
        return gt

    def reconciles(self) -> bool | None:
        if self.not_receipt or "total" in self.hidden_fields:
            return None
        return self.total_override is None


# ---------------------------------------------------------------------------------------------------
def render_text_receipt(spec: Spec, rng: random.Random) -> list[Image.Image]:
    c, a, lines = spec.currency, spec.amounts(), spec.lines()
    sym = SYMBOL[c]

    def m(v: Decimal | str | None) -> str:
        if v is None:
            return ""
        d = q(Decimal(v), c)
        text = f"{d:,.{EXP.get(c, 2)}f}"
        return text

    def unit_text(unit: str) -> str:
        decimals = len(unit.split(".")[1]) if "." in unit else 0
        return m(unit) if decimals <= EXP.get(c, 2) else unit  # e.g. fuel at 3.299/L keeps three decimals

    rows: list[tuple[str, str, str]] = []   # (style, left, right)
    if spec.merchant:
        rows.append(("title", spec.merchant.upper() if spec.layout == "thermal" else spec.merchant, ""))
    rows.append(("small", spec.address, ""))
    if spec.gstin:
        rows.append(("small", f"GSTIN: {spec.gstin}", ""))
    rows.append(("rule", "", ""))
    if spec.layout == "invoice":
        rows.append(("bold", "TAX INVOICE" if spec.gstin else "INVOICE", ""))
    if spec.reference:
        rows.append(("text", f"{'Invoice No' if spec.layout == 'invoice' else 'Ref'}: {spec.reference}", ""))
    if spec.date_printed:
        rows.append(("text", f"Date: {spec.date_printed}", f"Time: {spec.time}" if spec.time else ""))
    rows.append(("rule", "", ""))
    rows.append(("bold", "Item" if spec.layout != "thermal" else "ITEM", f"Amount ({c})" if spec.show_currency_code else "Amount"))
    for it, line in zip(spec.items, lines):
        qty = Decimal(it.qty)
        if spec.compact(it):
            rows.append(("text", it.desc, m(line["line_total"])))
        else:
            rows.append(("text", it.desc, ""))
            qty_text = it.qty if "." not in it.qty else f"{it.qty} L" if "fuel" in it.desc.lower() else it.qty
            rows.append(("text", f"   {qty_text} x {unit_text(it.unit)}"
                                 + (f"  less {m(it.discount)}" if it.discount else ""), m(line["line_total"])))
    rows.append(("rule", "", ""))
    page_break = len(rows)
    if spec.show_subtotal:
        rows.append(("text", "Subtotal", m(a["subtotal"])))
    if a["discount"]:
        rows.append(("text", "Discount", f"-{m(a['discount'])}"))
    if a["service_charge"]:
        rows.append(("text", f"Service charge {Decimal(spec.service_rate) * 100:.0f}%", m(a["service_charge"])))
    if a["tax"] is not None:
        if spec.split_tax:
            half = q(a["tax"] / 2, c)
            rows.append(("text", f"CGST {Decimal(spec.tax_rate) * 50:.1f}%", m(half)))
            rows.append(("text", f"SGST {Decimal(spec.tax_rate) * 50:.1f}%", m(a["tax"] - half)))
        elif spec.tax_included:
            rows.append(("small", f"Incl. {spec.tax_label} {Decimal(spec.tax_rate) * 100:.0f}%: {m(a['tax'])}", ""))
        else:
            rows.append(("text", f"{spec.tax_label} {Decimal(spec.tax_rate) * 100:g}%", m(a["tax"])))
    if a["tip"]:
        rows.append(("text", "Tip", m(a["tip"])))
    total_label = f"TOTAL {c}" if spec.show_currency_code else "TOTAL"
    rows.append(("total", total_label, f"{sym}{m(a['total'])}" if not spec.show_currency_code else m(a["total"])))
    if spec.tax_included:
        rows.append(("small", f"All prices include {spec.tax_label}.", ""))
    rows.append(("rule", "", ""))
    if spec.payment:
        rows.append(("text", f"Paid: {spec.payment}", ""))
    rows.append(("small", "SAMPLE DOCUMENT - FICTIONAL - NOT A REAL TRANSACTION", ""))
    rows.append(("small", "Thank you!", ""))

    width = {"thermal": 640, "invoice": 1000, "wide": 900}[spec.layout]
    family = {"thermal": "mono", "invoice": "sans", "wide": "serif"}[spec.layout]
    sizes = {"title": 34, "bold": 24, "text": 24, "small": 18, "total": 30, "rule": 10}
    fonts = {"title": font(family + "b" if family != "serif" else "sansb", 34), "bold": font(family + "b" if family != "serif" else "sansb", 24),
             "text": font(family, 24), "small": font(family, 18), "total": font(family + "b" if family != "serif" else "sansb", 30)}
    page_rows = [rows] if spec.pages == 1 else [rows[:page_break] + [("small", "Continued on page 2", "")], rows[page_break:]]
    images = []
    spec.marks = {}
    for number, chunk in enumerate(page_rows, start=1):
        height = 90 + sum(sizes[s] + 14 for s, _, _ in chunk) + 60
        image = Image.new("RGB", (width, max(height, 500)), "#fdfcf8")
        draw = ImageDraw.Draw(image)
        y, margin = 45, 40
        for style, left, right in chunk:
            if style == "total" or (style in ("text", "small") and any(k in left for k in ("Tax", "VAT", "GST", "HST", "MwSt", "IVA", "Incl."))):
                key = "total" if style == "total" else "tax"
                spec.marks.setdefault(key, y)
            if style == "rule":
                draw.line((margin, y + 4, width - margin, y + 4), fill="#999999", width=1)
                y += sizes["rule"] + 6
                continue
            f = fonts[style]
            if style == "title" or (style == "small" and spec.layout == "thermal" and not right):
                tw = draw.textlength(left, font=f)
                draw.text(((width - tw) / 2, y), left, font=f, fill="#1b1b1b")
            else:
                draw.text((margin, y), left, font=f, fill="#1b1b1b")
            if right:
                tw = draw.textlength(right, font=f)
                draw.text((width - margin - tw, y), right, font=f, fill="#1b1b1b")
            y += sizes[style] + 14
        if spec.pages > 1:
            draw.text((margin, image.height - 40), f"Page {number} of {spec.pages}", font=fonts["small"], fill="#666666")
        images.append(image)
    return images


def render_not_receipt(spec: Spec) -> list[Image.Image]:
    image = Image.new("RGB", (800, 700), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((50, 50), "Riverside Yoga Studio", font=font("sansb", 40), fill="#222")
    body = ["Autumn class timetable", "", "Mon  07:00  Sunrise flow", "Wed  18:30  Gentle stretch", "Fri  12:15  Lunch express",
            "Sat  09:00  Beginners", "", "Bring your own mat. First class is free!", "Call 555-0142 to book."]
    for i, line in enumerate(body):
        draw.text((50, 130 + i * 40), line, font=font("sans", 26), fill="#333")
    return [image]


def degrade(images: list[Image.Image], ops: list[str], rng: random.Random, marks: dict | None = None) -> list[Image.Image]:
    marks = marks or {}
    out = []
    for image in images:
        for op in ops:
            if op == "blur":
                image = image.filter(ImageFilter.GaussianBlur(1.3))
            elif op == "heavy_blur":
                image = image.filter(ImageFilter.GaussianBlur(3.2))
            elif op == "low_contrast":
                image = ImageEnhance.Contrast(image).enhance(0.35)
                image = ImageEnhance.Brightness(image).enhance(1.15)
            elif op == "rotate":
                image = image.rotate(rng.choice([-6, -4, 4, 6]), expand=True, fillcolor="#d9d6cf", resample=Image.Resampling.BICUBIC)
            elif op == "noise":
                px = image.load()
                for _ in range(image.width * image.height // 60):
                    x, y = rng.randrange(image.width), rng.randrange(image.height)
                    px[x, y] = (rng.randrange(80, 200),) * 3
            elif op == "crop_bottom":
                # Cut just above the first tax/total line so those values are genuinely not visible.
                cut = min(marks.get("tax", image.height), marks.get("total", image.height)) - 8
                image = image.crop((0, 0, image.width, max(cut, image.height // 3)))
            elif op == "shadow":
                overlay = Image.new("L", image.size, 0)
                d = ImageDraw.Draw(overlay)
                for i in range(image.width):
                    d.line((i, 0, i, image.height), fill=int(110 * i / image.width))
                image = Image.composite(Image.new("RGB", image.size, "#6b6355"), image, overlay)
            elif op == "smudge_total":
                d = ImageDraw.Draw(image)
                y = marks.get("total", int(image.height * 0.7))
                d.rectangle((image.width * 0.45, y - 8, image.width - 15, y + 46), fill="#5a5148")
        out.append(image)
    return out


def encode(images: list[Image.Image], fmt: str) -> bytes:
    buffer = io.BytesIO()
    if fmt == "pdf":
        # Fixed metadata timestamps keep the PDF bytes (and their SHA-256) reproducible.
        stamp = time.gmtime(datetime(2026, 10, 27, tzinfo=timezone.utc).timestamp())
        images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:], resolution=150,
                       creationDate=stamp, modDate=stamp)
    elif fmt == "png":
        images[0].save(buffer, format="PNG", optimize=True)
    else:
        images[0].convert("RGB").save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()


# ---------------------------------------------------------------------------------------------------
def I(desc, qty, unit, total=None, discount=None):  # noqa: E743
    return Item(desc, qty, unit, total, discount)


def specs() -> list[Spec]:
    S = Spec
    out = [
        # --- clear, well-formed receipts across currencies, layouts and date formats -----------------
        S("r001", ["clear", "usd"], "Blue Oak Cafe", "12 Harbour St, Portland OR", "USD",
          [I("Flat white", "2", "4.50"), I("Almond croissant", "1", "3.75"), I("Sparkling water", "1", "2.25")],
          "2026-03-14", "2026-03-14", reference="A19-55821"),
        S("r002", ["clear", "eur", "vat_included", "date_dd.mm"], "Bäckerei Lindenhof", "Lindenstraße 4, 10969 Berlin", "EUR",
          [I("Roggenbrot", "1", "3.90"), I("Butterbrezel", "2", "1.60"), I("Kaffee", "1", "2.80")],
          "2026-05-21", "21.05.2026", tax_rate="0.07", tax_label="MwSt", tax_included=True, payment="EC-Karte", reference="BON 4471"),
        S("r003", ["clear", "gbp", "date_text"], "Kestrel Books", "88 Mill Lane, York YO1 7HT", "GBP",
          [I("Field guide to moths", "1", "14.99"), I("Notebook A5", "2", "3.50")],
          "2026-06-03", "3 Jun 2026", tax_rate="0.20", tax_label="VAT", tax_included=True, layout="wide", reference="KB-20931"),
        S("r004", ["clear", "inr", "gst", "invoice"], "Sharma Electronics Pvt Ltd", "Shop 14, Linking Rd, Mumbai 400050", "INR",
          [I("USB-C cable 1m", "2", "349.00"), I("Wall charger 20W", "1", "1199.00")],
          "2026-07-11", "11/07/2026", tax_rate="0.18", split_tax=True, layout="invoice", gstin="27AAPFU0939F1ZV", reference="INV/2026/0711-08", payment="UPI"),
        S("r005", ["clear", "jpy", "zero_decimal"], "Hoshino Ramen", "2-3-1 Shibuya, Tokyo", "JPY",
          [I("Shoyu ramen", "2", "980"), I("Gyoza", "1", "450"), I("Oolong tea", "2", "300")],
          "2026-04-18", "2026/04/18", tax_rate="0.10", tax_included=True, tax_label="Consumption tax", payment="Cash", reference="0418-221"),
        S("r006", ["clear", "kwd", "three_decimal"], "Gulf Pantry", "Salem Al Mubarak St, Kuwait City", "KWD",
          [I("Dates 1kg", "1", "2.750"), I("Arabic coffee", "1", "1.900"), I("Bottled water x6", "1", "0.650")],
          "2026-08-02", "02-08-2026", tax_rate=None, payment="KNET", reference="GP-55017"),
        S("r007", ["clear", "cad", "date_us_text"], "Northline Hardware", "455 King St W, Toronto ON", "CAD",
          [I("Wood screws 100pk", "2", "6.49"), I("Paint roller", "1", "11.99"), I("Drop cloth", "1", "8.25")],
          "2026-09-09", "Sep 9, 2026", tax_rate="0.13", tax_label="HST", reference="NH-118204"),
        S("r008", ["clear", "aud"], "Coral Bay Pharmacy", "21 Esplanade, Cairns QLD", "AUD",
          [I("Sunscreen SPF50", "1", "18.95"), I("Antihistamine", "1", "12.40")],
          "2026-02-27", "27/02/2026", tax_rate="0.10", tax_label="GST", tax_included=True, reference="CBP 77310"),
        S("r009", ["clear", "sgd", "service_charge"], "Lotus Leaf Kitchen", "3 Temple St, Singapore 058556", "SGD",
          [I("Chicken rice", "2", "6.50"), I("Kangkong belacan", "1", "9.00"), I("Barley drink", "2", "2.20")],
          "2026-01-30", "30/01/2026", tax_rate="0.09", tax_label="GST", service_rate="0.10", reference="T12-0098"),
        S("r010", ["clear", "chf"], "Alpenblick Apotheke", "Bahnhofstrasse 9, 8001 Zürich", "CHF",
          [I("Vitamin D3", "1", "16.90"), I("Lip balm", "2", "4.50")],
          "2026-03-05", "05.03.2026", tax_rate="0.026", tax_label="MWST", tax_included=True, reference="AA-30551"),
        S("r011", ["clear", "aed", "invoice"], "Desert Rose Florist", "Al Wasl Rd, Dubai", "AED",
          [I("Bouquet - roses", "1", "185.00"), I("Delivery", "1", "35.00")],
          "2026-10-02", "02 Oct 2026", tax_rate="0.05", tax_label="VAT", layout="invoice", reference="DRF-2210-77"),
        S("r012", ["clear", "mxn"], "Taquería El Faro", "Av. Juárez 120, CDMX", "MXN",
          [I("Tacos al pastor", "6", "22.00"), I("Agua de horchata", "2", "35.00")],
          "2026-06-21", "21/06/2026", tax_rate="0.16", tax_label="IVA", tax_included=True, payment="Efectivo", reference="F-8812"),
        S("r013", ["clear", "usd", "tip"], "Copper Kettle Bistro", "301 Pine Ave, Austin TX", "USD",
          [I("Grilled salmon", "1", "24.00"), I("Caesar salad", "1", "11.50"), I("Iced tea", "2", "3.25")],
          "2026-08-15", "08/15/2026", tax_rate="0.0825", tip="7.50", reference="CK-4410"),
        S("r014", ["clear", "usd", "discount"], "Greenfield Grocers", "77 Elm St, Madison WI", "USD",
          [I("Organic milk", "2", "4.29"), I("Sourdough loaf", "1", "5.99"), I("Bananas", "1.32", "0.69"), I("Coffee beans", "1", "12.99")],
          "2026-05-02", "05/02/2026 ", tax_rate="0.05", discount="3.00", reference="GG-77812", date_any_of=["2026-05-02", "2026-02-05"],
          expect_flags=["date"]),
        S("r015", ["clear", "usd", "fuel", "fractional_unit"], "Summit Fuel", "Route 9 & Hill Rd, Albany NY", "USD",
          [I("Unleaded fuel", "10.535", "3.299"), I("Car wash", "1", "8.00")],
          "2026-07-28", "2026-07-28", tax_rate=None, reference="SF-019922"),
        S("r016", ["clear", "eur", "invoice", "many_items"], "Atelier Papier", "12 Rue des Martyrs, 75009 Paris", "EUR",
          [I("Carnet A4 ligné", "3", "4.20"), I("Stylo plume", "1", "18.50"), I("Encre bleue", "2", "6.90"), I("Enveloppes x50", "1", "7.40"),
           I("Ruban adhésif", "4", "1.85"), I("Classeur", "2", "5.60")],
          "2026-09-17", "17/09/2026", tax_rate="0.20", tax_label="TVA", layout="invoice", reference="FA-2026-0917"),
        S("r017", ["clear", "gbp", "discount", "line_discount"], "Harrow & Finch Outfitters", "4 Queen St, Bath BA1 1HE", "GBP",
          [I("Wool scarf", "1", "29.00", discount="5.00"), I("Leather gloves", "1", "34.00")],
          "2026-01-12", "12 Jan 2026", tax_rate="0.20", tax_label="VAT", tax_included=True, layout="wide", reference="HF-55102"),
        S("r018", ["clear", "inr", "restaurant", "service_charge"], "Masala Junction", "MG Road, Bengaluru 560001", "INR",
          [I("Paneer tikka", "1", "320.00"), I("Butter naan", "4", "60.00"), I("Dal makhani", "1", "280.00"), I("Lassi", "2", "90.00")],
          "2026-04-06", "06-Apr-2026", tax_rate="0.05", split_tax=True, service_rate="0.05", gstin="29ABCDE1234F1Z5", reference="MJ/0406/112"),
        S("r019", ["clear", "usd", "two_page", "pdf"], "Lakeside Office Supply", "900 Commerce Dr, Reno NV", "USD",
          [I("Copy paper 5-ream", "2", "34.99"), I("Toner cartridge", "1", "89.00"), I("Binder clips", "3", "4.49"), I("Desk lamp", "1", "29.95"),
           I("Whiteboard markers", "2", "7.99"), I("Stapler", "1", "15.50")],
          "2026-09-25", "Sep 25, 2026", tax_rate="0.0825", layout="invoice", pages=2, fmt="pdf", reference="LOS-0925-3381", payment="Net 30"),
        S("r020", ["clear", "eur", "two_page", "pdf"], "Trattoria Il Ponte", "Via Roma 18, 50123 Firenze", "EUR",
          [I("Bruschetta", "2", "7.50"), I("Tagliatelle al ragù", "2", "14.00"), I("Acqua frizzante", "1", "3.50"), I("Tiramisù", "2", "6.50")],
          "2026-06-14", "14/06/2026", tax_rate="0.10", tax_label="IVA", tax_included=True, service_rate="0.10", pages=2, fmt="pdf", reference="SC-61420"),
        S("r021", ["clear", "usd", "png"], "Bright Smile Dental", "15 Oak Plaza, Denver CO", "USD",
          [I("Routine cleaning", "1", "120.00"), I("X-ray (bitewing)", "1", "45.00")],
          "2026-03-30", "March 30, 2026", tax_rate=None, layout="invoice", fmt="png", reference="BSD-30331", payment="Mastercard ****2210"),
        S("r022", ["clear", "aud", "two_page", "pdf"], "Outback Camping Co", "8 Stuart Hwy, Alice Springs NT", "AUD",
          [I("Tent pegs (set)", "2", "12.00"), I("Head torch", "1", "39.95"), I("Water bladder 3L", "1", "44.00"), I("Fuel canister", "3", "8.50")],
          "2026-05-19", "19/05/2026", tax_rate="0.10", tax_label="GST", tax_included=True, pages=2, fmt="pdf", reference="OC-55190"),

        # --- degraded but legible: values are readable, quality should be noticed ------------------
        S("r023", ["blurry", "usd"], "Harbor Laundromat", "5 Dock St, Seattle WA", "USD",
          [I("Wash large", "2", "4.75"), I("Dryer 30 min", "2", "2.50"), I("Detergent", "1", "1.50")],
          "2026-02-11", "2026-02-11", tax_rate=None, degrade=["blur"], reference="HL-2211"),
        S("r024", ["low_contrast", "gbp"], "Penny Lane Deli", "22 Penny Ln, Liverpool", "GBP",
          [I("Club sandwich", "1", "6.95"), I("Crisps", "1", "1.10"), I("Elderflower press", "1", "2.40")],
          "2026-07-07", "07/07/2026", tax_rate=None, degrade=["low_contrast"], reference="PLD 7007"),
        S("r025", ["rotated", "eur"], "Kiosk am Markt", "Marktplatz 1, Leipzig", "EUR",
          [I("Zeitung", "1", "2.50"), I("Kaugummi", "2", "1.20"), I("Mineralwasser", "1", "1.80")],
          "2026-08-20", "20.08.2026", tax_rate="0.19", tax_label="MwSt", tax_included=True, degrade=["rotate"], reference="KM-8820"),
        S("r026", ["noisy", "shadow", "usd"], "Maple Street Diner", "140 Maple St, Burlington VT", "USD",
          [I("Pancake stack", "1", "9.50"), I("Bacon side", "1", "4.25"), I("Coffee", "2", "2.75")],
          "2026-09-12", "09/12/2026", tax_rate="0.06", degrade=["noise", "shadow"], tip="4.00", reference="MSD-0912",
          date_any_of=["2026-09-12", "2026-12-09"], expect_flags=["date"]),
        S("r027", ["rotated", "blurry", "inr"], "Chai Point Express", "Sector 18, Noida", "INR",
          [I("Masala chai", "2", "40.00"), I("Samosa", "3", "25.00")],
          "2026-03-22", "22/03/2026", tax_rate="0.05", split_tax=True, degrade=["rotate", "blur"], reference="CP-322-19"),

        # --- missing / unreadable fields ----------------------------------------------------------
        S("r028", ["missing_date"], "Quick Stop Market", "1200 Route 1, Portsmouth NH", "USD",
          [I("Energy drink", "2", "2.99"), I("Beef jerky", "1", "6.49")],
          None, None, time=None, tax_rate=None, reference="QS-90012", expect_review=True, expect_flags=["date"]),
        S("r029", ["missing_tax", "no_subtotal"], "Sunrise Bagels", "48 Bay Ridge Ave, Brooklyn NY", "USD",
          [I("Everything bagel", "3", "1.75"), I("Cream cheese", "1", "2.50")],
          "2026-06-08", "06/08/2026", tax_rate=None, show_subtotal=False, payment="Cash", reference=None,
          date_any_of=["2026-06-08", "2026-08-06"], expect_flags=["date"]),
        S("r030", ["missing_merchant"], None, "Unit 4, 19 Industrial Way, Leeds", "GBP",
          [I("Parking 2 hrs", "1", "4.80")],
          "2026-05-15", "15/05/2026", tax_rate=None, payment="Contactless", reference="PK-40015", expect_review=True, expect_flags=["merchant"]),
        S("r031", ["cropped", "missing_total"], "Riverbend Garden Centre", "Mill Rd, Cambridge CB1", "GBP",
          [I("Potting compost 40L", "2", "6.99"), I("Tomato plants", "3", "2.50"), I("Garden twine", "1", "3.20")],
          "2026-04-25", "25/04/2026", tax_rate="0.20", tax_label="VAT", tax_included=True, degrade=["crop_bottom"],
          hidden_fields=["total", "tax"], reference="RGC-4425", expect_review=True, expect_flags=["total"]),
        S("r032", ["heavy_blur", "unreadable"], "Midtown Cleaners", "300 W 38th St, New York NY", "USD",
          [I("Shirt laundered", "5", "3.50"), I("Suit dry clean", "1", "18.00")],
          "2026-07-19", "07/19/2026", tax_rate="0.08875", degrade=["heavy_blur"], reference="MC-71922", expect_review=True,
          expect_flags=["total"]),
        S("r033", ["smudged_total"], "Pinewood Pizza", "62 Lake Rd, Duluth MN", "USD",
          [I("Large pepperoni", "1", "17.99"), I("Garlic knots", "1", "5.49"), I("Soda 2L", "1", "3.29")],
          "2026-08-29", "08/29/2026", tax_rate="0.07375", degrade=["smudge_total"], hidden_fields=["total"], reference="PP-8291",
          expect_review=True, expect_flags=["total"]),
        S("r034", ["no_currency_code", "ambiguous_symbol"], "Harbour Bait & Tackle", "Wharf Rd", "AUD",
          [I("Squid bait", "2", "6.50"), I("Fishing line", "1", "14.90")],
          "2026-01-08", "08/01/2026", tax_rate=None, show_currency_code=False, reference="HBT-108", hidden_fields=["currency"],
          date_any_of=["2026-01-08", "2026-08-01"], expect_review=True, expect_flags=["currency", "date"]),

        # --- ambiguous values -----------------------------------------------------------------------
        S("r035", ["ambiguous_date"], "Corner Bookshop", "7 Church St, Dublin", "EUR",
          [I("Paperback novel", "1", "12.99"), I("Bookmark", "1", "2.50")],
          "2026-04-05", "04/05/26", tax_rate=None, reference="CB-0405", date_any_of=["2026-04-05", "2026-05-04"], expect_review=True,
          expect_flags=["date"]),
        S("r036", ["ambiguous_date", "usd"], "Lakeview Car Wash", "1100 Shore Dr, Chicago IL", "USD",
          [I("Deluxe wash", "1", "18.00"), I("Tire shine", "1", "4.00")],
          "2026-03-06", "03/06/2026", tax_rate="0.1025", reference="LCW-0306", date_any_of=["2026-03-06", "2026-06-03"],
          expect_review=True, expect_flags=["date"]),

        # --- reconciliation discrepancies (planted wrong totals and lines) --------------------------
        S("r037", ["planted_wrong_total", "discrepancy"], "Old Town Pharmacy", "3 Market Sq, Kraków", "EUR",
          [I("Ibuprofen 200mg", "1", "4.50"), I("Throat lozenges", "2", "3.20")],
          "2026-05-08", "08.05.2026", tax_rate="0.08", total_override="18.90", reference="OTP-5508", expect_review=True,
          expect_flags=["total"]),
        S("r038", ["planted_wrong_total", "discrepancy", "usd"], "Westside Tire & Auto", "2500 Grand Ave, Des Moines IA", "USD",
          [I("Oil change", "1", "49.99"), I("Wiper blades", "2", "14.50"), I("Shop supplies", "1", "6.00")],
          "2026-09-03", "09/03/2026", tax_rate="0.07", total_override="96.32", layout="invoice", reference="WTA-90311",
          date_any_of=["2026-09-03", "2026-03-09"], expect_review=True, expect_flags=["total", "date"]),
        S("r039", ["planted_wrong_total", "discrepancy", "inr"], "Green Leaf Supermart", "HSR Layout, Bengaluru", "INR",
          [I("Basmati rice 5kg", "1", "649.00"), I("Toor dal 1kg", "2", "152.00"), I("Sunflower oil 1L", "1", "189.00")],
          "2026-06-30", "30/06/2026", tax_rate="0.05", split_tax=True, total_override="1250.00", gstin="29GGGGG1314R9Z6",
          reference="GLS/0630/77", expect_review=True, expect_flags=["total"]),
        S("r040", ["line_mismatch", "discrepancy"], "Ferry Terminal Café", "Pier 3, Wellington", "USD",
          [I("Flat white", "2", "4.80", total="8.60"), I("Muffin", "1", "4.20")],
          "2026-02-20", "2026-02-20", tax_rate=None, reference="FTC-2202", expect_review=True, expect_flags=["subtotal"]),

        # --- refunds and other conventions ----------------------------------------------------------
        S("r041", ["refund", "negative_total"], "Northline Hardware", "455 King St W, Toronto ON", "CAD",
          [I("RETURN Paint roller", "-1", "11.99")],
          "2026-09-14", "Sep 14, 2026", tax_rate="0.13", tax_label="HST", reference="NH-RF-2207", expect_review=True, expect_flags=["total"]),
        S("r042", ["discount", "service_charge", "tip"], "Sakura Sushi Bar", "410 Mission St, San Francisco CA", "USD",
          [I("Salmon nigiri", "2", "7.50"), I("Dragon roll", "1", "16.00"), I("Miso soup", "2", "4.00")],
          "2026-07-02", "Jul 2, 2026", tax_rate="0.08625", discount="5.00", service_rate="0.05", tip="6.00", reference="SSB-7021"),

        # --- not a receipt ---------------------------------------------------------------------------
        S("r043", ["not_receipt"], None, "", "USD", [], None, None, not_receipt=True, expect_review=True, expect_flags=["merchant", "total"]),

        # --- near-miss negatives for duplicate detection (same merchant/date or same total, but different) --
        S("r044", ["near_duplicate_negative"], "Blue Oak Cafe", "12 Harbour St, Portland OR", "USD",
          [I("Flat white", "1", "4.50"), I("Almond croissant", "1", "3.75")],
          "2026-03-14", "2026-03-14", reference="A19-55902"),
        S("r045", ["near_duplicate_negative"], "Kiosk Zentrum", "Hauptstraße 2, Leipzig", "EUR",
          [I("Zeitung", "1", "2.50"), I("Kaugummi", "2", "1.20"), I("Mineralwasser", "1", "1.80")],
          "2026-08-20", "20.08.2026", tax_rate="0.19", tax_label="MwSt", tax_included=True, reference="KZ-1102"),
        S("r046", ["near_duplicate_negative", "usd"], "Harbor Laundromat", "5 Dock St, Seattle WA", "USD",
          [I("Wash large", "2", "4.75"), I("Dryer 30 min", "2", "2.50"), I("Detergent", "1", "1.50")],
          "2026-02-25", "2026-02-25", tax_rate=None, reference="HL-2297"),
        S("r047", ["clear", "usd", "many_items"], "Parkside Market", "19 Park Ave, Hoboken NJ", "USD",
          [I("Eggs dozen", "1", "4.49"), I("Spinach", "2", "2.99"), I("Greek yogurt", "3", "1.25"), I("Chicken thighs", "1.84", "4.99"),
           I("Pasta", "2", "1.79"), I("Tomato sauce", "1", "3.49"), I("Parmesan", "1", "6.99"), I("Apples", "2.10", "1.99")],
          "2026-10-11", "10/11/2026", tax_rate=None, reference="PM-101147", date_any_of=["2026-10-11", "2026-11-10"], expect_flags=["date"]),
        S("r048", ["clear", "gbp", "invoice"], "Thistle Web Studio", "4 Rose St, Edinburgh EH2", "GBP",
          [I("Website maintenance (Sept)", "1", "250.00"), I("Hosting renewal", "1", "84.00")],
          "2026-10-01", "1 October 2026", tax_rate="0.20", tax_label="VAT", layout="invoice", reference="TWS-INV-0193", payment="Bank transfer"),
        S("r049", ["clear", "sgd", "taxi"], "CityCab", "Singapore", "SGD",
          [I("Fare", "1", "18.40"), I("ERP charge", "1", "2.00"), I("Booking fee", "1", "3.30")],
          "2026-09-30", "30-09-2026", tax_rate=None, payment="NETS", reference="SHA7716C"),
        S("r050", ["clear", "jpy", "convenience"], "Family Mart Shinjuku", "Shinjuku 3-14, Tokyo", "JPY",
          [I("Onigiri tuna", "2", "160"), I("Green tea 500ml", "1", "150"), I("Umbrella", "1", "700")],
          "2026-06-26", "2026年06月26日", tax_rate="0.08", tax_included=True, tax_label="Tax", payment="Suica", reference="FM-0626-9"),
    ]
    by_id = {s.id: s for s in out}
    # --- exact duplicates (identical bytes) and probable duplicates (re-scans of the same receipt) ---
    import copy
    for new_id, source in (("d051", "r001"), ("d052", "r004"), ("d053", "r013")):
        dup = copy.deepcopy(by_id[source])
        dup.id, dup.tags, dup.duplicate_of, dup.duplicate_kind = new_id, ["exact_duplicate"], source, "exact"
        out.append(dup)
    for new_id, source, ops in (("d054", "r002", ["rotate", "shadow"]), ("d055", "r009", ["blur"]), ("d056", "r042", ["low_contrast", "rotate"])):
        dup = copy.deepcopy(by_id[source])
        dup.id, dup.tags, dup.duplicate_of, dup.duplicate_kind = new_id, ["probable_duplicate", "rescan"], source, "probable"
        dup.degrade, dup.seed, dup.expect_review = ops, 99, True
        out.append(dup)
    return out


def apply_ambiguous_date_rule(spec: Spec) -> None:
    """Dataset rule: a numeric date whose first two parts are both <= 12 and differ is ambiguous. Both readings
    are accepted for accuracy, and the system is expected to flag the date. Applied mechanically to every spec."""
    import re as _re
    match = _re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b", spec.date_printed or "")
    if not match or not spec.date_iso:
        return
    a, b = int(match.group(1)), int(match.group(2))
    if a <= 12 and b <= 12 and a != b:
        y, m, d = spec.date_iso.split("-")
        spec.date_any_of = sorted({spec.date_iso, f"{y}-{d}-{m}"})
        if "date" not in spec.expect_flags:
            spec.expect_flags = [*spec.expect_flags, "date"]


def main() -> None:
    FILES.mkdir(parents=True, exist_ok=True)
    rendered: dict[str, bytes] = {}
    rows = []
    for spec in specs():
        rng = random.Random(f"{spec.id}:{spec.seed}")
        if spec.duplicate_kind == "exact":
            data = rendered[spec.duplicate_of]
        else:
            images = render_not_receipt(spec) if spec.not_receipt else render_text_receipt(spec, rng)
            data = encode(degrade(images, spec.degrade, rng, getattr(spec, "marks", {})), spec.fmt)
        rendered[spec.id] = data
        name = f"{spec.id}.{spec.fmt}"
        (FILES / name).write_bytes(data)
        apply_ambiguous_date_rule(spec)
        gt = None if spec.not_receipt else spec.ground_truth()
        hard = {"missing_date", "missing_merchant", "missing_total", "unreadable", "smudged_total", "ambiguous_date",
                "planted_wrong_total", "line_mismatch", "refund", "not_receipt", "probable_duplicate", "cropped", "ambiguous_symbol"}
        expect_review = spec.expect_review if spec.expect_review is not None else bool(hard & set(spec.tags)) or bool(spec.expect_flags)
        rows.append({
            "id": spec.id, "file": f"files/{name}", "tags": spec.tags, "pages": spec.pages,
            "ground_truth": gt,
            "expected": {
                "is_receipt": not spec.not_receipt,
                "reconciles": spec.reconciles(),
                "needs_review": expect_review,
                "flag_fields": sorted(set(spec.expect_flags)),
                "duplicate_of": spec.duplicate_of,
                "duplicate_kind": spec.duplicate_kind,
            },
        })
    with (OUT / "manifest.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (OUT / "README.md").write_text(DATASET_README.format(n=len(rows), as_of=AS_OF), encoding="utf-8")
    print(f"wrote {len(rows)} documents to {FILES}")


DATASET_README = """# Evaluation dataset

{n} synthetic receipt documents with ground truth, generated by `evaluation/generate_dataset.py`.

* **Every document is fictional.** Merchants, addresses, amounts and references are made up; each image says
  "SAMPLE DOCUMENT - FICTIONAL". No private receipts are included.
* `manifest.jsonl` holds one row per document: the file, tags, ground-truth fields (as printed and visible),
  and the expected validation/review outcome (`reconciles`, `needs_review`, `flag_fields`, duplicates).
* Ground truth is what is *visible*: a total that is cropped off or smudged is labelled `null`.
* Ambiguous dates (`04/05/26`, both parts <= 12) list every acceptable reading in `date_any_of`; the system is
  expected to flag them for confirmation rather than guess silently.
* Validation dates are evaluated as of {as_of} so date checks are reproducible.

Coverage: clear receipts, invoices and wide layouts; USD, EUR, GBP, INR (GST), JPY (0 decimals), KWD (3 decimals),
CAD, AUD, SGD, CHF, AED, MXN; 7 date formats; tax-inclusive VAT; discounts, service charges, tips; fractional
quantities and unit prices; blur, low contrast, rotation, noise, shadow, crop and smudge; missing merchant/date/
total/currency; ambiguous dates; planted wrong totals and line-item errors; a refund; a non-receipt; two-page
PDFs; exact duplicate files, re-scanned duplicates and near-miss non-duplicates.

Limitations: rendered text is cleaner than real thermal paper, and there is no handwriting. Results on this set
are an upper bound for printed receipts, not a measurement of real-world photo performance.
"""

if __name__ == "__main__":
    main()
