# agent_data/ — investor operating data (local, mostly gitignored)

This directory holds the investor profile, tax facts, decision log and the
agent's working files, per the operating instructions (§1.1, §1.6, §13,
Appendix A/C).

| Path | Tracked? | Purpose |
|---|---|---|
| `investor_profile.example.yaml` | yes | Template (Appendix A). Copy to `investor_profile.yaml` and fill in. |
| `tax_facts.example.yaml` | yes | Template (§9.5). Copy to `tax_facts.yaml`. |
| `base_rates.example.md` | yes | Template (§7.4). Copy to `base_rates.md`. |
| `investor_profile.yaml` | **no** | Your real profile. Never committed. |
| `tax_facts.yaml` | **no** | Your verified tax facts. Never committed. |
| `base_rates.md` | **no** | Your base rates with sources. Never committed. |
| `decision_log/` | **no** (except `.gitkeep`) | Append-only decision records (Appendix C). Use `scripts/decision_log.py`. |
| `scratch/` | **no** (except `.gitkeep`) | Working files: state, raw inputs, computed outputs. |
| `charts/` | **no** (except `.gitkeep`) | Rendered chart pack (B6). |

Without a real profile, suitability conclusions and sizing are provisional.
