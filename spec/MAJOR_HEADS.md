# FinanceOS — Major Heads Reference

**Version:** 1.0.0-draft
**Status:** Draft
**Component:** Registries

---

## Purpose [INFORMATIVE]

This document describes the structure of the CGA Major Head coding system
that FinanceOS uses as the third dimension of its canonical cell. It is a
reference for driver authors mapping raw budget line items to canonical
codes.

**This document does NOT contain the canonical Major Head list.** That list
lives in `financeos/drivers/registries/major_heads.json` and is the only
source of truth used by the conformance gate.

---

## Source [NORMATIVE]

The canonical Major Head and Minor Head codes are defined by the
**Controller General of Accounts (CGA), Ministry of Finance, Government of
India** in the *List of Major and Minor Heads of Account of the Union and
States* (commonly abbreviated **LMMHA**).

The LMMHA is updated periodically by CGA via correction slips. The version
of LMMHA that `major_heads.json` is built from MUST be recorded in the
registry file's metadata header (`lmmha_version`, `lmmha_published_at`).

---

## Code Structure [INFORMATIVE]

A Major Head is a 4-digit code. The first digit denotes the section of the
Government Account:

| Range | Section | Account |
|---|---|---|
| `0020`–`1606` | Receipt Heads | Revenue Account |
| `2011`–`3606` | Expenditure Heads | Revenue Account |
| `4000` | Capital Receipt | Capital Account |
| `4046`–`5475` | Expenditure Heads | Capital Account |
| `6001`–`6004` | Public Debt | Capital Account |
| `6075`–`7475` | Loans and Advances | Capital Account |
| `7610`–`7615` | Inter-State Settlement | Capital Account |
| `7999` | Appropriation to Contingency Fund | Capital Account |
| `8000` series | Contingency Fund / Public Account | Public Account |

**Convention:** the last three digits of an Expenditure Major Head match
the last three digits of the corresponding Receipt Major Head where the
two are paired. Example:

| Code | Description |
|---|---|
| `0210` | Medical and Public Health (Receipts) |
| `2210` | Medical and Public Health (Revenue Expenditure) |
| `4210` | Medical and Public Health (Capital Expenditure) |

---

## Selected Major Heads [INFORMATIVE]

A non-exhaustive sample of Major Heads frequently used in state budget
analysis. The full list lives in the registry JSON.

### Tax Revenue (Receipts)

| Code | Description |
|---|---|
| `0020` | Corporation Tax (Union) |
| `0021` | Taxes on Income other than Corporation Tax (Union) |
| `0028` | Other Taxes on Income and Expenditure |
| `0029` | Land Revenue |
| `0030` | Stamps and Registration Fees |
| `0039` | State Excise |
| `0040` | Taxes on Sales, Trade, etc. (and SGST under GST regime) |
| `0041` | Taxes on Vehicles |
| `0042` | Taxes on Goods and Passengers |
| `0045` | Other Taxes and Duties on Commodities and Services |

### Non-Tax Revenue (Receipts)

| Code | Description |
|---|---|
| `0049` | Interest Receipts |
| `0050` | Dividends and Profits |
| `0070` | Other Administrative Services |
| `0075` | Miscellaneous General Services |

### Grants from the Centre

| Code | Description |
|---|---|
| `1601` | Grants-in-aid from Central Government |

### Revenue Expenditure (selected)

| Code | Description |
|---|---|
| `2011` | Parliament/State/UT Legislatures |
| `2014` | Administration of Justice |
| `2015` | Elections |
| `2049` | Interest Payments |
| `2055` | Police |
| `2071` | Pensions and Other Retirement Benefits |
| `2202` | General Education |
| `2210` | Medical and Public Health |
| `2211` | Family Welfare |
| `2215` | Water Supply and Sanitation |
| `2216` | Housing |
| `2217` | Urban Development |
| `2235` | Social Security and Welfare |
| `2245` | Relief on account of Natural Calamities |
| `2401` | Crop Husbandry |
| `2415` | Agricultural Research and Education |
| `2702` | Minor Irrigation |
| `2705` | Command Area Development |
| `2801` | Power |
| `2851` | Village and Small Industries |
| `2852` | Industries |
| `3054` | Roads and Bridges |
| `3055` | Road Transport |
| `3604` | Compensation and Assignments to Local Bodies and PRIs |

### Capital Expenditure (selected)

| Code | Description |
|---|---|
| `4202` | Capital Outlay on Education, Sports, Art and Culture |
| `4210` | Capital Outlay on Medical and Public Health |
| `4215` | Capital Outlay on Water Supply and Sanitation |
| `4217` | Capital Outlay on Urban Development |
| `4401` | Capital Outlay on Crop Husbandry |
| `4700` | Capital Outlay on Major Irrigation |
| `4701` | Capital Outlay on Medium Irrigation |
| `4702` | Capital Outlay on Minor Irrigation |
| `4801` | Capital Outlay on Power Projects |
| `5054` | Capital Outlay on Roads and Bridges |
| `5055` | Capital Outlay on Road Transport |

### Public Debt

| Code | Description |
|---|---|
| `6003` | Internal Debt of the State Government |
| `6004` | Loans and Advances from the Central Government |

---

## Mapping to Functional Categories [INFORMATIVE]

For cross-state comparison, FinanceOS rolls Major Heads up to ~15
functional categories. The mapping is defined alongside the registry in
`financeos/drivers/registries/functional_categories.json`. A non-normative
sketch:

| Functional category | Example Major Heads |
|---|---|
| `health` | 2210, 2211, 4210 |
| `education` | 2202, 4202 |
| `social_welfare` | 2235, 2245 |
| `roads_and_transport` | 3054, 3055, 5054, 5055 |
| `irrigation` | 2702, 2705, 4700, 4701, 4702 |
| `power_subsidy` | 2801 (revenue exp portion) |
| `agriculture` | 2401, 2415, 4401 |
| `urban_development` | 2217, 4217 |
| `pensions` | 2071 |
| `interest_payments` | 2049 |
| `general_admin` | 2011, 2014, 2015, 2055 |
| `own_tax_revenue` | 0029, 0030, 0039, 0040, 0041, 0042, 0045 |
| `central_transfers` | 1601, plus shared-tax components from 0020/0021/0028 |
| `non_tax_revenue` | 0049, 0050, 0070, 0075 |
| `debt_service` | 2049 (revenue), 6003, 6004 (capital) |

This mapping is *opinionated* — definitions of "subsidy" or "social
welfare" vary by analyst. The mapping JSON MUST cite a source for each
grouping.
