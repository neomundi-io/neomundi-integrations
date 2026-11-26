<div align="center">

[![English](https://img.shields.io/badge/EN-English-6b7280?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-2563eb?style=flat-square)](README_FR.md)

# OpenTelemetry × NeoMundi

**Mesurer les appels de modèle que votre application trace déjà — un span processor à côté de vos exporteurs, la latence que votre trace a déjà observée, et le `request_id` NeoMundi réécrit dans la même trace.**

**[Démarrage](#démarrage)** · **[Ce qu'il refuse](#refus)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limites](LIMITATIONS.md)** · **[Coût](#ce-que-ça-coûte)**

</div>

---

> **Niveau de preuve : E1 — hors ligne uniquement.** Aucune mesure réelle
> n'a été prise avec ce paquet : la fenêtre qui l'a construit n'avait pas
> de clé de vérification du compte de service NeoMundi. La suite pilote le
> vrai client `openai`, la vraie instrumentation contrib openai-v2 et le
> vrai SDK OpenTelemetry à travers des transports simulés. Voir
> [LIMITATIONS.md §9](LIMITATIONS.md#9-evidence-level-e1-not-e2).

## Ce que c'est

`NeoMundiSpanProcessor` est un `SpanProcessor` OpenTelemetry. Vous
l'ajoutez au tracer provider que votre application utilise déjà. Autour
d'un appel de modèle à mesurer, vous ouvrez une interaction et déclarez le
nombre de tokens que votre fournisseur a renvoyé ; le processor retrouve le
span GenAI produit par votre instrumentation à l'intérieur, en prend la
latence, le modèle et l'identité de trace, et soumet à `POST /v1/govern`
via le SDK `neomundi`. Le `request_id` est ensuite posé sur un span
`neomundi.interaction` de la **même trace**, que votre exporteur envoie à
votre backend.

## Ce que ce n'est pas

- **Pas un moyen de mesurer des spans seuls.** Le `gen_ai.usage.*` d'un span est écrit par une instrumentation, et nous en avons mesuré une qui remplace le décompte du fournisseur par une estimation sans que le span change d'aspect. Le décompte d'un span n'est **jamais** soumis ([LIMITATIONS §1](LIMITATIONS.md#1-a-spans-token-count-is-never-measured--and-that-is-the-design)).
- **Pas sans retouche.** Un bloc `with` par appel mesuré, pour déclarer le décompte.
- **Pas une instrumentation.** Il lit le span GenAI de l'instrumentation que vous utilisez ; il n'en installe aucune.
- **Ni traceur, ni évaluateur, ni vérificateur de vérité.** Chaque soumission est une mesure facturée ; NeoMundi vérifie l'intégrité et l'authenticité, pas la véracité d'une réponse.

## Installation

Pas encore sur PyPI — `pip install otel-neomundi` ne fonctionne pas.
Installer depuis le dépôt :

```bash
pip install 'git+https://github.com/neomundi-io/neomundi-integrations.git#subdirectory=integrations/otel'
```

Python ≥ 3.11. Dépend de `opentelemetry-sdk>=1.45.2,<2.0` et
`neomundi>=2.1.0,<3.0`. Avec l'instrumentation contrib openai-v2, figez
`opentelemetry-util-genai>=1.1b0,<1.2` — la dernière version casse son
import ([LIMITATIONS §6](LIMITATIONS.md#6-the-latest-contrib-instrumentation-does-not-import-as-pip-installs-it)).

## Démarrage

```python
processor = NeoMundiSpanProcessor(
    api_key=..., base_url="https://api.neomundi.io", tracer_provider=tracer_provider
)
tracer_provider.add_span_processor(processor)  # à côté de vos exporteurs

with processor.interaction() as interaction:
    reply = client.chat.completions.create(model=..., messages=...)
    # Le décompte renvoyé par le FOURNISSEUR -- ou None s'il n'en a renvoyé aucun.
    interaction.declare(token_count=reply.usage.total_tokens if reply.usage else None)

print(interaction.outcome.status, interaction.outcome.request_id)
```

Gratuit d'abord : `NeoMundiSpanProcessor(submit=False, tracer_provider=...)`
ne demande aucune clé, n'ouvre aucune socket, et dit lesquels de vos
appels seraient mesurés. Exécutable hors ligne :
[`examples/01_offline_interaction.py`](examples/01_offline_interaction.py)
et le [notebook](notebooks/quickstart.ipynb).

## Refus

Chaque interaction se termine par un `Outcome` au statut explicite. Rien
n'est soumis, et rien n'est facturé, quand :

| Statut | Pourquoi |
|---|---|
| `not_submitted_no_token_count` | rien de déclaré — **même si le span porte `gen_ai.usage.*`** |
| `not_submitted_invalid_token_count` | le décompte déclaré n'est pas un entier positif |
| `not_submitted_no_genai_span` | aucun span GenAI dans l'interaction, ou processor sur un autre provider |
| `not_submitted_ambiguous_spans` | plusieurs spans GenAI : l'appel visé n'est pas deviné |
| `not_submitted_unsupported_operation` | ni chat, ni complétion, ni generate content, ou pas de nom d'opération |
| `not_submitted_application_error` | votre bloc a levé (relevé tel quel), ou le span est en `ERROR` |
| `not_submitted_no_latency` | le span n'a pas de début et de fin exploitables |
| `skipped_sampling` / `not_submitted_local_only` | exclu par `sample_rate` / `submit=False` |
| `not_submitted_transport_error` | API injoignable — votre appel n'a pas été affecté |

Aucune exception de ce paquet n'atteint votre application. Les outcomes ne
portent ni prompt, ni réponse, ni clé — seulement des longueurs et des
identifiants. [`examples/02_refusals.py`](examples/02_refusals.py) montre
chaque refus sur un vrai span.

## Ce que ça coûte

🔴 **Chaque observation soumise est une mesure facturée** (0,030 CHF en
TIER_03). Un span seul n'est jamais facturé ; seule une interaction avec un
décompte déclaré l'est. `sample_rate` (par défaut `1.0`) est déterministe
dans l'identifiant de trace. **Coupe-circuit :** `processor.sample_rate =
0.0`, immédiat.

## État, honnêtement

| | |
|---|---|
| Suite hors ligne | 48 tests, verts en Python 3.12 / Linux en local ; la CI couvre 3.11–3.13 sur Linux, macOS, Windows |
| Exécution réelle | **aucune** — E1. 0 observation soumise, 0 relue. `qualify.py` prêt ; dry run vert contre un endpoint scripté |
| Maturité | experimental |

Lire [LIMITATIONS.md](LIMITATIONS.md) avant de tirer une conclusion d'un score.

## Licence

Apache-2.0. Voir [LICENSE](../../LICENSE).
