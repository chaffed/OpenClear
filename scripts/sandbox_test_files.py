# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Synthetic test files for a sandbox (SDK_PLAN.md P4), built with Moov's own libraries:

- `issued_items.csv`: the positive pay (issued checks) file;
- `presented.x937`: those checks presented for payment, as an X9.37 image cash letter
  written by Moov imagecashletter (EBCDIC, with the check images as TIFF);
- `ach_debits.ach`: ACH debits against the sandbox's accounts, written by Moov ACH;
- `expected.csv`: what OpenClear should make of each item (matched, or which exception).

Checks come from CheckSim (https://github.com/... ../CheckSim: synthetic checks with a
ground-truth manifest, fictional banks, SPECIMEN micro-print) when `--checksim` points at
its output, or a small built-in set without images otherwise. A few issued checks are
left out of the issued file on purpose (not in file), and CheckSim's altered payees and
amounts stay altered in what's presented, so the exceptions are known in advance.

    docker run -d -p 18080:8080 moov/ach && docker run -d -p 18083:8083 moov/imagecashletter
    python scripts/sandbox_test_files.py --out ./sandbox-files --account 1000234561 [--checksim ../CheckSim/output]
    python scripts/sandbox_test_files.py --out ./sandbox-files --account 1000234561 --load   # into the sandbox, via the SDK

`--load` reads OPENCLEAR_URL, OPENCLEAR_CLIENT_ID and OPENCLEAR_CLIENT_SECRET."""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

SANDBOX_ROUTING = "231380104"  # the sandbox bank's routing number (fictional use)
ODFI_ROUTING = "091000019"


@dataclass
class Item:
    check_number: str
    amount_cents: int
    payee: str
    presented_cents: int
    presented_payee: str
    issue_date: date
    issued: bool
    front: bytes | None = None
    back: bytes | None = None


def _tiff(png_path: Path) -> bytes | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(png_path) as image:
        out = io.BytesIO()
        image.convert("1").save(out, format="TIFF", compression="group4")
        return out.getvalue()


def from_checksim(directory: Path, limit: int, rng: random.Random) -> list[Item]:
    items = []
    with (directory / "manifest.csv").open() as handle:
        for row in list(csv.DictReader(handle))[:limit]:
            cents = int(row["amount_cents"])
            altered_amount = row.get("fraud_type") in ("amount_mismatch", "amount_exceeds_typical")
            items.append(Item(
                check_number=row["check_number"], amount_cents=cents, payee=row["payee"],
                presented_cents=cents + (rng.randint(100, 50_000) if altered_amount else 0),
                presented_payee=row.get("payee_displayed") or row["payee"],
                issue_date=date.fromisoformat(row["check_date"]), issued=rng.random() > 0.1,
                front=_tiff(directory / row["front_image"]), back=_tiff(directory / row["back_image"]),
            ))
    return items


def _specimen(item_number: str, payee: str, cents: int, side: int) -> bytes | None:
    """A plain synthetic check image (front: payee, amount, number; back: endorsement line),
    marked SPECIMEN, as a bilevel TIFF."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    image = Image.new("L", (1200, 550), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 1194, 544), outline=0, width=3)
    if side == 0:
        draw.text((40, 40), "SANDBOX BANK (FICTIONAL)", fill=0)
        draw.text((1000, 40), f"No. {item_number}", fill=0)
        draw.text((40, 200), f"PAY TO THE ORDER OF  {payee}", fill=0)
        draw.text((900, 200), f"${cents / 100:,.2f}", fill=0)
        draw.text((40, 480), f"C{SANDBOX_ROUTING}A 1000234561C {item_number}", fill=0)
    else:
        draw.text((40, 60), "ENDORSE HERE", fill=0)
    draw.text((420, 300), "SPECIMEN - SYNTHETIC TEST ITEM", fill=0)
    out = io.BytesIO()
    image.convert("1").save(out, format="TIFF", compression="group4")
    return out.getvalue()


def built_in(count: int, rng: random.Random) -> list[Item]:
    payees = ["Northgate Office Supply", "Summit Roofing LLC", "Dana Reyes", "Cascade Landscaping", "Harbor View Consulting"]
    items = []
    for n in range(count):
        cents = rng.randint(2_000, 500_000)
        altered = n % 7 == 3
        items.append(Item(
            check_number=str(5001 + n), amount_cents=cents, payee=payees[n % len(payees)],
            presented_cents=cents + (12_345 if altered else 0), presented_payee=payees[n % len(payees)],
            issue_date=date.today() - timedelta(days=rng.randint(1, 30)), issued=n % 10 != 4,
            front=_specimen(str(5001 + n), payees[n % len(payees)], cents + (12_345 if altered else 0), 0),
            back=_specimen(str(5001 + n), "", 0, 1),
        ))
    return items


def expectation(item: Item) -> str:
    if not item.issued:
        return "not_in_file"
    if item.presented_cents != item.amount_cents:
        return "amount_mismatch"
    if item.presented_payee != item.payee:
        return "payee_mismatch (needs OCR of the image)"
    return "matched"


def x937_with_moov(items: list[Item], account: str, icl_url: str) -> bytes:
    today = datetime.now(timezone.utc)
    stamp = today.strftime("%Y-%m-%dT00:00:00Z")
    checks = []
    for sequence, item in enumerate(items, start=1):
        views, data = [], []
        for side, image in ((0, item.front), (1, item.back)):
            if not image:
                continue
            views.append({"imageIndicator": 1, "imageCreatorRoutingNumber": ODFI_ROUTING, "imageCreatorDate": stamp,
                          "imageViewFormatIndicator": "00", "imageViewCompressionAlgorithm": "00", "viewSideIndicator": side,
                          "viewDescriptor": "00", "digitalSignatureIndicator": 0, "imageRecreateIndicator": 0})
            data.append({"eceInstitutionRoutingNumber": ODFI_ROUTING, "bundleBusinessDate": stamp, "cycleNumber": "1",
                         "eceInstitutionItemSequenceNumber": f"{sequence:015d}", "clippingOrigin": 0,
                         "lengthImageReferenceKey": "0000", "lengthDigitalSignature": "00000",
                         "lengthImageData": f"{len(image):07d}", "imageData": base64.b64encode(image).decode()})
        checks.append({
            "auxiliaryOnUs": item.check_number, "payorBankRoutingNumber": SANDBOX_ROUTING[:8], "payorBankCheckDigit": SANDBOX_ROUTING[8],
            "onUs": f"{account}/", "itemAmount": item.presented_cents, "eceInstitutionItemSequenceNumber": f"{sequence:015d}",
            "documentationTypeIndicator": "G", "returnAcceptanceIndicator": "0", "micrValidIndicator": 1, "bofdIndicator": "Y",
            "addendumCount": 0, "correctionIndicator": 0, "archiveTypeIndicator": "B",
            "imageViewDetail": views, "imageViewData": data, "imageViewAnalysis": [],
        })
    header = {"collectionTypeIndicator": "01", "destinationRoutingNumber": SANDBOX_ROUTING, "eceInstitutionRoutingNumber": ODFI_ROUTING}
    total = sum(item.presented_cents for item in items)
    images = sum(len(check["imageViewData"]) for check in checks)
    records = 1 + 1 + 1 + sum(1 + 2 * len(c["imageViewData"]) for c in checks) + 1 + 1 + 1  # header, letter, bundle, items, controls
    document = {
        "fileHeader": {"standardLevel": "03", "testIndicator": "T", "immediateDestination": SANDBOX_ROUTING, "immediateOrigin": ODFI_ROUTING,
                       "fileCreationDate": stamp, "fileCreationTime": stamp, "resendIndicator": "N", "immediateDestinationName": "SANDBOX BANK",
                       "immediateOriginName": "SANDBOX ODFI", "fileIDModifier": "1", "companionDocumentIndicator": "1"},
        "cashLetters": [{
            "cashLetterHeader": {**header, "cashLetterBusinessDate": stamp, "cashLetterCreationDate": stamp, "cashLetterCreationTime": stamp,
                                 "recordTypeIndicator": "I" if images else "E", "documentationTypeIndicator": "G",
                                 "cashLetterID": "SBX00001", "fedWorkType": "C"},
            "bundles": [{"bundleHeader": {**header, "bundleBusinessDate": stamp, "bundleCreationDate": stamp, "bundleID": "SBX001",
                                          "bundleSequenceNumber": "0001", "cycleNumber": "1"}, "checks": checks,
                         "bundleControl": {"bundleitemsCount": len(checks), "bundleTotalAmount": total, "micrValidTotalAmount": total,
                                           "bundleImagesCount": images, "creditTotalIndicator": 0}}],
            "cashLetterControl": {"cashLetterBundleCount": 1, "cashLetterItemsCount": len(checks), "cashLetterTotalAmount": total,
                                  "cashLetterImagesCount": images, "eceInstitutionName": "SANDBOX ODFI",
                                  "settlementDate": (today + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"), "creditTotalIndicator": 0},
        }],
        "fileControl": {"cashLetterCount": 1, "totalRecordCount": records, "totalItemCount": len(checks), "fileTotalAmount": total,
                        "creditTotalIndicator": 0},
    }
    created = httpx.post(f"{icl_url}/files/create", json=document, timeout=60)
    if created.status_code >= 300 or created.json().get("error"):
        raise SystemExit(f"Moov imagecashletter refused the file: {created.text[:500]}")
    contents = httpx.get(f"{icl_url}/files/{created.json()['id']}/contents", timeout=60)
    contents.raise_for_status()
    return contents.content


def ach_with_moov(account: str, count: int, ach_url: str, rng: random.Random) -> tuple[bytes, list[dict]]:
    effective = (date.today() + timedelta(days=1)).strftime("%y%m%d")
    originators = [("ACME UTILITY", "1234567890"), ("UNKNOWN BILLER", "9988776655")]
    batches, expected = [], []
    for company, company_id in originators:
        entries = []
        for n in range(count):
            amount = rng.randint(1_000, 90_000)
            trace = f"{ODFI_ROUTING[:8]}{len(expected) + 1:07d}"
            entries.append({"transactionCode": 27, "RDFIIdentification": SANDBOX_ROUTING[:8], "checkDigit": SANDBOX_ROUTING[8],
                            "DFIAccountNumber": account, "amount": amount, "identificationNumber": f"SBX{n}",
                            "individualName": "Sandbox Operating", "traceNumber": trace})
            expected.append({"trace_number": trace, "originator": company, "amount_cents": amount,
                             "expected": "unauthorized_originator unless an ACH authorization covers it"})
        batches.append({"batchHeader": {"serviceClassCode": 225, "companyName": company, "companyIdentification": company_id,
                                        "standardEntryClassCode": "PPD", "companyEntryDescription": "SANDBOX", "effectiveEntryDate": effective,
                                        "ODFIIdentification": ODFI_ROUTING[:8]}, "entryDetails": entries})
    document = {"fileHeader": {"immediateOrigin": ODFI_ROUTING, "immediateOriginName": "SANDBOX ODFI", "immediateDestination": SANDBOX_ROUTING,
                               "immediateDestinationName": "SANDBOX BANK", "fileCreationDate": date.today().strftime("%y%m%d"),
                               "fileCreationTime": "1200", "fileIDModifier": "A"}, "batches": batches}
    created = httpx.post(f"{ach_url}/files/create", json=document, timeout=60)
    if created.status_code >= 300 or created.json().get("error"):
        raise SystemExit(f"Moov ACH refused the file: {created.text[:500]}")
    return httpx.get(f"{ach_url}/files/{created.json()['id']}/contents", timeout=60).content, expected


def write(out: Path, account: str, items: list[Item], *, icl_url: str, ach_url: str, ach_count: int, rng: random.Random) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "issued_items.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["account_number", "check_number", "amount", "payee_name", "issue_date"])
        for item in items:
            if item.issued:
                writer.writerow([account, item.check_number, f"{item.amount_cents / 100:.2f}", item.payee, item.issue_date.isoformat()])
    (out / "presented.x937").write_bytes(x937_with_moov(items, account, icl_url))
    ach, ach_expected = ach_with_moov(account, ach_count, ach_url, rng)
    (out / "ach_debits.ach").write_bytes(ach)
    with (out / "expected.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["kind", "reference", "amount", "expected"])
        for item in items:
            writer.writerow(["check", item.check_number, f"{item.presented_cents / 100:.2f}", expectation(item)])
        for entry in ach_expected:
            writer.writerow(["ach", entry["trace_number"], f"{entry['amount_cents'] / 100:.2f}", entry["expected"]])
    (out / "README.txt").write_text(
        "Synthetic test files (scripts/sandbox_test_files.py). Load issued_items.csv first, then presented.x937 "
        "(check images, x937) and ach_debits.ach (ACH, nacha). expected.csv says what each item should become.\n"
    )


def load(out: Path) -> None:
    sdk = Path(__file__).resolve().parents[1] / "sdks" / "python" / "src"
    sys.path.insert(0, str(sdk))
    from openclear_sdk import OpenClear

    oc = OpenClear(os.environ["OPENCLEAR_URL"], client_id=os.environ["OPENCLEAR_CLIENT_ID"], client_secret=os.environ["OPENCLEAR_CLIENT_SECRET"])
    for kind, name, fmt in (("issued_items", "issued_items.csv", "tabular"), ("check_images", "presented.x937", "x937"),
                            ("ach_transactions", "ach_debits.ach", "nacha")):
        upload = oc.bulk.upload_file(kind, fmt, (name, (out / name).read_bytes()))
        print(f"{name}: {upload['succeeded']} imported, {upload['failed']} failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--account", required=True, help="The sandbox account the checks and debits are drawn on")
    parser.add_argument("--checksim", help="CheckSim output directory (manifest.csv and images)")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--ach-count", type=int, default=3, help="Debits per originator")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--moov-icl", default=os.environ.get("MOOV_ICL_URL", "http://localhost:18083"))
    parser.add_argument("--moov-ach", default=os.environ.get("MOOV_ACH_URL", "http://localhost:18080"))
    parser.add_argument("--load", action="store_true")
    args = parser.parse_args(argv)
    rng = random.Random(args.seed)
    items = from_checksim(Path(args.checksim), args.count, rng) if args.checksim else built_in(args.count, rng)
    out = Path(args.out)
    write(out, args.account, items, icl_url=args.moov_icl, ach_url=args.moov_ach, ach_count=args.ach_count, rng=rng)
    print(f"Wrote {out}: {sum(i.issued for i in items)} issued, {len(items)} presented checks, {2 * args.ach_count} ACH debits")
    if args.load:
        load(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
