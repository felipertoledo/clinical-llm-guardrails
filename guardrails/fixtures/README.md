# Fixtures

Both files are in Portuguese because the linter reads Portuguese documents. English glosses below.

## real_false_positives.md

Sentences that an early version of the linter blocked by mistake. Each one contains a word that is also a doubt marker ("confirmar", "conferir", "a definir", a blank field, "Dr."), used in a way that is not a marker. The linter must return exit code 0 on this file.

| Line | Gloss | Why it must pass |
|---|---|---|
| 1 | Fill in the diaries every day, even on good days. | Instruction to the patient |
| 2 | Every day (maintenance): 1 puff in the morning and 1 at night. | Parenthesis that is not a marker |
| 3 | It is a working hypothesis, which we will confirm or adjust together. | "confirmar" as a verb in prose |
| 4 | No weekly change happens without the physician confirming. | Same |
| 5 | The blood pressure monitor you used (so I can check it is calibrated). | "conferir" as a verb in prose |
| 6 | To the colleague: Dr. Carlos, general practitioner. | "Dr." before a colleague's name is allowed; only the author's own name is checked |
| 7–9 | Day 6 `___/___`; Took it every day? ☐ yes ☐ forgot; What happened / What I learned | Diary fields the patient fills in: warning, not a block |
| 10 | Signature line | Intentional blank |
| 11 | Canonical signature | Required form |
| 12 | This assessment helps to understand and to define the best path of care. | "a definir" inside a verb phrase |
| 13 | We will define it together. | Same |

## provenance_markers.md

A referral where each statement carries where it came from: `[relato do filho]` (reported by the son), `[laudo]` (from a report), `[observado em consulta, hoje]` (observed today). Square brackets are normally unfilled placeholders and block; provenance markers are the exception and must pass. "não esclarecido" (not established) states a fact about the history and is not a doubt marker.
