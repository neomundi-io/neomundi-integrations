<div align="center">

[![English](https://img.shields.io/badge/EN-English-6b7280?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-2563eb?style=flat-square)](README_FR.md)

# OpenAI Agents SDK × NeoMundi

**Mesurer chaque appel de modèle que fait la boucle de votre agent, y compris celui qui décide d'un outil et celui qui lit son résultat. Un processeur de traces à côté de ceux que vous avez déjà, la latence que le SDK a enregistrée, et uniquement des comptes de jetons publiés par le fournisseur.**

**[Démarrage](#démarrage)** · **[Ce qu'il refuse de faire](#refus)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limites](LIMITATIONS.md)** · **[Coût](#ce-que-ça-coûte)**

</div>

---

> **Niveau de preuve : E1, hors ligne uniquement.** Aucune mesure réelle
> n'a été prise avec ce paquet : la fenêtre qui l'a construit n'avait pas
> de clé de vérification de service NeoMundi. La suite pilote un vrai
> `Agent`, le vrai `Runner` et les vraies classes de modèle du SDK, à
> travers des transports simulés. Voir
> [LIMITATIONS.md §9](LIMITATIONS.md#9-evidence-level-e1-not-e2).

## Ce que c'est

`NeoMundiTraceProcessor` est un `TracingProcessor` de l'Agents SDK. Vous
l'installez une fois et il **s'ajoute** aux processeurs déjà enregistrés.
Pour chaque appel de modèle sur l'API Responses (la classe de modèle par
défaut du SDK), il prend trois choses. Le compte de jetons vient de
l'objet `Response` du fournisseur. La latence est `ended_at - started_at`
du span, telle que le SDK l'a enregistrée. L'agent et les outils viennent
de la boucle. Il soumet ensuite à `POST /v1/govern` via le SDK `neomundi`,
sur un thread dédié : l'agent ne l'attend jamais.

Une exécution qui utilise des outils fait un appel de modèle par étape, et
chacun est mesuré séparément. Les exécutions d'outils, les étapes d'agent,
les tours et le total de l'exécution sont signalés par leur nom et **pas**
mesurés : ce ne sont pas des appels de modèle, ou ce sont des sommes
d'appels déjà mesurés.

## Ce que ce n'est pas

- **Pas un moyen de mesurer les agents Chat Completions ou LiteLLM.** Leurs spans portent un dictionnaire d'usage reconstruit par le SDK. Il est rempli de zéros quand le fournisseur se tait, et sur la route LiteLLM nous l'avons mesuré portant une estimation locale (21/7/28) là où le fournisseur n'a rien publié. Ces spans sont refusés ([LIMITATIONS §1](LIMITATIONS.md#1-chat-completions-and-litellm-spans-are-refused--by-design)).
- **Pas un lecteur de traces exportées.** `export()` abandonne la `Response` du fournisseur. La mesure n'est possible qu'en processus, depuis un processeur.
- **Pas une mesure des outils.** L'exécution d'un outil n'a ni modèle ni compte de jetons : elle est nommée sur le résultat, jamais facturée.
- **Pas un traceur, un évaluateur ni un vérificateur de vérité.** Chaque soumission est une mesure facturée. NeoMundi vérifie l'intégrité et l'authenticité, pas la véracité d'une réponse.

## Installation

Pas encore sur PyPI : `pip install openai-agents-neomundi` ne fonctionne
pas. Installation depuis le dépôt :

```bash
pip install 'git+https://github.com/neomundi-io/neomundi-integrations.git#subdirectory=integrations/openai-agents'
```

Python ≥ 3.11. Dépend de `openai-agents>=0.26.0,<0.27` (épinglé à la
version mineure dont la surface de traçage a été vérifiée) et de
`neomundi>=2.1.0,<3.0`.

## Démarrage

```python
from openai_agents_neomundi import NeoMundiTraceProcessor

processor = NeoMundiTraceProcessor(
    api_key=..., base_url="https://api.neomundi.io"
).install()  # add_trace_processor -- JAMAIS set_trace_processors

result = await Runner.run(agent, "Quel temps fait-il à Paris ?")

processor.flush()  # les soumissions tournent hors du thread de l'agent
for outcome in processor.outcomes:
    print(outcome.span_type, outcome.status, outcome.token_count, outcome.request_id)
```

🔴 **N'utilisez pas `set_trace_processors([processor])`.** Cette fonction
*remplace* la liste. Elle retire en silence l'exportateur du SDK vers le
tableau de bord OpenAI, et tout processeur que votre application avait
enregistré.

D'abord gratuitement : `NeoMundiTraceProcessor(submit=False)` ne demande
aucune clé, n'ouvre aucun socket, et indique lesquels de vos appels
seraient mesurés. Exécutables, hors ligne :
[`examples/01_offline_agent.py`](examples/01_offline_agent.py) et le
[notebook](notebooks/quickstart.ipynb).

## Refus

Chaque span se termine par un `Outcome` au statut explicite. Rien n'est
soumis, et rien n'est facturé, quand :

| Statut | Pourquoi |
|---|---|
| `not_submitted_unverified_token_count` | un span de génération Chat Completions / LiteLLM ; le fournisseur n'a publié aucun usage ou un usage partiel ; ou `AnyLLMModel` est chargé, et il reconstruit les objets `Response` |
| `not_submitted_no_provider_response` | l'exécution n'enregistre pas les données sensibles (`trace_include_sensitive_data=False`) : le span ne porte aucun objet du fournisseur |
| `not_submitted_aggregate_span` | un span de tour ou de tâche : une somme d'appels déjà mesurés |
| `not_submitted_not_a_model_call` | une exécution d'outil, une étape d'agent, un transfert ou un garde-fou, nommé sur le résultat |
| `not_submitted_unsupported_span` | parole, transcription, ou type de span inconnu |
| `not_submitted_application_error` | l'appel de modèle a échoué, ou la réponse n'est pas complète |
| `not_submitted_no_latency` | le span n'a pas de début et de fin exploitables |
| `skipped_sampling` / `not_submitted_local_only` | exclu par `sample_rate` / `submit=False` |
| `not_submitted_queue_full` / `not_submitted_processor_closed` | abandonné plutôt que de bloquer l'agent / après `shutdown()` |
| `not_submitted_transport_error` | l'API était injoignable, et votre exécution n'a pas été affectée |

Aucune exception de ce paquet n'atteint votre agent. Les résultats ne
portent ni prompt, ni réponse, ni entrée ou sortie d'outil, ni clé. Ils ne
portent que des longueurs, des noms et des identifiants.
[`examples/02_refusals.py`](examples/02_refusals.py) montre chaque refus
sur une exécution réelle.

## Ce que ça coûte

🔴 **Chaque observation soumise est une mesure facturée** (0,030 CHF en
TIER_03). **Une exécution d'agent est facturée une fois par appel de
modèle** : une exécution qui appelle un outil fait deux appels et coûte
deux observations. `sample_rate` (par défaut `1.0`) est déterministe sur
l'identifiant du span. **Coupe-circuit :** `processor.sample_rate = 0.0`,
immédiat. Installer un second processeur retire le premier : relancer une
cellule de notebook ne facture pas deux fois.

## État réel

| | |
|---|---|
| Suite hors ligne | 45 tests, verts sous Python 3.12 / Linux en local ; la CI exécute 3.11–3.13 sous Linux, macOS, Windows |
| Exécution réelle | **aucune** : E1. 0 observation soumise, 0 relue. `qualify.py` est prêt et son essai à blanc est vert contre un fournisseur simulé. Il n'a pas été exécuté contre Ollama |
| Maturité | expérimentale |

Lisez [LIMITATIONS.md](LIMITATIONS.md) avant de tirer une conclusion d'un score.

## Licence

Apache-2.0. Voir [LICENSE](../../LICENSE).
