# Back-test against claims history

- Claims: **3830**
- Exact match (status and denial code): **98.1%** (3756/3830)
- Excluding 61 generic D99 denials no rule can predict: **99.7%**

Rules were tuned on the whole history, so the period split below is a weak holdout:

- submitted before 2026-09-01: 98.0% (1867/1906)
- submitted from 2026-09-01: 98.2% (1889/1924)

## Confusion matrix (rows = actual, columns = predicted)

```
predicted  D01  D02  D03  D04  D05  D06  D07  D08  D09  D10  PAID
actual                                                           
D01        181    0    0    0    0    0    0    0    0    0     0
D02          0   65    0    0    0    0    0    0    0    0     0
D03          0    0  218    0    0    0    0    0    0    0     0
D04          0    0    0   37    0    0    0    0    0    0     0
D05          0    0    0    0   27    0    0    0    0    0     0
D06          0    0    0    0    0   79    0    0    0    0     0
D07          0    0    0    0    0    0   75    0    0    0     0
D08          0    0    0    0    0    0    0   70    0    0     0
D09          0    0    0    0    0    0    0    0  239    0     0
D10          0    0    0    0    0    0    0    0    0   35     0
D99          0    0    0    0    0    0    0    0    0   12    61
PAID         0    1    0    0    0    0    0    0    0    0  2730
```

## Misses (excluding generic D99s)

- C008100 (PY03): actual D99, predicted D10. Remark: Contract amendment 3: services must be billed in whole hours
- C018067 (PY03): actual D99, predicted D10. Remark: Units not billable per amendment 3 (hourly increments)
- C005597 (PY03): actual D99, predicted D10. Remark: Contract amendment 3: services must be billed in whole hours
- C011222 (PY03): actual D99, predicted D10. Remark: Contract amendment 3: services must be billed in whole hours
- C017583 (PY03): actual D99, predicted D10. Remark: Contract amendment 3: services must be billed in whole hours
- C003828 (PY03): actual D99, predicted D10. Remark: Units not billable per amendment 3 (hourly increments)
- C007278 (PY03): actual D99, predicted D10. Remark: Units not billable per amendment 3 (hourly increments)
- C001113 (PY03): actual D99, predicted D10. Remark: Contract amendment 3: services must be billed in whole hours
- C006198 (PY03): actual D99, predicted D10. Remark: Contract amendment 3: services must be billed in whole hours
- C002910 (PY03): actual D99, predicted D10. Remark: Contract amendment 3: services must be billed in whole hours
- C012372 (PY02): actual PAID, predicted D02. Remark: -
- C006366 (PY03): actual D99, predicted D10. Remark: Contract amendment 3: services must be billed in whole hours
- C013827 (PY03): actual D99, predicted D10. Remark: Units not billable per amendment 3 (hourly increments)

## Hidden outcomes (to-check claims history suggests were denied)

- C021301: history paid C012372 on AUTH11084, which only adds up if C021301 was denied (all rules pass, so the reason is unknown)
