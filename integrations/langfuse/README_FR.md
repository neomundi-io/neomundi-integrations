<div align="center">

[![English](https://img.shields.io/badge/EN-English-6b7280?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-2563eb?style=flat-square)](README_FR.md)

# Langfuse × NeoMundi

**La mesure comportementale signée de NeoMundi, en scores sur la trace Langfuse que vous avez déjà ouverte.**

**[Démarrage](#démarrage)** · **[Ce qu'elle refuse](#refus)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limites](LIMITATIONS.md)** · **[Coût](#ce-que-cela-coûte)**

</div>

---

> **Niveau de preuve E2.** Deux mesures réelles le 2026-11-26, relues
> depuis l'API NeoMundi comme persistées, **et les six scores relus depuis
> le serveur Langfuse** sur les deux traces. Auparavant : aucune mesure
> réelle n'a été prise et aucun score n'a été relu depuis un serveur
> Langfuse. Voir [LIMITATIONS.md §6](LIMITATIONS.md#6-evidence-level-e1-not-e2).

## Ce que c'est

Un **pont**, pas un second observateur. Langfuse garde la trace ; NeoMundi
mesure l'interaction ; `NeoMundiLangfuseBridge` écrit les signaux de la
mesure sur cette trace sous forme de scores Langfuse, avec le `request_id`
NeoMundi dans les métadonnées de chaque score, pour passer de la trace à
l'enregistrement signé.

Il s'appuie sur les types de résultat du SDK `neomundi` et sur
`create_score` de Langfuse. Il ne réimplémente ni l'un ni l'autre, ne
mesure rien, et ne contacte jamais l'API NeoMundi.

## Ce que ce n'est pas

- **Pas un évaluateur.** Il transporte des mesures ; il ne les interprète pas.
- **Pas un traceur.** Langfuse trace. Ceci ajoute seulement des scores à ses traces.
- **Pas un vérificateur de vérité.** NeoMundi vérifie l'intégrité et l'authenticité, pas l'exactitude d'une réponse.

## Installation

```bash
pip install langfuse-neomundi
```

Python ≥ 3.11. Dépend de `langfuse>=5.2.0,<6.0` et `neomundi>=2.1.0,<3.0`.

## Démarrage

```python
bridge = NeoMundiLangfuseBridge(langfuse)  # votre client Langfuse existant
outcome = bridge.write(trace.close())  # un TraceRecord ou GovernResult neomundi
print(outcome.status, outcome.signals_written)
```

En contexte, dans une observation Langfuse :

```python
from langfuse import get_client
from neomundi import NeoMundiClient
from langfuse_neomundi import NeoMundiLangfuseBridge

langfuse = get_client()
neomundi = NeoMundiClient(api_key=..., base_url="https://api.neomundi.io")
bridge = NeoMundiLangfuseBridge(langfuse)

with langfuse.start_as_current_observation(name="answer"):
    with neomundi.trace(model_name="my-model") as trace:
        answer = call_my_llm(question)
        trace.record(prompt=question, response=answer, token_count=usage.total_tokens)
    bridge.write(trace.close())  # s'attache à la trace active
```

Ce qui arrive sur la trace (noms exacts, mesurés hors ligne) :

| Score | Type | Écrit quand |
|---|---|---|
| `neomundi.stability_score` | NUMERIC | le serveur l'a déclaré mesuré |
| `neomundi.coherence_score` | NUMERIC | idem |
| `neomundi.factual_hallucination_score` | NUMERIC | idem |
| `neomundi.semantic_instability_score` | NUMERIC | idem |
| `neomundi.semantic_risk` | NUMERIC | idem |
| `neomundi.measurement` | CATEGORICAL | toujours : `complete`, `partial`, `not_attempted`, `unknown`, `not_submitted`, `submitted_no_result` |

Les métadonnées de chaque score portent `request_id`, `measurement_version`
et la provenance de l'évaluateur. Le résumé liste ce qui a été retenu, et pourquoi.

**Avec `langchain-neomundi` :** passer `bridge.as_outcome_callback()` comme
`on_outcome` du handler. Seul le résumé est alors écrit — voir
[LIMITATIONS.md §3](LIMITATIONS.md#3-through-langchain-neomundi-only-the-summary-is-written).

## Refus

| Situation | Statut | Ce qui arrive dans Langfuse |
|---|---|---|
| Un signal non déclaré mesuré (même avec une valeur de convention) | `queued` | aucun score pour lui ; listé dans le résumé |
| Pas de `trace_id` et pas de trace Langfuse active | `not_written_no_trace` | rien |
| La mesure n'a pas été soumise | `queued` | le résumé seul, `not_submitted` |
| L'entrée n'est pas une mesure NeoMundi | `not_written_unsupported_input` | rien |
| `dry_run=True` | `dry_run` | rien ; les scores sont rendus sur l'issue |
| Le pont ou le client Langfuse lève une exception | `not_written_internal_error` | rien ; le type d'exception, jamais son message |

🔴 **`queued` signifie remis à Langfuse, pas livré.** Langfuse abandonne
les scores sur une erreur d'API sans lever d'exception. Voir
[LIMITATIONS.md §1](LIMITATIONS.md#1-queued-is-not-a-delivery-receipt).

## Ce que cela coûte

Le pont **ne facture rien** : il ne contacte jamais NeoMundi. La mesure
qu'il transporte a été facturée à sa soumission — 0,030 CHF par
observation — donc le contrôle du coût est là où vous mesurez
(`sample_rate` de `langchain-neomundi`, ou votre propre appel à
`client.trace()`).

`dry_run=True` calcule chaque score et ne remet rien à Langfuse : on peut
brancher le pont et lire ce qu'il écrirait avant de l'activer.

## État réel, sans enjolivure

| | |
|---|---|
| Suite hors ligne | 33 tests, plus 2 tests de composition exécutés quand `langchain-neomundi` est installé |
| Exécution réelle | **E2** — 2 observations, 2 relues comme persistées, 6/6 scores relus depuis Langfuse, 0,06 CHF (2026-11-26) |
| Maturité | beta |

Lire [LIMITATIONS.md](LIMITATIONS.md) avant de conclure quoi que ce soit d'un score.

## Exécuter les tests

```bash
cd integrations/langfuse
python -m pip install -e ".[dev]"
pytest -q
```

Hors ligne, sans clé, sans coût. Les quatre tests de panne Langfuse
prennent quelques secondes chacun, parce que Langfuse réessaie vraiment.

## Licence

Apache-2.0. Voir [LICENSE](../../LICENSE).
