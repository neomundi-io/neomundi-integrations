<div align="center">

[![English](https://img.shields.io/badge/EN-English-6b7280?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-2563eb?style=flat-square)](README_FR.md)

# MLflow × NeoMundi

**Mesurer les appels de modèle que MLflow trace déjà, et retrouver chaque mesure sur la trace MLflow. Une fonction à côté de vos propres processeurs de spans, la latence que MLflow a enregistrée, et uniquement des comptes de jetons publiés par le fournisseur.**

**[Démarrage](#démarrage)** · **[Ce qu'il refuse de faire](#refus)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limites](LIMITATIONS.md)** · **[Coût](#ce-que-ça-coûte)**

</div>

---

> **Niveau de preuve : E1, hors ligne uniquement.** Aucune mesure réelle
> n'a été prise avec ce paquet. La fenêtre qui l'a construit n'avait pas
> de clé de vérification de service NeoMundi. La suite pilote le vrai
> `mlflow.openai.autolog()` autour du vrai client `openai`, à travers des
> transports simulés et une base sqlite jetable. Voir
> [LIMITATIONS.md §8](LIMITATIONS.md#8-evidence-level-e1-not-e2).

## Ce que c'est

`NeoMundiMlflowProcessor` enregistre **une fonction** auprès du point
d'extension public de MLflow, `mlflow.tracing.configure(span_processors=[...])`.
Elle est **ajoutée après** vos processeurs, et MLflow l'appelle pour
chaque span qu'il termine. Pour chaque appel de modèle écrit par
l'autolog `openai` de MLflow, elle prend trois choses :

- le compte de jetons, le `total_tokens` du fournisseur lui-même, que
  MLflow recopie dans `mlflow.chat.tokenUsage` ;
- la latence, le `end_time_ns - start_time_ns` que MLflow a enregistré
  sur le span ;
- le modèle servi.

Elle soumet ensuite à `POST /v1/govern` de NeoMundi via le SDK `neomundi`,
sur un thread de travail, pour que votre appel n'attende jamais. Puis elle
écrit le `request_id` et la `measurement_version` NeoMundi **sur la trace
MLflow**, sous forme d'étiquettes. Vous ouvrez la trace dans l'interface
MLflow et vous y trouvez l'observation qui l'a mesurée.

Tous les autres spans reçoivent aussi un résultat, avec un statut nommé.
Les spans agent, chaîne, workflow et tâche sont des agrégats. Outils,
retrievers et embeddings ne sont pas des appels de modèle. Un compte écrit
par une autre instrumentation n'est pas vérifié.

## Ce que ce n'est pas

- **Pas une mesure de tous les autologs MLflow.** MLflow range sous un même attribut les comptes de jetons de nombreux auteurs, et certains ne viennent pas du fournisseur. Nous avons mesuré trois cas où le fournisseur ne publiait **rien** : le traceur de l'Agents SDK écrit `0/0/0`, le logger MLflow de LiteLLM écrit sa propre estimation (`14/7/21`), et LangChain recopie ce que le modèle de chat a calculé. Seul l'autolog `openai` recopie l'usage du fournisseur tel quel et le laisse absent quand il n'y en a pas ([LIMITATIONS §1](LIMITATIONS.md#1-only-the-openai-autolog-is-measured--by-design)).
- **Pas un processeur OpenTelemetry.** Un processeur ajouté au tracer provider de MLflow est perdu sans un mot au prochain `mlflow.set_experiment()`. Ce paquet utilise donc le point d'extension public de MLflow lui-même ([§2](LIMITATIONS.md#2-staying-attached-what-removes-the-hook)).
- **Pas un lecteur de l'agrégat de trace.** Le total de trace de MLflow additionne des appels déjà mesurés un par un.
- **Pas un traceur, un évaluateur ni un vérificateur de vérité.** Chaque soumission est une mesure facturée. NeoMundi vérifie l'intégrité et l'authenticité, pas la véracité d'une réponse.

## Installation

Pas encore sur PyPI, donc `pip install mlflow-neomundi` ne fonctionne pas.
Installez depuis le dépôt :

```bash
pip install 'git+https://github.com/neomundi-io/neomundi-integrations.git#subdirectory=integrations/mlflow'
```

Python ≥ 3.11. Dépend de `mlflow-tracing>=3.18.0,<3.19`, le paquet léger
qu'un `mlflow` complet inclut déjà, et de `neomundi>=2.1.0,<3.0`. MLflow
a besoin d'un stockage pour tracer : avec `mlflow-tracing` seul et sans
URI de suivi, il n'enregistre aucun span ([§5](LIMITATIONS.md#5-mlflow-must-be-able-to-trace)).

## Démarrage

```python
import os

import mlflow
from mlflow_neomundi import NeoMundiMlflowProcessor

mlflow.set_tracking_uri(
    "http://localhost:5000"
)  # d'abord : une nouvelle URI réinitialise les hooks MLflow
mlflow.openai.autolog()

processor = NeoMundiMlflowProcessor(
    api_key=os.environ["NEOMUNDI_API_KEY"],
    base_url="https://api.neomundi.io",
).install()

reply = client.chat.completions.create(model="gpt-4o-mini", messages=[...])
processor.flush()  # les soumissions se font hors du chemin de l'appel

for o in processor.outcomes:
    print(o.span_type, o.status, o.token_count, o.latency_ms, o.request_id)
print(processor.installed)  # MLflow l'appelle-t-il encore ?
```

Gratuit d'abord : `NeoMundiMlflowProcessor(submit=False).install()` ne
demande aucune clé, n'ouvre aucune socket, et fait toutes les
vérifications. Il montre, sur votre propre trafic, quels appels seraient
mesurés, lesquels seraient refusés, et pourquoi.

## Refus

Chaque statut est une constante exportée par le paquet. Aucun refus ne
soumet quoi que ce soit.

| Statut | Quand |
|---|---|
| `not_submitted_unverified_token_count` | le span a été écrit par autre chose que l'autolog `openai` de MLflow : traceur de l'Agents SDK, LangChain, logger de LiteLLM, span écrit à la main |
| `not_submitted_no_token_count` | le fournisseur n'a publié aucun usage : l'attribut est absent et reste absent |
| `not_submitted_invalid_token_count` | usage sans `total_tokens` entier positif (partiel, nul, zéro) ; jamais reconstruit |
| `not_submitted_aggregate_span` | `AGENT`, `CHAIN`, `WORKFLOW`, `TASK` : ils contiennent des appels déjà mesurés |
| `not_submitted_not_a_model_call` | `TOOL`, `RETRIEVER`, `RERANKER`, `EMBEDDING`, `GUARDRAIL`, `PARSER`, `MEMORY`, `EVALUATOR` |
| `not_submitted_unsupported_span` | `UNKNOWN`, ou un type de span que cette version ne connaît pas |
| `not_submitted_application_error` | l'appel de modèle a échoué (statut de span `ERROR`) |
| `not_submitted_no_latency` | pas de début et de fin utilisables |
| `skipped_sampling` | exclu par `sample_rate` |
| `not_submitted_local_only` | `submit=False` |
| `not_submitted_transport_error` | l'API NeoMundi était injoignable ou a refusé ; votre appel n'est pas touché |
| `not_submitted_queue_full`, `not_submitted_processor_closed`, `not_submitted_internal_error` | le processeur a protégé votre application |

Les résultats ne contiennent jamais le prompt, la réponse ni une clé. Ils
ne portent que des comptes, des noms et des identifiants.
[`examples/02_refusals.py`](examples/02_refusals.py) montre chaque refus
sur un vrai span MLflow.

## Ce que ça coûte

🔴 **Chaque observation soumise est une mesure facturée** (0,030 CHF en
TIER_03). Une observation par appel de modèle mesuré. Les spans agrégats
ne sont jamais facturés, donc un run d'agent sous `mlflow.openai.autolog()`
n'est pas compté deux fois. `sample_rate` (par défaut `1.0`) est
déterministe dans l'identifiant de span. **Coupe-circuit :**
`processor.sample_rate = 0.0`, immédiat. Installer un second processeur
retire le premier, donc relancer une cellule de notebook ne facture pas
deux fois.

## État réel

| | |
|---|---|
| Suite de tests hors ligne | 75 tests, verts sous Python 3.12 / Linux en local ; la CI couvre 3.11–3.13 sous Linux, macOS, Windows |
| Exécution réelle | **aucune** : E1. 0 observation soumise, 0 relue. `qualify.py` est prêt, et son essai à blanc par le harnais est vert contre un point d'accès scripté en vrai HTTP. Il n'a pas été lancé contre Ollama |
| Maturité | expérimental |

Lisez [LIMITATIONS.md](LIMITATIONS.md) avant de tirer une conclusion d'un score.

## Licence

Apache-2.0. Voir [LICENSE](../../LICENSE).
