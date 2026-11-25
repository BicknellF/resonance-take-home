### Cumulative: adding one part at a time

| Agent | Exact match | Change | Excl. generic D99s | Denials caught | Paid claims wrongly denied |
|---|---:|---:|---:|---:|---:|
| **Predict every claim paid**<br><sub>baseline, no rules</sub> | 71.3% |  | 72.5% | 0.0% | 0 / 2,731 |
| **+ Payer manual rules, stateless**<br><sub>D01 EVV, D03 filing, D04 modifier, D07/D08 auth, D09 units vs minutes</sub> | 89.3% | +18.0 pts | 90.8% | 78.6% | 67 / 2,731 |
| **+ Payer bulletins**<br><sub>Northwind 45-day limit from Aug, Harbor weekend U3 from Sep</sub> | 91.5% | +2.1 pts | 92.9% | 77.1% | 0 / 2,731 |
| **+ Summit amendment 3 (from denial remarks)**<br><sub>D10: whole hours from July</sub> | 92.1% | +0.6 pts | 93.6% | 79.9% | 0 / 2,731 |
| **+ Stateful ledger, history claims only**<br><sub>D02 auth units, D05 duplicates, D06 Harbor daily cap</sub> | 95.6% | +3.6 pts | 97.2% | 93.5% | 0 / 2,731 |
| **+ One timeline with the to-check claims**<br><sub>to-check claims draw on the same auths</sub> | 95.8% | +0.2 pts | 97.4% | 94.4% | 1 / 2,731 |
| **+ Payer precedence learned from history**<br><sub>= the full agent</sub> | 98.1% | +2.2 pts | 99.7% | 94.4% | 1 / 2,731 |

### Leave one out: the full agent with one part switched off

| Agent | Exact match | Change | Excl. generic D99s | Denials caught | Paid claims wrongly denied |
|---|---:|---:|---:|---:|---:|
| **Full agent** | 98.1% |  | 99.7% | 94.4% | 1 / 2,731 |
| **Without dropping the 32 duplicate history rows** | 97.2% (3,862 rows) | -0.8 pts | 98.8% | 94.5% | 34 / 2,758 |
| **Without Northwind 8-minute rule (whole units instead)** | 83.2% | -14.8 pts | 84.6% | 95.7% | 439 / 2,731 |
| **Without payer bulletins** | 96.0% | -2.1 pts | 97.5% | 94.4% | 68 / 2,731 |
| **Without Summit amendment 3** | 97.2% | -0.9 pts | 98.7% | 91.8% | 1 / 2,731 |
| **Without to-check claims in the timeline** | 97.8% | -0.3 pts | 99.4% | 93.5% | 0 / 2,731 |
| **Without stateful ledger (D02, D05, D06)** | 93.6% | -4.4 pts | 95.1% | 79.9% | 0 / 2,731 |
| **Without learned precedence (D01, D02, ... order)** | 95.8% | -2.2 pts | 97.4% | 94.4% | 1 / 2,731 |
