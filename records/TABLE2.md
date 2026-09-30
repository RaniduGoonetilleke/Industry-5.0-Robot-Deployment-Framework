*Table 2. Outcomes of the offline recommendation rule (repository code and examples), in order of precedence. Every in-scope candidate is
evaluated first, so each outcome reports every candidate's failed and unresolved checks.*

| Outcome | Condition |
|---|---|
| REFUSE | a shared work-area hazard; or shared safety PASS and a nonempty in-scope set whose candidates all fail a check |
| WITHHOLD | the shared safety state is unresolved; no record is in scope; or no candidate is ready or waiting while at least one is unresolved |
| WAIT | no candidate is ready, and at least one fully supported candidate is on a busy robot |
| ASK | several candidates remain after any complete declared order, or a choice is stale or names an excluded candidate |
| CONFIRM | one candidate remains, it requires confirmation, and no matching confirmation was given |
| SELECT | one candidate remains, confirmed against the current digest or not requiring confirmation; an offline recommendation, not release authority |

