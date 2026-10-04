# Local verification checklist — what the cloud could not prove

Everything here is **implemented, unverified**. The user runs each entry locally against
the real databases; CLAUDE.md's rule applies — confirm with an *independent* check
(direct SQL, log inspection, a regenerated report), never the app's own success message.

Entry format (one per item, added by the lead when it accepts the item):

```
### P<n> — <title>
- Why cloud could not verify: <no database / no LLM endpoint / ...>
- Command: <exact command>
- Independent check: <SQL / file / report to compare>
- Expected evidence: <numbers from the report's Evidence section, or "any value > X because ...">
- Result (filled in locally): 
```

(no entries yet)
